"""pactree-py exposes only public tree APIs.

Resolver / ledger / root-store / materialize / patch-runtime internals must not
be reachable from public package roots, and the public write surface must not
expose selectors or report methods.
"""

from __future__ import annotations

import importlib.util

import pactree
from pactree import ContextTree


FORBIDDEN_PUBLIC_NAMES = (
    "NativeResolver",
    "PactRoot",
    "PACTNode",
    "NodeRecord",
    "materialize",
    "root_store",
    "ledger",
)


def test_top_level_package_hides_internals():
    public = set(getattr(pactree, "__all__", []))
    for name in FORBIDDEN_PUBLIC_NAMES:
        assert name not in public, f"{name} leaked into pactree.__all__"
        assert not hasattr(pactree, name), f"pactree.{name} is reachable"


def test_native_and_data_models_modules_are_not_public():
    assert importlib.util.find_spec("pactree.native") is None
    assert importlib.util.find_spec("pactree.data_models") is None
    assert not hasattr(pactree, "native")
    assert not hasattr(pactree, "data_models")


def test_components_surface_has_no_internal_node_types():
    import pactree.components as comps

    for name in ("PACTNode", "PactRoot", "NodeRecord"):
        assert name not in comps.__all__
        assert not hasattr(comps, name)


def test_file_module_has_no_store_or_materialize_surface():
    import pactree.file as pfile

    for name in ("materialize", "commit_context_tree", "create", "root_store", "buffered_read"):
        assert not hasattr(pfile, name), f"pactree.file.{name} leaked"
        assert name not in getattr(pfile, "__all__", [])


def test_context_tree_has_no_selector_or_report_or_resolver_surface():
    public = [name for name in dir(ContextTree) if not name.startswith("_")]
    for name in public:
        assert "target_selector" not in name
        assert not name.endswith("_report")
        assert not name.endswith("_value_report")

    for name in ("resolver", "root_store", "ledger", "materialize"):
        assert name not in public


def test_no_public_json_root_store_construction():


    for name in ("context_tree_new", "context_tree_from_store", "pact_store_new"):
        assert not hasattr(pactree, name)
