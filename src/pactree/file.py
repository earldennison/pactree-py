"""Python facade for canonical Rust `.pact` files.

The storage engine lives in Rust. This module keeps Python at the API edge:
paths and JSON-shaped payloads in, JSON-shaped reports out.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import pactree_engine











def inspect(path: str | Path) -> dict[str, Any]:
    return pactree_engine._pact_file_inspect(str(path))


def open(
    path: str | Path,
    *,
    branch_id: str | None = None,
    revision: int | None = None,
) -> dict[str, Any]:
    """Open a projection of the file, optionally AT A REVISION.

    Without ``revision`` this is the branch head (the ``revision`` field of
    the result names it). With ``revision=N`` the projection is the tree as
    it stood at that revision — time travel reads; rewind/fork-at compose
    on this.
    """
    return pactree_engine._pact_file_open(str(path), branch_id, revision, 1024)


def open_handle(path: str | Path, branch_id: str | None = None):
    """Open a read-only snapshot handle over a `.pact` file."""

    from . import open_handle as _open_handle

    return _open_handle(path, branch_id)


def open_head_fast(path: str | Path) -> dict[str, Any]:
    """Read head metadata through the fast file-header path."""

    return pactree_engine._pact_file_open_head_fast(str(Path(path).expanduser()))


def batch_open_head_fast(paths: list[str | Path]) -> list[dict[str, Any]]:
    """Read fast head metadata for a fleet of `.pact` files in one engine call."""

    return pactree_engine._pact_file_batch_open_head_fast(
        [str(Path(path).expanduser()) for path in paths]
    )


def info(path: str | Path) -> dict[str, Any]:
    return pactree_engine._pact_file_info(str(path))


def validate(path: str | Path) -> dict[str, Any]:
    return pactree_engine._pact_file_validate(str(path))


def bundle_verify(path: str | Path) -> dict[str, Any]:
    return pactree_engine._pact_file_bundle_verify(str(path))


def bundle_export_full(path: str | Path, output_path: str | Path) -> dict[str, Any]:
    return pactree_engine._pact_file_bundle_export_full(str(path), str(output_path))


def bundle_prune(path: str | Path) -> dict[str, Any]:
    return pactree_engine._pact_file_bundle_prune(str(path))
















def slice(
    source_path: str | Path,
    output_path: str | Path,
    spec: dict[str, Any] | str,
) -> dict[str, Any]:


    import json

    spec_json = spec if isinstance(spec, str) else json.dumps(spec)
    return pactree_engine._pact_file_slice(str(source_path), str(output_path), spec_json)


def import_check(path: str | Path) -> dict[str, Any]:
    return pactree_engine._pact_file_import_check(str(path))











__all__ = [
    "info",
    "inspect",
    "batch_open_head_fast",
    "open_head_fast",
    "open_handle",
    "validate",
    "bundle_verify",
    "bundle_export_full",
    "bundle_prune",
    "slice",
    "import_check",
]
