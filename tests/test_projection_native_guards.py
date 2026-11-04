"""Projection bindings return native classes rather than serialized JSON."""

from __future__ import annotations

import importlib.util
import pathlib
import re


_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"

RUST_SOURCES = [_SRC / "rust_lib.rs", *sorted((_SRC / "bindings").glob("*.rs"))]


PROJECTION_PYO3 = ("pact_file_projection",)


def _pyo3_return_type(text: str, fn: str) -> str:
    match = re.search(rf"\bfn {fn}\((?:[^{{]*?)\)\s*->\s*(PyResult<[^{{]+?>)\s*\{{", text, re.S)
    assert match, f"pyo3 fn {fn} not found in rust_lib.rs"
    return " ".join(match.group(1).split())


def test_pyo3_projection_fns_return_pyclasses_not_strings():
    text = "\n".join(p.read_text(encoding="utf-8") for p in RUST_SOURCES)
    for fn in PROJECTION_PYO3:
        ret = _pyo3_return_type(text, fn)
        assert "String" not in ret, f"{fn} returns {ret!r} -- must return a pyclass, not JSON"
        assert "Py<Py" in ret, f"{fn} returns {ret!r} -- expected a Py<Py...> pyclass handle"


def test_no_python_native_bridge_layer():


    assert importlib.util.find_spec("pactree._native") is None


def test_native_context_tree_preview_view_is_native():
    from pactree import pactree_engine

    assert not hasattr(pactree_engine, "NativeResolver")
    assert hasattr(pactree_engine, "ContextTree")
    tree = pactree_engine.ContextTree()
    view = tree.preview_view()
    assert view.__class__.__name__ == "PactView"
    assert isinstance(tree.preview_view_json(), str)
