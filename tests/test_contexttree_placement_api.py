"""Public Placement API.

Placement is the single placement object for context writes. It only selects the
Rust `From` shape from its arguments; it never resolves selectors, depths, or
component ids -- Pact resolves placement when the mutation runs.
"""

from __future__ import annotations

import pytest

from pactree import ContextTree, Placement
from pactree.components import TextContent


def test_placement_depth_offset_inserts_in_system_lane():
    tree = ContextTree()
    trace = tree.insert(TextContent(content="system"), Placement(-1, 0))
    assert trace.produced_component_ids


def test_placement_component_id_inserts_under_component():
    tree = ContextTree()
    parent = tree.insert(TextContent(content="parent"), Placement(-1, 0))
    parent_id = parent.produced_component_ids[0]
    trace = tree.append(TextContent(content="child"), Placement(parent_id, 0))
    assert trace.produced_component_ids


def test_placement_selector_shape_works():
    tree = ContextTree()

    trace = tree.insert(TextContent(content="x"), Placement("#" + tree.id))
    assert trace.produced_component_ids


def test_placement_depth_string_shape():

    p = Placement("d-1")
    assert "Placement" in repr(p)


def test_placement_rejects_bad_target_types():
    with pytest.raises(Exception):
        Placement(object())
    with pytest.raises(Exception):
        Placement(True)


def test_placement_has_no_target_or_coords_ceremony():
    p = Placement(-1, 0)
    for forbidden in ("coords", "parent", "new", "target", "id"):
        assert not hasattr(p, forbidden), f"Placement exposes {forbidden}"

    import pactree

    assert not hasattr(pactree, "Target")
    assert not hasattr(pactree, "PlacementTarget")
