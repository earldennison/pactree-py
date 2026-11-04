"""ContextTree is a Python facade over native ContextTree.

These tests freeze the architectural boundary documented in
``pactree.context.base``. They do not exercise behavior; they assert that the
ContextTree stays a thin provider-facing adapter over the Rust-owned moving
``ContextTree`` so new PACT/runtime behavior is steered to the repository that
owns it.
"""

from __future__ import annotations

import importlib

import pytest

import pactree
from pactree import pactree_engine
from pactree.context.base import ContextTree


def test_compatibility_import_resolves_context_tree() -> None:
    assert pactree.ContextTree is ContextTree


def test_native_bridge_alias_module_is_removed() -> None:


    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("pactree.context.native_bridge")


def test_context_tree_holds_native_context_tree_not_resolver() -> None:
    ctx = ContextTree()
    assert isinstance(ctx, pactree_engine.ContextTree)
    assert not hasattr(ctx, "_tree")
    assert not hasattr(ctx, "_resolver")


def test_boundary_is_documented_as_thin_native_facade() -> None:
    module_doc = (ContextTree.__module__ and __import__(ContextTree.__module__, fromlist=["__doc__"]).__doc__) or ""
    assert "python facade over the native" in module_doc.lower()
    assert "send" in vars(ContextTree)
    assert "receive" in vars(ContextTree)
    assert "seal" not in vars(ContextTree)
    assert "pact_seal_boundary" not in vars(ContextTree)
