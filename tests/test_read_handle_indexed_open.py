from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "bindings" / "context_tree.rs"


def test_python_read_handle_open_is_indexed_only_and_lazily_hydrates():
    source = SOURCE.read_text(encoding="utf-8")
    start = source.index("pub struct PyReadHandle")
    end = source.index("#[pymethods]\nimpl PyContextTree")
    read_handle = source[start:end]

    assert "PactFileReadHandle::open" in read_handle
    assert "ContextTree::open_file" not in read_handle
    assert "inner: Option<pact::ContextTree>" not in read_handle
