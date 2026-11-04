"""Public, serialization-safe models for independently packaged extensions."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Protocol, runtime_checkable


PACTREE_EXTENSION_API_VERSION = "1"
ExtensionAuthority = Literal["authoritative", "derived"]
ForkStrategy = Literal["none", "authoritative", "follow"]


@dataclass(frozen=True, slots=True)
class ContextRef:
    context_tree_id: str
    branch_id: str
    revision: int


@dataclass(frozen=True, slots=True)
class ExtensionViewRef:
    extension: str
    namespace_id: str
    branch_id: str
    checkpoint_id: str


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    context: ContextRef
    extension_views: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "extension_views",
            MappingProxyType(dict(self.extension_views)),
        )

    @property
    def files(self) -> Any | None:
        return self.extension_views.get("fs")


@dataclass(frozen=True, slots=True)
class SynchronizedRef:
    revision: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtensionDescriptor:
    name: str
    factory: Callable[["ExtensionContext", Any], Any]
    api_version: str = PACTREE_EXTENSION_API_VERSION
    package_version: str = "0"
    authority: ExtensionAuthority = "derived"
    record_prefixes: tuple[str, ...] = ()
    required_capabilities: frozenset[str] = frozenset()
    optional_capabilities: frozenset[str] = frozenset()
    state_schema_version: int = 1
    state_migrator: Callable[[Path, int, int], None] | None = None
    fork_strategy: ForkStrategy = "none"
    requires_extensions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtensionContext:
    pact_path: Path
    pact_file_id: str
    context_tree_id: str
    branch_id: str
    held_revision: int
    extension_views: Mapping[str, Any]
    scope_id: str
    open_id: str
    read_handle: Any
    transaction: Any
    commit_stream: Any
    shared_data_dir: Path
    data_dir: Path
    cache_dir: Path
    runtime_dir: Path
    external_refs: Any
    authorizations: Any
    process_supervisor: Any
    policy: Any
    cancellation: Any
    logger: Any

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "extension_views",
            MappingProxyType(dict(self.extension_views)),
        )

    @property
    def snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            context=ContextRef(
                context_tree_id=self.context_tree_id,
                branch_id=self.branch_id,
                revision=self.held_revision,
            ),
            extension_views=self.extension_views,
        )


@dataclass(slots=True)
class PreparedFork:
    """Prepared authoritative child state awaiting the canonical PACT seal."""

    extension: str
    child_view: Any = None
    records: Sequence[Mapping[str, Any]] = ()
    bind_child: Callable[
        [ContextRef], tuple[Any, Sequence[Mapping[str, Any]]]
    ] | None = None
    finalize: Callable[[], None] = lambda: None
    abort: Callable[[], None] = lambda: None
    _settled: bool = field(default=False, init=False, repr=False)

    def resolve_child(
        self,
        context: ContextRef,
    ) -> tuple[Any, Sequence[Mapping[str, Any]]]:
        if self.bind_child is None:
            return self.child_view, self.records
        return self.bind_child(context)

    def commit(self) -> None:
        if self._settled:
            return
        self.finalize()
        self._settled = True

    def rollback(self) -> None:
        if self._settled:
            return
        self.abort()
        self._settled = True


@runtime_checkable
class ExtensionLifecycle(Protocol):
    """Basic lifecycle; hosts also honor an optional ``retire()`` hook.

    ``retire()`` stops active/background work when a refreshed generation
    supersedes an instance while keeping its pinned read surface available
    until final ``close()``.
    """

    def activate(self) -> Any: ...

    def close(self) -> None: ...


@runtime_checkable
class AuthoritativeForkParticipant(Protocol):
    def snapshot_view(self) -> Any: ...

    def view_at_revision(self, revision: int) -> Any: ...

    def prepare_fork(
        self,
        *,
        source_view: Any,
        context: ContextRef,
        child_scope_id: str,
        child_path: Path,
    ) -> PreparedFork: ...
