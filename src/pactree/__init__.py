"""Python host edge for the Rust-owned Pactree substrate."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ._version import __version__
from . import file
from .components import external_ref
from .engine import EngineUnavailableError, engine_available
from .extensions import (
    ContextRef,
    ExtensionActivationError,
    ExtensionCapabilityError,
    ExtensionCompatibilityError,
    ExtensionConfigurationError,
    ExtensionContext,
    ExtensionDescriptor,
    ExtensionForkError,
    ExtensionNotFoundError,
    ExtensionOrderError,
    ExtensionViewRef,
    Pact,
    PactExtensionError,
    PreparedFork,
    RuntimeSnapshot,
    SynchronizedRef,
    open_pact,
)


def open(
    path: str | Path,
    *,
    extensions: Sequence[str | Mapping[str, Any]] = (),
    branch_id: str | None = None,
    revision: int | None = None,
) -> Pact:
    """Open one pinned PACT plus ordered independently installed extensions."""

    return open_pact(
        path,
        extensions=extensions,
        branch_id=branch_id,
        revision=revision,
    )


def open_handle(path: str | Path, branch_id: str | None = None):
    """Open a read-only snapshot handle over a `.pact` file."""

    from .pactree_engine import open_handle as _open_handle  # type: ignore[attr-defined]

    return _open_handle(str(Path(path).expanduser()), branch_id)


def open_extension_writer(path: str | Path, branch_id: str | None = None):
    """Open a bounded, extension-only PACTBIN2 writer."""

    from .pactree_engine import ExtensionWriter  # type: ignore[attr-defined]

    return ExtensionWriter.open(str(Path(path).expanduser()), branch_id)


def open_append_writer(
    path: str | Path,
    branch_id: str = "main",
    memory_options: Mapping[str, int] | None = None,
    derived_depth: bool = False,
):
    """Open the bounded native append writer (`pact.append.v1`).

    The writer records finalized message batches as one root transaction each, with a
    durable receipt per batch. Exact retries return the saved receipt; conflicts, format
    limits and unknown outcomes surface as `PactAppendError` with a native `category`.
    """

    from .pactree_engine import AppendWriter  # type: ignore[attr-defined]

    return AppendWriter.open(
        str(Path(path).expanduser()),
        branch_id,
        dict(memory_options) if memory_options is not None else None,
        derived_depth,
    )


def open_head_fast(path: str | Path):
    """Read head metadata through the fast file-header path."""

    from .pactree_engine import _pact_file_open_head_fast  # type: ignore[attr-defined]

    return _pact_file_open_head_fast(str(Path(path).expanduser()))


def batch_open_head_fast(paths: list[str | Path]):
    """Read fast head metadata for a fleet of `.pact` files in one engine call."""

    from .pactree_engine import _pact_file_batch_open_head_fast  # type: ignore[attr-defined]

    return _pact_file_batch_open_head_fast(
        [str(Path(path).expanduser()) for path in paths]
    )


def __getattr__(name: str):
    if name == "ReadHandle":
        from .pactree_engine import ReadHandle  # type: ignore[attr-defined]

        return ReadHandle
    if name == "ExtensionWriter":
        from .pactree_engine import ExtensionWriter  # type: ignore[attr-defined]

        return ExtensionWriter
    if name == "AppendWriter":
        from .pactree_engine import AppendWriter  # type: ignore[attr-defined]

        return AppendWriter
    if name == "PACT_APPEND_CAPABILITY":
        from .pactree_engine import PACT_APPEND_CAPABILITY  # type: ignore[attr-defined]

        return PACT_APPEND_CAPABILITY
    if name in {"ContextTree", "UpdateResult", "TraceRef"}:
        from .context import ContextTree, UpdateResult, TraceRef

        return {
            "ContextTree": ContextTree,
            "UpdateResult": UpdateResult,
            "TraceRef": TraceRef,
        }[name]
    if name == "Placement":

        from .pactree_engine import Placement  # type: ignore[attr-defined]

        return Placement
    raise AttributeError(name)





try:
    from .pactree_engine import (  # type: ignore[attr-defined]
        PactAppendError,
        PactCodecError,
        PactCorruptFile,
        PactError,
        PactInvalidInput,
        PactNotFound,
    )
except ImportError:  # pragma: no cover - engine not bundled in this release

    class PactError(ValueError):  # type: ignore[no-redef]
        """Base class for every error raised by the pactree native engine."""

    class PactInvalidInput(PactError):  # type: ignore[no-redef]
        """Caller supplied malformed or invalid input."""

    class PactNotFound(PactError):  # type: ignore[no-redef]
        """A referenced node, branch, transaction, component, or revision is missing."""

    class PactCorruptFile(PactError):  # type: ignore[no-redef]
        """A .pact artifact is structurally corrupt."""

    class PactCodecError(PactError):  # type: ignore[no-redef]
        """Encode/decode of pactree bytes or JSON failed."""

    class PactAppendError(PactError):  # type: ignore[no-redef]
        """A bounded append failed; `category` names the native failure variant."""


__all__ = [
    "AppendWriter",
    "ContextTree",
    "ExtensionWriter",
    "PactAppendError",
    "open_append_writer",
    "ReadHandle",
    "Placement",
    "EngineUnavailableError",
    "ContextRef",
    "ExtensionActivationError",
    "ExtensionCapabilityError",
    "ExtensionCompatibilityError",
    "ExtensionConfigurationError",
    "ExtensionContext",
    "ExtensionDescriptor",
    "ExtensionForkError",
    "ExtensionNotFoundError",
    "ExtensionOrderError",
    "ExtensionViewRef",
    "Pact",
    "PactExtensionError",
    "PreparedFork",
    "RuntimeSnapshot",
    "SynchronizedRef",
    "engine_available",
    "external_ref",
    "file",
    "PactCodecError",
    "PactCorruptFile",
    "PactError",
    "PactInvalidInput",
    "PactNotFound",
    "TraceRef",
    "UpdateResult",
    "batch_open_head_fast",
    "open_head_fast",
    "open_handle",
    "open_extension_writer",
    "open",
    "__version__",
]
