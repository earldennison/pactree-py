"""Pure-Python Pactree extension discovery, lifecycle, and fork host."""

from __future__ import annotations

import contextvars
import importlib.metadata
import json
import logging
import shutil
import threading
import uuid
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .authorization import (
    ApplyAuthorization,
    AuthorizationAttestation,
    AuthorizationIssuer,
)
from .errors import (
    ExtensionActivationError,
    ExtensionAuthorizationError,
    ExtensionCapabilityError,
    ExtensionCompatibilityError,
    ExtensionConfigurationError,
    ExtensionForkError,
    ExtensionNotFoundError,
    ExtensionOrderError,
    ExtensionTransactionError,
)
from .models import (
    PACTREE_EXTENSION_API_VERSION,
    ContextRef,
    ExtensionContext,
    ExtensionDescriptor,
    PreparedFork,
    RuntimeSnapshot,
    SynchronizedRef,
)
from .services import (
    CancellationToken,
    CommitStream,
    DisabledProcessSupervisor,
    ExternalReferenceRegistry,
    HostPolicy,
    StateRoots,
    TransactionCoordinator,
    PreparedExtensionBatch,
    stable_token,
)


ENTRY_POINT_GROUP = "pactree.extensions"
_UNSET = object()
_runtime_scope: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "pactree_extension_scope",
    default=None,
)


@contextmanager
def _bind_runtime_scope(scope_id: str):
    """Internal runtime hook; applications do not attach agents manually."""

    token = _runtime_scope.set(scope_id)
    try:
        yield
    finally:
        _runtime_scope.reset(token)


@dataclass(frozen=True, slots=True)
class _RequestedExtension:
    name: str
    dependency: Any


@dataclass(frozen=True, slots=True)
class _SharedHostServices:
    path: Path
    branch_id: str
    state_roots: StateRoots
    commit_stream: CommitStream
    policy: HostPolicy
    process_supervisor: DisabledProcessSupervisor
    authorizations: AuthorizationIssuer
    transaction: TransactionCoordinator
    logger: logging.Logger


@dataclass(slots=True)
class _Binding:
    descriptor: ExtensionDescriptor
    dependency: Any
    context: ExtensionContext
    instance: Any
    surface: Any
    retired: bool = False
    closed: bool = False

    def retire(self) -> None:
        if self.retired:
            return
        self.retired = True
        retire = getattr(self.instance, "retire", None)
        if callable(retire):
            retire()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        first_error: BaseException | None = None
        try:
            self.retire()
        except BaseException as error:
            first_error = error
        closer = getattr(self.instance, "close", None)
        if callable(closer):
            try:
                closer()
            except BaseException as error:
                first_error = first_error or error
        if first_error is not None:
            raise first_error


class ContextSurface:
    def __init__(self, pact: "Pact"):
        self._pact = pact

    @property
    def current(self) -> ContextRef:
        return self._pact.snapshot.context

    def at(self, revision: int) -> ContextRef:
        return self._pact._context_at(revision)

    def stream(
        self,
        *,
        mode: str = "message_surface",
        anchor_row_id: str | None = None,
        anchor_seq: int | None = None,
        direction: str = "before",
        limit: int = 80,
    ) -> Mapping[str, Any]:
        """Page context rows from this surface's pinned PACT snapshot."""

        return self._pact.read_handle.stream(
            mode=mode,
            anchor_row_id=anchor_row_id,
            anchor_seq=anchor_seq,
            direction=direction,
            limit=limit,
        )

    def fork(self, *, at: int) -> "Pact":
        return self._pact.fork(context=self.at(at))


class Pact:
    """One pinned PACT snapshot plus ordered independently installed extensions."""

    def __init__(
        self,
        *,
        path: Path,
        requested: tuple[_RequestedExtension, ...],
        descriptors: tuple[ExtensionDescriptor, ...],
        branch_id: str | None,
        revision: int | None,
        scope_id: str | None,
        state_roots: StateRoots | None,
        shared_services: _SharedHostServices | None = None,
        owns_transaction: bool = True,
    ):
        self.path = path.expanduser().resolve()
        self._requested = requested
        self._descriptors = descriptors
        self._requested_branch = branch_id
        self._requested_revision = revision
        self._lock = threading.RLock()
        self._closed = False
        self._retired: list[list[_Binding]] = []
        self._open_id = uuid.uuid4().hex
        self._external_refs = ExternalReferenceRegistry()
        self._cancellation = CancellationToken()
        self._owns_transaction = owns_transaction

        handle, info, held_revision = self._open_snapshot(branch_id, revision)
        file_identity = str(
            info.get("source_file_identity")
            or stable_token(str(self.path), prefix="file")
        )
        raw_scope = scope_id or _runtime_scope.get() or "standalone"
        self.scope_id = _normalize_scope(raw_scope)
        self.pact_file_id = file_identity
        self.context_tree_id = str(
            info.get("context_tree_id")
            or info.get("root_id")
            or stable_token(file_identity, prefix="context")
        )
        self.branch_id = str(info.get("branch_id") or branch_id or "main")
        self._file_token = stable_token(file_identity, prefix="file")

        if shared_services is None:
            self._services = _make_shared_services(
                path=self.path,
                branch_id=self.branch_id,
                initial_revision=int(info["revision"]),
                state_roots=state_roots or StateRoots.resolve(),
            )
        else:
            if shared_services.path != self.path or shared_services.branch_id != self.branch_id:
                handle.close()
                raise ExtensionConfigurationError(
                    "shared extension host services belong to another PACT branch"
                )
            self._services = shared_services
        self._state_roots = self._services.state_roots
        self._commit_stream = self._services.commit_stream
        self._policy = self._services.policy
        self._process_supervisor = self._services.process_supervisor
        self._authorizations = self._services.authorizations
        self._logger = self._services.logger

        self._read_handle = handle
        self._held_revision = held_revision
        self._transaction = self._services.transaction
        try:
            bindings, snapshot = self._activate(handle, held_revision)
        except BaseException:
            self._cancellation.cancel()
            shutil.rmtree(self._state_roots.runtime / self._open_id, ignore_errors=True)
            if self._owns_transaction:
                self._transaction.close()
                self._authorizations.close()
            else:
                self._transaction.abort_participant(self._open_id)
            raise
        self._bindings = bindings
        self._snapshot = snapshot
        self.context = ContextSurface(self)

    @property
    def extensions(self) -> Mapping[str, Any]:
        return MappingProxyType(
            {binding.descriptor.name: binding.surface for binding in self._bindings}
        )

    @property
    def snapshot(self) -> RuntimeSnapshot:
        return self._snapshot

    @property
    def read_handle(self) -> Any:
        return self._read_handle

    def at(self, revision: int) -> SynchronizedRef:
        self._validate_revision(revision)
        return SynchronizedRef(revision=revision)

    def extension_record(
        self,
        record_id: str,
        *,
        at_revision: int | None = None,
    ) -> Mapping[str, Any]:
        """Read a generic extension record even when its package is not installed."""

        return self._read_handle.extension(
            record_id,
            at_revision=self._held_revision if at_revision is None else at_revision,
        )

    def extension_records(
        self,
        *,
        kind: str | None = None,
        index_key: str | None = None,
        cursor: Mapping[str, Any] | None = None,
        direction: str = "before",
        limit: int = 100,
        at_revision: int | None = None,
    ) -> Mapping[str, Any]:
        """Page generic records through the pinned native index."""

        return self._read_handle.extensions(
            kind=kind,
            index_key=index_key,
            cursor=cursor,
            direction=direction,
            limit=limit,
            at_revision=self._held_revision if at_revision is None else at_revision,
        )

    def __getattr__(self, name: str) -> Any:
        for binding in self.__dict__.get("_bindings", ()):
            if binding.descriptor.name == name:
                return binding.surface
        raise AttributeError(name)

    def __enter__(self) -> "Pact":
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cancellation.cancel()
            first_error: BaseException | None = None
            groups = [self._bindings, *reversed(self._retired)]
            for bindings in groups:
                for binding in reversed(bindings):
                    try:
                        binding.close()
                    except BaseException as error:
                        first_error = first_error or error
            try:
                if self._owns_transaction:
                    self._transaction.close()
                    self._authorizations.close()
                else:
                    self._transaction.abort_participant(self._open_id)
            except BaseException as error:
                first_error = first_error or error
            runtime_root = self._state_roots.runtime / self._open_id
            shutil.rmtree(runtime_root, ignore_errors=True)
            if first_error is not None:
                raise ExtensionActivationError("extension shutdown failed") from first_error

    def refresh(self) -> RuntimeSnapshot:
        """Atomically select a new head; existing pinned handles remain valid."""

        with self._lock:
            self._ensure_open()
            handle, info, held_revision = self._open_snapshot(
                self.branch_id,
                None,
            )
            branch_id = str(info.get("branch_id") or self.branch_id)
            previous_external_refs = self._external_refs
            self._external_refs = ExternalReferenceRegistry()
            try:
                bindings, snapshot = self._activate(handle, held_revision)
            except BaseException:
                self._external_refs = previous_external_refs
                raise
            previous_bindings = self._bindings
            self._retired.append(previous_bindings)
            self._bindings = bindings
            self._read_handle = handle
            self._held_revision = held_revision
            self.branch_id = branch_id
            self._snapshot = snapshot
            self._commit_stream.reconcile(info)
            for binding in reversed(previous_bindings):
                try:
                    binding.retire()
                except BaseException:
                    self._logger.exception(
                        "retired extension generation failed to stop maintenance"
                    )
            return snapshot

    def fork(
        self,
        *,
        at: int | SynchronizedRef | None = None,
        context: int | ContextRef | None = None,
        files: Any = _UNSET,
        path: str | Path | None = None,
    ) -> "Pact":
        """Create a non-destructive child after every authoritative fork barrier passes."""

        with self._lock:
            self._ensure_open()
            context_ref, selected_views = self._resolve_fork_selection(
                at=at,
                context=context,
                files=files,
            )
            child_id = uuid.uuid4().hex
            child_scope = stable_token(
                f"{self.scope_id}\0{child_id}",
                prefix="scope",
            )
            child_path = (
                Path(path).expanduser().resolve()
                if path is not None
                else self._default_child_path(child_id)
            )
            if child_path.exists():
                raise ExtensionForkError(f"fork target already exists: {child_path}")
            child_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

            prepared: list[PreparedFork] = []
            sealed = False
            try:
                for binding in self._bindings:
                    if binding.descriptor.authority != "authoritative":
                        continue
                    prepare = _method(binding, "prepare_fork")
                    if prepare is None:
                        raise ExtensionForkError(
                            f"authoritative extension {binding.descriptor.name!r} "
                            "does not implement prepare_fork"
                        )
                    plan = prepare(
                        source_view=selected_views[binding.descriptor.name],
                        context=context_ref,
                        child_scope_id=child_scope,
                        child_path=child_path,
                    )
                    if not isinstance(plan, PreparedFork):
                        raise ExtensionForkError(
                            f"extension {binding.descriptor.name!r} returned an invalid fork plan"
                        )
                    prepared.append(plan)

                self._read_handle.fork(
                    str(child_path),
                    revision=context_ref.revision,
                )
                child_handle, child_info, child_revision = self._open_snapshot_for_path(
                    child_path,
                    None,
                    None,
                )
                child_handle.close()
                child_context = ContextRef(
                    context_tree_id=str(
                        child_info.get("context_tree_id")
                        or child_info.get("root_id")
                    ),
                    branch_id=str(child_info.get("branch_id") or "main"),
                    revision=child_revision,
                )
                resolved = [plan.resolve_child(child_context) for plan in prepared]
                records = [record for _view, items in resolved for record in items]
                child_views = {
                    plan.extension: view
                    for plan, (view, _items) in zip(prepared, resolved, strict=True)
                }
                records.append(
                    _composite_branch_record(
                        child_id=child_id,
                        source_context=context_ref,
                        selected_views=selected_views,
                        child_views=child_views,
                    )
                )
                from pactree import open_extension_writer

                writer = open_extension_writer(child_path)
                writer.append(records)
                sealed = True
                for plan in prepared:
                    plan.commit()
            except BaseException as error:
                for plan in reversed(prepared):
                    try:
                        plan.rollback()
                    except BaseException:
                        self._logger.exception("authoritative fork rollback failed")
                if child_path.exists() and not sealed:
                    child_path.unlink(missing_ok=True)
                if isinstance(error, ExtensionForkError):
                    raise
                if sealed:
                    raise ExtensionForkError(
                        f"Pactree child fork sealed at {child_path}; "
                        "child retained for repair"
                    ) from error
                raise ExtensionForkError(
                    "Pactree child fork failed before canonical seal"
                ) from error

            return open_pact(
                child_path,
                extensions=_specs_for_reopen(self._requested),
                _scope_id=child_scope,
                _state_roots=self._state_roots,
            )

    def _open_snapshot(
        self,
        branch_id: str | None,
        revision: int | None,
    ) -> tuple[Any, Mapping[str, Any], int]:
        return self._open_snapshot_for_path(self.path, branch_id, revision)

    @staticmethod
    def _open_snapshot_for_path(
        path: Path,
        branch_id: str | None,
        revision: int | None,
    ) -> tuple[Any, Mapping[str, Any], int]:
        from pactree import open_handle

        handle = open_handle(path, branch_id)
        info = handle.info()
        head_revision = int(info["revision"])
        held_revision = head_revision if revision is None else revision
        if held_revision < 0 or held_revision > head_revision:
            raise ExtensionConfigurationError(
                f"revision {held_revision} is outside branch head {head_revision}"
            )
        return handle, info, held_revision

    def _activate(
        self,
        handle: Any,
        held_revision: int,
    ) -> tuple[list[_Binding], RuntimeSnapshot]:
        bindings: list[_Binding] = []
        extension_views: dict[str, Any] = {}
        try:
            for requested, descriptor in zip(self._requested, self._descriptors, strict=True):
                self._policy.require(descriptor.name, descriptor.required_capabilities)
                data_dir, cache_dir, runtime_dir = self._state_roots.for_extension(
                    extension=descriptor.name,
                    file_token=self._file_token,
                    scope_token=self.scope_id,
                    open_id=self._open_id,
                )
                shared_data_dir = self._state_roots.shared_data_for_extension(
                    descriptor.name
                )
                _ensure_state_schema(descriptor, data_dir)
                context = ExtensionContext(
                    pact_path=self.path,
                    pact_file_id=self.pact_file_id,
                    context_tree_id=self.context_tree_id,
                    branch_id=self.branch_id,
                    held_revision=held_revision,
                    extension_views=extension_views,
                    scope_id=self.scope_id,
                    open_id=self._open_id,
                    read_handle=handle,
                    transaction=self._transaction.participant(
                        descriptor.name,
                        descriptor.record_prefixes,
                        participant_id=self._open_id,
                        scope_id=self.scope_id,
                    ),
                    commit_stream=self._commit_stream,
                    shared_data_dir=shared_data_dir,
                    data_dir=data_dir,
                    cache_dir=cache_dir,
                    runtime_dir=runtime_dir,
                    external_refs=self._external_refs,
                    authorizations=self._authorizations.verifier,
                    process_supervisor=self._process_supervisor,
                    policy=self._policy,
                    cancellation=self._cancellation,
                    logger=self._logger.getChild(descriptor.name),
                )
                instance: Any | None = None
                try:
                    instance = descriptor.factory(context, requested.dependency)
                    activator = getattr(instance, "activate", None)
                    activated = activator() if callable(activator) else instance
                    surface = instance if activated is None else activated
                    binding = _Binding(
                        descriptor=descriptor,
                        dependency=requested.dependency,
                        context=context,
                        instance=instance,
                        surface=surface,
                    )
                    bindings.append(binding)
                    if descriptor.authority == "authoritative":
                        snapshot_view = _method(binding, "snapshot_view")
                        if snapshot_view is None:
                            raise ExtensionCompatibilityError(
                                f"authoritative extension {descriptor.name!r} "
                                "does not expose snapshot_view"
                            )
                        extension_views[descriptor.name] = snapshot_view()
                except BaseException:
                    if instance is not None and not any(
                        binding.instance is instance for binding in bindings
                    ):
                        closer = getattr(instance, "close", None)
                        if callable(closer):
                            closer()
                    raise
        except BaseException as error:
            for binding in reversed(bindings):
                try:
                    binding.close()
                except BaseException:
                    self._logger.exception("partial extension activation cleanup failed")
            if isinstance(
                error,
                (
                    ExtensionCapabilityError,
                    ExtensionCompatibilityError,
                    ExtensionConfigurationError,
                ),
            ):
                raise
            raise ExtensionActivationError("extension activation failed") from error

        snapshot = RuntimeSnapshot(
            context=ContextRef(
                context_tree_id=self.context_tree_id,
                branch_id=self.branch_id,
                revision=held_revision,
            ),
            extension_views=extension_views,
        )
        return bindings, snapshot

    def _context_at(self, revision: int) -> ContextRef:
        self._validate_revision(revision)
        return ContextRef(
            context_tree_id=self.context_tree_id,
            branch_id=self.branch_id,
            revision=revision,
        )

    def _validate_revision(self, revision: int) -> None:
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise ExtensionConfigurationError("revision must be a non-negative integer")
        head = int(self._read_handle.info()["revision"])
        if revision > head:
            raise ExtensionConfigurationError(
                f"revision {revision} is outside branch head {head}"
            )

    def _resolve_fork_selection(
        self,
        *,
        at: int | SynchronizedRef | None,
        context: int | ContextRef | None,
        files: Any,
    ) -> tuple[ContextRef, dict[str, Any]]:
        if at is not None and (context is not None or files is not _UNSET):
            raise ExtensionConfigurationError(
                "synchronized at cannot be combined with independent context/files selection"
            )
        current_views = dict(self.snapshot.extension_views)
        if at is not None:
            revision = at.revision if isinstance(at, SynchronizedRef) else at
            context_ref = self._context_at(revision)
            selected: dict[str, Any] = {}
            for binding in self._bindings:
                if binding.descriptor.authority != "authoritative":
                    continue
                resolve = _method(binding, "view_at_revision")
                if resolve is None:
                    raise ExtensionForkError(
                        f"authoritative extension {binding.descriptor.name!r} "
                        "cannot resolve synchronized revisions"
                    )
                selected[binding.descriptor.name] = resolve(revision)
            return context_ref, selected

        if context is None:
            context_ref = self.snapshot.context
        elif isinstance(context, int):
            context_ref = self._context_at(context)
        elif isinstance(context, ContextRef):
            if (
                context.context_tree_id != self.context_tree_id
                or context.branch_id != self.branch_id
            ):
                raise ExtensionConfigurationError("context selection belongs to another PACT")
            self._validate_revision(context.revision)
            context_ref = context
        else:
            raise ExtensionConfigurationError("context selection is malformed")

        if files is not _UNSET:
            if "fs" not in current_views:
                raise ExtensionConfigurationError("files selection requires the fs extension")
            current_views["fs"] = files
        return context_ref, current_views

    def _default_child_path(self, child_id: str) -> Path:
        lineage = stable_token(self.context_tree_id, prefix="lineage")
        return self._state_roots.data / "lineages" / lineage / f"{child_id}.pact"

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Pactree host is closed")


class PactScopeView:
    """One non-owning, scope-bound, immutable read generation."""

    def __init__(self, host: "EmbeddedExtensionHost", pact: Pact):
        self._host = host
        self._pact = pact
        self._closed = False

    @property
    def path(self) -> Path:
        return self._pact.path

    @property
    def scope_id(self) -> str:
        return self._pact.scope_id

    @property
    def snapshot(self) -> RuntimeSnapshot:
        self._ensure_open()
        return self._pact.snapshot

    @property
    def context(self) -> ContextSurface:
        self._ensure_open()
        return self._pact.context

    @property
    def extensions(self) -> Mapping[str, Any]:
        self._ensure_open()
        return self._pact.extensions

    @property
    def read_handle(self) -> Any:
        self._ensure_open()
        return self._pact.read_handle

    def at(self, revision: int) -> SynchronizedRef:
        self._ensure_open()
        return self._pact.at(revision)

    def extension_record(self, *args: Any, **kwargs: Any) -> Mapping[str, Any]:
        self._ensure_open()
        return self._pact.extension_record(*args, **kwargs)

    def extension_records(self, *args: Any, **kwargs: Any) -> Mapping[str, Any]:
        self._ensure_open()
        return self._pact.extension_records(*args, **kwargs)

    def fork(self, *args: Any, **kwargs: Any) -> Pact:
        self._ensure_open()
        return self._pact.fork(*args, **kwargs)

    def refresh(self) -> "PactScopeView":
        self._ensure_open()
        return self._host._refresh_view(self)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._host._close_view(self)

    def __enter__(self) -> "PactScopeView":
        self._ensure_open()
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()

    def __getattr__(self, name: str) -> Any:
        extensions = self.extensions
        if name in extensions:
            return extensions[name]
        raise AttributeError(name)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Pactree scope view is closed")
        self._host._ensure_open()


class EmbeddedExtensionHost:
    """Private owner binding for one already-owned ContextTree."""

    def __init__(
        self,
        *,
        owner_tree: Any,
        path: Path,
        requested: tuple[_RequestedExtension, ...],
        descriptors: tuple[ExtensionDescriptor, ...],
        state_roots: StateRoots | None,
    ) -> None:
        handle, info, _held_revision = Pact._open_snapshot_for_path(path, None, None)
        try:
            context_tree_id = str(info.get("context_tree_id") or info.get("root_id"))
            if context_tree_id != str(owner_tree.id):
                raise ExtensionConfigurationError(
                    "embedded extension host ContextTree does not own the bound PACT"
                )
            branch_id = str(info.get("branch_id") or "main")
        finally:
            handle.close()
        self.path = path
        self._owner_tree = owner_tree
        self._requested = requested
        self._descriptors = descriptors
        self._services = _make_shared_services(
            path=path,
            branch_id=branch_id,
            initial_revision=int(info["revision"]),
            state_roots=state_roots or StateRoots.resolve(),
            owner_tree=owner_tree,
        )
        self._views: dict[int, PactScopeView] = {}
        self._lock = threading.RLock()
        self._closed = False

    def view_for(
        self,
        scope_id: str,
        *,
        revision: int | None = None,
    ) -> PactScopeView:
        with self._lock:
            self._ensure_open()
            pact = Pact(
                path=self.path,
                requested=self._requested,
                descriptors=self._descriptors,
                branch_id=self._services.branch_id,
                revision=revision,
                scope_id=scope_id,
                state_roots=self._services.state_roots,
                shared_services=self._services,
                owns_transaction=False,
            )
            view = PactScopeView(self, pact)
            self._views[id(view)] = view
            self._services.commit_stream.reconcile(pact.read_handle.info())
            return view

    def seal_prepared(
        self,
        batches: Sequence[PreparedExtensionBatch],
        *,
        boundary: str,
    ) -> Mapping[str, Any]:
        self._ensure_open()
        return self._services.transaction.seal_prepared(
            batches,
            boundary=boundary,
        )

    def receive_prepared(
        self,
        batches: Sequence[PreparedExtensionBatch],
        *,
        contents: Any,
    ) -> Mapping[str, Any]:
        """Close the owner's PostResponse boundary with prepared extension rows."""

        self._ensure_open()
        return self._services.transaction.receive_prepared(
            batches,
            contents=contents,
        )

    def authorize(
        self,
        view: PactScopeView,
        *,
        capability: str,
        expected_operation: Any,
        expected_snapshot: RuntimeSnapshot,
        expected_target_head: Any,
        actor: str,
        ttl_seconds: float = 30.0,
    ) -> ApplyAuthorization:
        self._require_view(view)
        if expected_snapshot != view.snapshot:
            raise ExtensionAuthorizationError(
                "authorization snapshot does not match the pinned scope view"
            )
        return self._services.authorizations.issue(
            capability=capability,
            scope_id=view.scope_id,
            expected_operation=expected_operation,
            expected_snapshot=expected_snapshot,
            expected_target_head=expected_target_head,
            actor=actor,
            ttl_seconds=ttl_seconds,
        )

    def consume_authorization(
        self,
        view: PactScopeView,
        authorization: ApplyAuthorization,
        *,
        capability: str,
        expected_operation: Any,
        expected_snapshot: RuntimeSnapshot,
        expected_target_head: Any,
    ) -> AuthorizationAttestation:
        self._require_view(view)
        return self._services.authorizations.consume(
            authorization,
            capability=capability,
            scope_id=view.scope_id,
            expected_operation=expected_operation,
            expected_snapshot=expected_snapshot,
            expected_target_head=expected_target_head,
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            views = tuple(self._views.values())
        first_error: BaseException | None = None
        for view in reversed(views):
            try:
                view.close()
            except BaseException as error:
                first_error = first_error or error
        try:
            self._services.transaction.close()
        except BaseException as error:
            first_error = first_error or error
        self._services.authorizations.close()
        if first_error is not None:
            raise ExtensionActivationError("embedded extension host close failed") from first_error

    def __enter__(self) -> "EmbeddedExtensionHost":
        self._ensure_open()
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()

    def _refresh_view(self, view: PactScopeView) -> PactScopeView:
        self._require_view(view)
        return self.view_for(view.scope_id)

    def reconcile(self) -> Any | None:
        """Publish a missed wake only when canonical head advanced."""

        self._ensure_open()
        handle, info, _revision = Pact._open_snapshot_for_path(
            self.path,
            self._services.branch_id,
            None,
        )
        try:
            return self._services.commit_stream.reconcile(info)
        finally:
            handle.close()

    def _close_view(self, view: PactScopeView) -> None:
        with self._lock:
            self._views.pop(id(view), None)
        view._pact.close()

    def _require_view(self, view: PactScopeView) -> None:
        self._ensure_open()
        if not isinstance(view, PactScopeView) or view._host is not self or view._closed:
            raise ExtensionAuthorizationError("scope view does not belong to this host")

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("embedded Pactree extension host is closed")


def bind_context_tree(
    context_tree: Any,
    *,
    extensions: Sequence[str | Mapping[str, Any]] = (),
    state_roots: StateRoots | None = None,
) -> EmbeddedExtensionHost:
    """Privately bind extensions to an already-owned, file-backed ContextTree."""

    ensure_bound = getattr(context_tree, "_ensure_pact_file_bound", None)
    if not callable(ensure_bound):
        raise ExtensionConfigurationError("embedded owner is not a ContextTree")
    path = ensure_bound(create=True)
    if path is None:
        raise ExtensionConfigurationError(
            "embedded ContextTree must be bound to a PACTBIN2 path"
        )
    requested = _parse_requested_extensions(extensions)
    descriptors = _discover_and_validate(requested)
    return EmbeddedExtensionHost(
        owner_tree=context_tree,
        path=Path(path).expanduser().resolve(),
        requested=requested,
        descriptors=descriptors,
        state_roots=state_roots,
    )


def _make_shared_services(
    *,
    path: Path,
    branch_id: str,
    initial_revision: int,
    state_roots: StateRoots,
    owner_tree: Any | None = None,
) -> _SharedHostServices:
    commit_stream = CommitStream()
    commit_stream.prime(branch_id=branch_id, revision=initial_revision)

    def writer_factory(selected_path: Path, selected_branch: str) -> Any:
        from pactree import open_extension_writer

        return open_extension_writer(selected_path, selected_branch)

    transaction = TransactionCoordinator(
        path=path,
        branch_id=branch_id,
        writer_factory=writer_factory,
        commit_stream=commit_stream,
        owner_tree=owner_tree,
    )
    return _SharedHostServices(
        path=path,
        branch_id=branch_id,
        state_roots=state_roots,
        commit_stream=commit_stream,
        policy=HostPolicy(),
        process_supervisor=DisabledProcessSupervisor(),
        authorizations=AuthorizationIssuer(),
        transaction=transaction,
        logger=logging.getLogger("pactree.extensions"),
    )


def open_pact(
    path: str | Path,
    *,
    extensions: Sequence[str | Mapping[str, Any]] = (),
    branch_id: str | None = None,
    revision: int | None = None,
    _scope_id: str | None = None,
    _state_roots: StateRoots | None = None,
) -> Pact:
    requested = _parse_requested_extensions(extensions)
    descriptors = _discover_and_validate(requested)
    return Pact(
        path=Path(path),
        requested=requested,
        descriptors=descriptors,
        branch_id=branch_id,
        revision=revision,
        scope_id=_scope_id,
        state_roots=_state_roots,
    )


def _parse_requested_extensions(
    specs: Sequence[str | Mapping[str, Any]],
) -> tuple[_RequestedExtension, ...]:
    if isinstance(specs, (str, bytes)) or not isinstance(specs, Sequence):
        raise ExtensionConfigurationError("extensions must be an ordered sequence")
    requested: list[_RequestedExtension] = []
    seen: set[str] = set()
    for spec in specs:
        if isinstance(spec, str):
            name, dependency = spec, None
        elif isinstance(spec, Mapping) and len(spec) == 1:
            name, dependency = next(iter(spec.items()))
        else:
            raise ExtensionConfigurationError(
                "each extension must be a string or a one-entry mapping"
            )
        if not isinstance(name, str) or not name or not name.isidentifier() or name.startswith("_"):
            raise ExtensionConfigurationError(f"invalid extension name: {name!r}")
        if name in seen:
            raise ExtensionConfigurationError(f"duplicate extension: {name}")
        seen.add(name)
        requested.append(_RequestedExtension(name=name, dependency=dependency))
    return tuple(requested)


def _discover_and_validate(
    requested: tuple[_RequestedExtension, ...],
) -> tuple[ExtensionDescriptor, ...]:
    available: dict[str, list[Any]] = {}
    entry_points = importlib.metadata.entry_points()
    selected = (
        entry_points.select(group=ENTRY_POINT_GROUP)
        if hasattr(entry_points, "select")
        else entry_points.get(ENTRY_POINT_GROUP, ())
    )
    for entry_point in selected:
        available.setdefault(entry_point.name, []).append(entry_point)

    descriptors: list[ExtensionDescriptor] = []
    for item in requested:
        matches = available.get(item.name, [])
        if not matches:
            raise ExtensionNotFoundError(
                f"extension {item.name!r} is not installed in {ENTRY_POINT_GROUP}"
            )
        if len(matches) != 1:
            raise ExtensionCompatibilityError(
                f"multiple installed entry points own extension {item.name!r}"
            )
        loaded = matches[0].load()
        descriptor = loaded if isinstance(loaded, ExtensionDescriptor) else loaded()
        if not isinstance(descriptor, ExtensionDescriptor):
            raise ExtensionCompatibilityError(
                f"entry point {item.name!r} did not return ExtensionDescriptor"
            )
        descriptors.append(descriptor)
    _validate_descriptors(requested, descriptors)
    return tuple(descriptors)


def _validate_descriptors(
    requested: tuple[_RequestedExtension, ...],
    descriptors: list[ExtensionDescriptor],
) -> None:
    loaded_names: list[str] = []
    prefixes: list[tuple[str, str]] = []
    derived_seen = False
    reserved_aliases = set(dir(Pact)) | {"context", "extensions", "path", "scope_id"}
    for item, descriptor in zip(requested, descriptors, strict=True):
        if descriptor.name != item.name:
            raise ExtensionCompatibilityError(
                f"entry point {item.name!r} returned descriptor {descriptor.name!r}"
            )
        if descriptor.api_version != PACTREE_EXTENSION_API_VERSION:
            raise ExtensionCompatibilityError(
                f"extension {item.name!r} uses unsupported API {descriptor.api_version!r}"
            )
        if descriptor.name in reserved_aliases:
            raise ExtensionCompatibilityError(
                f"extension name collides with Pactree host surface: {descriptor.name}"
            )
        if descriptor.authority not in {"authoritative", "derived"}:
            raise ExtensionCompatibilityError(
                f"extension {item.name!r} has invalid authority {descriptor.authority!r}"
            )
        if descriptor.fork_strategy not in {"none", "authoritative", "follow"}:
            raise ExtensionCompatibilityError(
                f"extension {item.name!r} has invalid fork strategy"
            )
        if descriptor.authority == "authoritative":
            if derived_seen:
                raise ExtensionOrderError(
                    "authoritative extensions must activate before derived extensions"
                )
            if descriptor.fork_strategy != "authoritative":
                raise ExtensionCompatibilityError(
                    f"authoritative extension {item.name!r} must join the fork barrier"
                )
        else:
            derived_seen = True
        missing_dependencies = [
            name for name in descriptor.requires_extensions if name not in loaded_names
        ]
        if missing_dependencies:
            raise ExtensionOrderError(
                f"extension {item.name!r} must follow: " + ", ".join(missing_dependencies)
            )
        if not isinstance(descriptor.state_schema_version, int) or descriptor.state_schema_version < 1:
            raise ExtensionCompatibilityError(
                f"extension {item.name!r} has invalid state schema version"
            )
        if not descriptor.record_prefixes:
            raise ExtensionCompatibilityError(
                f"extension {item.name!r} must declare at least one record prefix"
            )
        for prefix in descriptor.record_prefixes:
            if not prefix:
                raise ExtensionCompatibilityError(
                    f"extension {item.name!r} declares an empty record prefix"
                )
            for owner, existing in prefixes:
                if prefix.startswith(existing) or existing.startswith(prefix):
                    raise ExtensionCompatibilityError(
                        f"record prefix collision between {owner!r} and {item.name!r}"
                    )
            prefixes.append((item.name, prefix))
        loaded_names.append(item.name)


def _ensure_state_schema(descriptor: ExtensionDescriptor, data_dir: Path) -> None:
    marker = data_dir / ".schema-version"
    if not marker.exists():
        marker.write_text(f"{descriptor.state_schema_version}\n", encoding="utf-8")
        return
    try:
        current = int(marker.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as error:
        raise ExtensionCompatibilityError(
            f"extension {descriptor.name!r} has an unreadable state schema marker"
        ) from error
    target = descriptor.state_schema_version
    if current == target:
        return
    if current > target:
        raise ExtensionCompatibilityError(
            f"extension {descriptor.name!r} cannot downgrade state {current} to {target}"
        )
    if descriptor.state_migrator is None:
        raise ExtensionCompatibilityError(
            f"extension {descriptor.name!r} requires state migration {current} -> {target}"
        )
    descriptor.state_migrator(data_dir, current, target)
    marker.write_text(f"{target}\n", encoding="utf-8")


def _method(binding: _Binding, name: str) -> Any | None:
    method = getattr(binding.instance, name, None)
    if callable(method):
        return method
    method = getattr(binding.surface, name, None)
    return method if callable(method) else None


def _specs_for_reopen(
    requested: tuple[_RequestedExtension, ...],
) -> list[str | Mapping[str, Any]]:
    return [
        item.name if item.dependency is None else {item.name: item.dependency}
        for item in requested
    ]


def _normalize_scope(value: str) -> str:
    if (
        value.startswith("scope-")
        and len(value) == 30
        and all(character in "0123456789abcdef" for character in value[6:])
    ):
        return value
    return stable_token(value, prefix="scope")


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ExtensionForkError(f"extension view is not serializable: {type(value).__name__}")


def _composite_branch_record(
    *,
    child_id: str,
    source_context: ContextRef,
    selected_views: Mapping[str, Any],
    child_views: Mapping[str, Any],
) -> Mapping[str, Any]:
    payload = {
        "source_context": _jsonable(source_context),
        "selected_extension_views": _jsonable(selected_views),
        "child_extension_views": _jsonable(child_views),
    }
    json.dumps(payload, sort_keys=True)
    return {
        "schema": "pactree.composite-branch.v1",
        "kind": "pactree.host.composite_branch",
        "record_id": f"fork-{child_id}",
        "payload": payload,
        "content_refs": [],
        "external_refs": [],
        "index_keys": [f"child:{child_id}"],
    }
