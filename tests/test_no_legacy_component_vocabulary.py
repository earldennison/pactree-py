"""Production code excludes legacy component vocabulary.

The Python component model is gone; authoring components are Rust-owned wrappers.
Production code must not resurrect PACTNode/PACTCore/PactRoot or a Python
`_native` bridge module.
"""

from __future__ import annotations

import pathlib

import pactree

SRC = pathlib.Path(pactree.__file__).parent


def test_no_legacy_component_core_names_in_production():
    forbidden = ("PACTNode", "PACTCore", "PactRoot")
    for path in SRC.rglob("*.py"):
        text = path.read_text()
        for name in forbidden:
            assert name not in text, f"{name} present in {path}"


def test_no_python_native_bridge_in_production():
    for path in SRC.rglob("*.py"):
        text = path.read_text()
        assert "import pactree._native" not in text, f"_native import in {path}"
        assert "from ._native" not in text, f"_native import in {path}"
    assert not (SRC / "_native.py").exists()
