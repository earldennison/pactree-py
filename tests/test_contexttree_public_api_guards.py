"""The public write surface excludes selectors,
report methods, or the old `delete`/`target_node_id` naming.
"""

from __future__ import annotations

import inspect

import pytest

from pactree import ContextTree, Placement
from pactree.components import TextContent


WRITE_METHODS = ("insert", "append", "update", "replace", "upsert", "prune")


def test_write_methods_exist():
    for name in WRITE_METHODS:
        assert hasattr(ContextTree, name), f"missing public write {name!r}"


def test_no_target_selector_on_public_writes():
    for name in WRITE_METHODS:
        params = inspect.signature(getattr(ContextTree, name)).parameters
        assert "target_selector" not in params, f"{name} exposes target_selector"
        assert "target_node_id" not in params, f"{name} exposes target_node_id"


def test_passing_target_selector_raises_type_error():
    tree = ContextTree()
    with pytest.raises(TypeError):
        tree.insert(TextContent(content="x"), target_selector="d-1")


def test_delete_is_not_a_context_mutation():

    assert not hasattr(ContextTree, "delete")


def test_no_report_method_names():

    public = [name for name in dir(ContextTree) if not name.startswith("_")]
    for name in public:
        assert not name.endswith("_report"), f"public {name} is a report method"
        assert not name.endswith("_value_report"), f"public {name} is a value_report method"


def test_update_first_arg_is_component_id_not_selector():
    params = list(inspect.signature(ContextTree.update).parameters)

    assert params[1] == "component_id"
