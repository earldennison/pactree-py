"""Host-owned services shared by independently installed extensions."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import (
    ExtensionCapabilityError,
    ExtensionConfigurationError,
    ExtensionTransactionError,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - the session sidecar is Unix-only
    fcntl = None  # type: ignore[assignment]


HOST_CAPABILITIES = frozenset(
    {
        "pact.read.pinned",
        "pact.extensions.read",
        "pact.extensions.write",
        "pact.fork",
        "pact.commit_stream",
        "external_refs.register",
        "external_refs.resolve",
        "state.durable",
        "state.cache",
        "state.runtime",
        "cancellation",
    }
)


def stable_token(value: str, *, prefix: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


@dataclass(frozen=True, slots=True)
class StateRoots:
    data: Path
    cache: Path
    runtime: Path

    @classmethod
    def resolve(cls) -> "StateRoots":
        home = Path.home()
        data = Path(
            os.environ.get(
                "PACTREE_DATA_HOME",
                Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
                / "pactree",
            )
        ).expanduser()
        cache = Path(
            os.environ.get(
                "PACTREE_CACHE_HOME",
                Path(os.environ.get("XDG_CACHE_HOME", home / ".cache")) / "pactree",
            )
        ).expanduser()
        runtime_base = Path(
            os.environ.get("XDG_RUNTIME_DIR", data / "runtime")
        ).expanduser()
        runtime = runtime_base / "pactree" if runtime_base != data / "runtime" else runtime_base
        return cls(data=data, cache=cache, runtime=runtime)

    def for_extension(
        self,
        *,
        extension: str,
        file_token: str,
        scope_token: str,
        open_id: str,
    ) -> tuple[Path, Path, Path]:
        durable = self.data / "extensions" / extension / file_token / scope_token
        cache = self.cache / "extensions" / extension / file_token / scope_token
        runtime = self.runtime / open_id / extension / scope_token
        for path in (durable, cache, runtime):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return durable, cache, runtime

    def shared_data_for_extension(self, extension: str) -> Path:
        shared = self.data / "extensions" / extension
        shared.mkdir(mode=0o700, parents=True, exist_ok=True)
        return shared


class HostPolicy:
    """Default-deny capability policy for extension activation."""

    def __init__(self, granted: Iterable[str] = HOST_CAPABILITIES):
        self._granted = frozenset(granted)

    @property
    def granted(self) -> frozenset[str]:
        return self._granted

    def require(self, extension: str, capabilities: Iterable[str]) -> None:
        missing = sorted(set(capabilities) - self._granted)
        if missing:
            raise ExtensionCapabilityError(
                f"extension {extension!r} requires unavailable capabilities: "
                + ", ".join(missing)
            )


class CancellationToken:
    def __init__(self, deadline: float | None = None):
        self._event = threading.Event()
        self._deadline = deadline

    @property
    def cancelled(self) -> bool:
        return self._event.is_set() or (
            self._deadline is not None and time.monotonic() >= self._deadline
        )

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise TimeoutError("Pactree extension operation was cancelled")


@dataclass(frozen=True, slots=True)
class CommitEvent:
    sequence: int
    branch_id: str
    revision: int
    transaction_id: str


class CommitStream:
    """Bounded wake hints; PACT cursor reconciliation remains authoritative."""

    def __init__(self, *, max_events: int = 1024):
        self._events: deque[CommitEvent] = deque(maxlen=max_events)
        self._sequence = 0
        self._wake_sequence = 0
        self._latest_revision: dict[str, int] = {}
        self._condition = threading.Condition()

    def publish(self, commit: Mapping[str, Any]) -> CommitEvent:
        with self._condition:
            branch_id = str(commit["branch_id"])
            revision = int(commit["revision"])
            previous = self._latest_revision.get(branch_id, -1)
            if revision <= previous:
                for existing in reversed(self._events):
                    if (
                        existing.branch_id == branch_id
                        and existing.revision == revision
                    ):
                        return existing
                raise ExtensionTransactionError(
                    "commit notification regressed behind canonical head"
                )
            self._sequence += 1
            event = CommitEvent(
                sequence=self._sequence,
                branch_id=branch_id,
                revision=revision,
                transaction_id=str(
                    commit.get("transaction_id")
                    or stable_token(
                        f"{branch_id}\0{revision}",
                        prefix="transaction",
                    )
                ),
            )
            self._latest_revision[branch_id] = revision
            self._events.append(event)
            self._condition.notify_all()
            return event

    def reconcile(self, head: Mapping[str, Any]) -> CommitEvent | None:
        """Recover a missed wake from authoritative branch/head metadata."""

        branch_id = str(head["branch_id"])
        revision = int(head["revision"])
        with self._condition:
            if revision <= self._latest_revision.get(branch_id, -1):
                return None
        return self.publish(head)

    def prime(self, *, branch_id: str, revision: int) -> None:
        """Record an already-observed head without emitting a wake."""

        with self._condition:
            self._latest_revision[branch_id] = max(
                revision,
                self._latest_revision.get(branch_id, -1),
            )

    def after(self, sequence: int) -> tuple[CommitEvent, ...]:
        with self._condition:
            return tuple(event for event in self._events if event.sequence > sequence)

    def wait_after(self, sequence: int, timeout: float | None = None) -> tuple[CommitEvent, ...]:
        with self._condition:
            if not any(event.sequence > sequence for event in self._events):
                self._condition.wait(timeout)
            return tuple(event for event in self._events if event.sequence > sequence)

    @property
    def wake_sequence(self) -> int:
        with self._condition:
            return self._wake_sequence

    def wait_after_or_wake(
        self,
        sequence: int,
        wake_sequence: int,
        timeout: float | None = None,
    ) -> tuple[tuple[CommitEvent, ...], int]:
        """Wait for a commit or a level-triggered lifecycle wake."""

        with self._condition:
            if (
                not any(event.sequence > sequence for event in self._events)
                and self._wake_sequence == wake_sequence
            ):
                self._condition.wait(timeout)
            return (
                tuple(event for event in self._events if event.sequence > sequence),
                self._wake_sequence,
            )

    def wake(self) -> None:
        """Wake waiters without publishing a durable-commit hint."""

        with self._condition:
            self._wake_sequence += 1
            self._condition.notify_all()


class ExternalReferenceRegistry:
    """Verified, bounded external-content resolvers keyed by reference kind."""

    def __init__(self):
        self._resolvers: dict[str, Callable[..., bytes]] = {}
        self._lock = threading.RLock()

    def register(self, *, kind: str, resolver: Callable[..., bytes]) -> None:
        if not kind or not callable(resolver):
            raise ExtensionConfigurationError("external reference resolver is malformed")
        with self._lock:
            if kind in self._resolvers:
                raise ExtensionConfigurationError(
                    f"external reference resolver already registered: {kind}"
                )
            self._resolvers[kind] = resolver

    def resolve(self, *, kind: str, reference: Mapping[str, Any], max_bytes: int) -> bytes:
        if max_bytes <= 0:
            raise ExtensionConfigurationError("max_bytes must be positive")
        with self._lock:
            resolver = self._resolvers.get(kind)
        if resolver is None:
            raise LookupError(f"no verified external reference resolver for {kind!r}")
        payload = resolver(reference=reference, max_bytes=max_bytes)
        if not isinstance(payload, bytes):
            raise TypeError(f"resolver {kind!r} returned non-bytes content")
        if len(payload) > max_bytes:
            raise ValueError(f"resolver {kind!r} exceeded the {max_bytes}-byte budget")
        return payload

    def kinds(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._resolvers))


class WriterLease:
    """Cross-process lease excluding standalone and embedded extension writers."""

    def __init__(self, path: Path, branch_id: str) -> None:
        token = stable_token(branch_id, prefix="branch")
        self._path = path.with_name(f".{path.name}.{token}.extensions.lock")
        self._fd: int | None = None
        self._lock = threading.RLock()

    @property
    def held(self) -> bool:
        with self._lock:
            return self._fd is not None

    def acquire(self) -> None:
        with self._lock:
            if self._fd is not None:
                return
            if fcntl is None:
                raise ExtensionTransactionError(
                    "extension writer leases require a Unix host"
                )
            fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
            os.fchmod(fd, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                os.close(fd)
                raise ExtensionTransactionError(
                    "another extension host owns the PACT writer lease"
                ) from error
            self._fd = fd

    def release(self) -> None:
        with self._lock:
            fd = self._fd
            self._fd = None
        if fd is None:
            return
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class PreparedExtensionBatch:
    """Validated extension records awaiting one owner-controlled PACT seal."""

    def __init__(
        self,
        *,
        coordinator: "TransactionCoordinator",
        participant_id: str,
        scope_id: str,
        batch_id: str,
        operation_key: str,
        content_hash: str,
        records: tuple[Mapping[str, Any], ...],
        finalize: Callable[[Mapping[str, Any]], None] | None,
        abort: Callable[[], None] | None,
    ) -> None:
        self.batch_id = batch_id
        self.participant_id = participant_id
        self.scope_id = scope_id
        self.operation_key = operation_key
        self.content_hash = content_hash
        self._records = tuple(copy.deepcopy(record) for record in records)
        self._coordinator = coordinator
        self._finalize_callback = finalize
        self._abort_callback = abort
        self._state = "prepared"
        self._commit: Mapping[str, Any] | None = None
        self._lock = threading.RLock()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def records(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(copy.deepcopy(record) for record in self._records)

    @property
    def commit(self) -> Mapping[str, Any] | None:
        with self._lock:
            return None if self._commit is None else dict(self._commit)

    def finalize(self, commit: Mapping[str, Any]) -> None:
        with self._lock:
            if self._state == "committed":
                return
            if self._state != "prepared":
                raise ExtensionTransactionError(
                    f"prepared batch {self.batch_id} is already {self._state}"
                )
            self._state = "committed"
            self._commit = dict(commit)
            self._coordinator._forget(self)
            callback = self._finalize_callback
        if callback is not None:
            callback(commit)

    def abort(self) -> None:
        with self._lock:
            if self._state != "prepared":
                return
            self._state = "aborted"
            self._coordinator._forget(self)
            callback = self._abort_callback
        if callback is not None:
            callback()


class TransactionCoordinator:
    """One lazy extension writer per open PACT, serialized across participants."""

    def __init__(
        self,
        *,
        path: Path,
        branch_id: str,
        writer_factory: Callable[[Path, str], Any],
        commit_stream: CommitStream,
        owner_tree: Any | None = None,
        writer_lease: WriterLease | None = None,
        max_operation_history: int = 4096,
        max_pending: int = 1024,
    ):
        self._path = path
        self._branch_id = branch_id
        self._writer_factory = writer_factory
        self._commit_stream = commit_stream
        self._owner_tree = owner_tree
        self._writer_lease = writer_lease or WriterLease(path, branch_id)
        self._writer: Any | None = None
        self._lock = threading.RLock()
        self._pending: dict[str, PreparedExtensionBatch] = {}
        self._completed: OrderedDict[str, PreparedExtensionBatch] = OrderedDict()
        self._operation_hashes: dict[tuple[str, str], str] = {}
        self._max_operation_history = max_operation_history
        self._max_pending = max_pending
        self._closed = False
        if owner_tree is not None:
            self._writer_lease.acquire()

    @property
    def writer_opened(self) -> bool:
        return self._writer is not None

    @property
    def writer_lease_held(self) -> bool:
        return self._writer_lease.held

    def participant(
        self,
        extension: str,
        prefixes: tuple[str, ...],
        *,
        participant_id: str,
        scope_id: str,
    ) -> "ExtensionTransaction":
        with self._lock:
            self._ensure_open()
            return ExtensionTransaction(
                self,
                extension=extension,
                prefixes=prefixes,
                participant_id=participant_id,
                scope_id=scope_id,
            )

    def prepare(
        self,
        *,
        participant_id: str,
        scope_id: str,
        operation_key: str | None,
        records: list[Mapping[str, Any]],
        finalize: Callable[[Mapping[str, Any]], None] | None,
        abort: Callable[[], None] | None,
    ) -> PreparedExtensionBatch:
        normalized_records = [copy.deepcopy(dict(record)) for record in records]
        content_hash = hashlib.sha256(
            json.dumps(
                normalized_records,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                default=repr,
            ).encode("utf-8")
        ).hexdigest()
        key = operation_key or f"operation-{uuid.uuid4().hex}"
        operation_identity = (scope_id, key)
        batch_id = stable_token(
            f"{scope_id}\0{key}\0{content_hash}",
            prefix="batch",
        )
        with self._lock:
            self._ensure_open()
            prior_hash = self._operation_hashes.get(operation_identity)
            if prior_hash is not None and prior_hash != content_hash:
                raise ExtensionTransactionError(
                    f"operation {key!r} was prepared with different content"
                )
            existing = self._pending.get(batch_id)
            if existing is not None:
                return existing
            completed = self._completed.get(batch_id)
            if completed is not None:
                self._completed.move_to_end(batch_id)
                return completed
            if len(self._pending) >= self._max_pending:
                raise ExtensionTransactionError(
                    "prepared extension batch capacity is exhausted"
                )
            batch = PreparedExtensionBatch(
                coordinator=self,
                participant_id=participant_id,
                scope_id=scope_id,
                batch_id=batch_id,
                operation_key=key,
                content_hash=content_hash,
                records=tuple(normalized_records),
                finalize=finalize,
                abort=abort,
            )
            self._operation_hashes[operation_identity] = content_hash
            self._pending[batch_id] = batch
            return batch

    def _commit_prepared(
        self,
        batches: Iterable[PreparedExtensionBatch],
    ) -> Mapping[str, Any]:
        supplied = tuple(batches)
        with self._lock:
            self._ensure_open()
            prior_commit = self._completed_commit(supplied)
            if prior_commit is not None:
                return prior_commit
            selected = self._validate_batches(supplied)
            if self._owner_tree is not None:
                raise ExtensionTransactionError(
                    "embedded extensions must seal prepared batches through their owner"
                )
            records = [record for batch in selected for record in batch.records]
            try:
                self._writer_lease.acquire()
                if self._writer is None:
                    self._writer = self._writer_factory(self._path, self._branch_id)
                commit = self._writer.append(records)
            except BaseException:
                for batch in selected:
                    batch.abort()
                raise
            self._commit_stream.publish(commit)
        self._finalize_batches(selected, commit)
        return commit

    def seal_prepared(
        self,
        batches: Iterable[PreparedExtensionBatch],
        *,
        boundary: str,
    ) -> Mapping[str, Any]:
        supplied = tuple(batches)
        with self._lock:
            self._ensure_open()
            prior_commit = self._completed_commit(supplied)
            if prior_commit is not None:
                return prior_commit
            selected = self._validate_batches(supplied)
            if self._owner_tree is None:
                raise ExtensionTransactionError(
                    "only an embedded owner can co-seal prepared batches"
                )
            records = [record for batch in selected for record in batch.records]
            try:
                self._owner_tree.stage_extensions(records)
                commit = self._owner_tree.seal_staged(boundary)
                if commit is None:
                    raise ExtensionTransactionError("owner produced no canonical commit")
            except BaseException as error:
                try:
                    self._owner_tree.discard()
                except BaseException:
                    pass
                for batch in selected:
                    batch.abort()
                if isinstance(error, ExtensionTransactionError):
                    raise
                raise ExtensionTransactionError(
                    "prepared extension batch failed before canonical seal"
                ) from error
            self._commit_stream.publish(commit)
        self._finalize_batches(selected, commit)
        return commit

    def receive_prepared(
        self,
        batches: Iterable[PreparedExtensionBatch],
        *,
        contents: Any,
    ) -> Mapping[str, Any]:
        """Close PostResponse with extension records in the same owner seal."""

        supplied = tuple(batches)
        with self._lock:
            self._ensure_open()
            prior_commit = self._completed_commit(supplied)
            if prior_commit is not None:
                return prior_commit
            selected = self._validate_batches(supplied)
            if self._owner_tree is None:
                raise ExtensionTransactionError(
                    "only an embedded owner can receive prepared batches"
                )
            records = [record for batch in selected for record in batch.records]
            try:
                self._owner_tree.stage_extensions(records)
                result = self._owner_tree.receive(contents)
                commit = getattr(result, "metadata", {}).get("commit_ref")
                if not isinstance(commit, Mapping):
                    raise ExtensionTransactionError(
                        "owner produced no canonical PostResponse commit"
                    )
            except BaseException as error:
                try:
                    self._owner_tree.discard()
                except BaseException:
                    pass
                for batch in selected:
                    batch.abort()
                if isinstance(error, ExtensionTransactionError):
                    raise
                raise ExtensionTransactionError(
                    "prepared extension batch failed before PostResponse seal"
                ) from error
            self._commit_stream.publish(commit)
        self._finalize_batches(selected, commit)
        return commit

    def abort_pending(self) -> None:
        with self._lock:
            batches = tuple(self._pending.values())
            first_error: BaseException | None = None
            for batch in batches:
                try:
                    batch.abort()
                except BaseException as error:
                    first_error = first_error or error
        if first_error is not None:
            raise ExtensionTransactionError(
                "prepared extension batch abort failed"
            ) from first_error

    def abort_participant(self, participant_id: str) -> None:
        with self._lock:
            batches = tuple(
                batch
                for batch in self._pending.values()
                if batch.participant_id == participant_id
            )
            first_error: BaseException | None = None
            for batch in batches:
                try:
                    batch.abort()
                except BaseException as error:
                    first_error = first_error or error
        if first_error is not None:
            raise ExtensionTransactionError(
                "participant prepared-batch abort failed"
            ) from first_error

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        first_error: BaseException | None = None
        try:
            self.abort_pending()
        except BaseException as error:
            first_error = error
        with self._lock:
            self._writer = None
        self._writer_lease.release()
        if first_error is not None:
            raise first_error

    def _validate_batches(
        self,
        batches: Iterable[PreparedExtensionBatch],
    ) -> tuple[PreparedExtensionBatch, ...]:
        selected = tuple(batches)
        if not selected:
            raise ExtensionTransactionError("no prepared extension batches supplied")
        seen: set[str] = set()
        for batch in selected:
            if not isinstance(batch, PreparedExtensionBatch):
                raise ExtensionTransactionError("prepared extension batch is malformed")
            if batch._coordinator is not self:
                raise ExtensionTransactionError(
                    "prepared extension batch belongs to another host"
                )
            if batch.state != "prepared":
                raise ExtensionTransactionError(
                    f"prepared extension batch is already {batch.state}"
                )
            if batch.batch_id in seen:
                raise ExtensionTransactionError("duplicate prepared extension batch")
            seen.add(batch.batch_id)
        return selected

    def _completed_commit(
        self,
        batches: Iterable[PreparedExtensionBatch],
    ) -> Mapping[str, Any] | None:
        selected = tuple(batches)
        if not selected:
            return None
        for batch in selected:
            if not isinstance(batch, PreparedExtensionBatch):
                return None
            if batch._coordinator is not self:
                return None
        if not all(batch.state == "committed" for batch in selected):
            return None
        commits = [dict(batch.commit or {}) for batch in selected]
        if not commits or any(commit != commits[0] for commit in commits[1:]):
            raise ExtensionTransactionError(
                "completed extension batches do not share one canonical commit"
            )
        return commits[0]

    def _finalize_batches(
        self,
        batches: tuple[PreparedExtensionBatch, ...],
        commit: Mapping[str, Any],
    ) -> None:
        first_error: BaseException | None = None
        for batch in batches:
            try:
                batch.finalize(commit)
            except BaseException as error:
                first_error = first_error or error
        if first_error is not None:
            raise ExtensionTransactionError(
                "canonical seal committed but extension finalization failed"
            ) from first_error

    def _forget(self, batch: PreparedExtensionBatch) -> None:
        with self._lock:
            self._pending.pop(batch.batch_id, None)
            identity = (batch.scope_id, batch.operation_key)
            if batch.state == "committed":
                self._completed[batch.batch_id] = batch
                self._completed.move_to_end(batch.batch_id)
                while len(self._completed) > self._max_operation_history:
                    _batch_id, evicted = self._completed.popitem(last=False)
                    evicted_identity = (evicted.scope_id, evicted.operation_key)
                    self._operation_hashes.pop(evicted_identity, None)
            else:
                self._operation_hashes.pop(identity, None)

    def _ensure_open(self) -> None:
        if self._closed:
            raise ExtensionTransactionError("extension transaction coordinator is closed")


class ExtensionTransaction:
    def __init__(
        self,
        coordinator: TransactionCoordinator,
        *,
        extension: str,
        prefixes: tuple[str, ...],
        participant_id: str,
        scope_id: str,
    ):
        self._coordinator = coordinator
        self._extension = extension
        self._prefixes = prefixes
        self._participant_id = participant_id
        self._scope_id = scope_id

    @property
    def owner_controlled(self) -> bool:
        """Whether prepared rows must be sealed by an embedded PACT owner."""

        return self._coordinator._owner_tree is not None

    def prepare(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        operation_key: str | None = None,
        finalize: Callable[[Mapping[str, Any]], None] | None = None,
        abort: Callable[[], None] | None = None,
    ) -> PreparedExtensionBatch:
        batch = [dict(record) for record in records]
        if not batch:
            raise ExtensionConfigurationError("extension transaction is empty")
        for record in batch:
            kind = record.get("kind")
            if not isinstance(kind, str) or not any(
                kind.startswith(prefix) for prefix in self._prefixes
            ):
                raise ExtensionConfigurationError(
                    f"extension {self._extension!r} does not own record kind {kind!r}"
                )
        return self._coordinator.prepare(
            participant_id=self._participant_id,
            scope_id=self._scope_id,
            operation_key=operation_key,
            records=batch,
            finalize=finalize,
            abort=abort,
        )

    def commit(self, records: Iterable[Mapping[str, Any]]) -> Mapping[str, Any]:
        prepared = self.prepare(records)
        return self._coordinator._commit_prepared([prepared])


class DisabledProcessSupervisor:
    """Read-only opens do not implicitly start brokers, workers, or subprocesses."""

    def start(self, *_args: Any, **_kwargs: Any) -> None:
        raise ExtensionCapabilityError(
            "process supervision requires an explicit runtime policy grant"
        )
