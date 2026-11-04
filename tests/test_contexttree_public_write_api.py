"""Public ContextTree write surface.

The six public mutations are insert / append / update / replace / upsert / prune.
Placement carries position; targets are plain component ids; results are
trace-style (`TraceRef`).
"""

from __future__ import annotations

from pactree import ContextTree, Placement, TraceRef
from pactree.components import TextContent


def _tree_with_one():
    tree = ContextTree()
    trace = tree.insert(TextContent(content="first"), Placement(-1, 0))
    return tree, trace.produced_component_ids[0]


def test_insert_returns_trace_with_produced_id():
    tree = ContextTree()
    trace = tree.insert(TextContent(content="hello"), Placement(-1, 0))
    assert isinstance(trace, TraceRef)
    assert trace.trace_id
    assert trace.op_kind == "insert"
    assert trace.produced_component_ids


def test_append_under_component_id():
    tree, cid = _tree_with_one()
    trace = tree.append(TextContent(content="child"), Placement(cid, 0))
    assert isinstance(trace, TraceRef)
    assert trace.produced_component_ids


def test_update_requires_plain_component_id():
    tree, cid = _tree_with_one()
    trace = tree.update(cid, content="changed")
    assert isinstance(trace, TraceRef)
    assert trace.op_kind == "update"


def test_replace_targets_component_id():
    tree, cid = _tree_with_one()
    trace = tree.replace(TextContent(content="replacement"), cid)
    assert isinstance(trace, TraceRef)
    assert trace.op_kind == "replace"


def test_upsert_inserts_when_absent():
    tree = ContextTree()
    trace = tree.upsert(
        TextContent(content="maybe new"),
        component_id=None,
        placement=Placement(-1, 0),
    )
    assert isinstance(trace, TraceRef)
    assert trace.produced_component_ids


def test_prune_deletes_by_component_id():
    tree, cid = _tree_with_one()
    trace = tree.prune(cid)
    assert isinstance(trace, TraceRef)
    assert trace.op_kind == "delete"
    assert trace.deleted_component_ids == [cid] or trace.deleted_component_ids


def test_prune_deletes_by_selector():
    tree = ContextTree()
    tree.insert(TextContent(content="evict me", tags=["evictable"]), Placement(-1, 0))
    keep = tree.insert(TextContent(content="keep me"), Placement(-1, 1))
    keep_id = keep.produced_component_ids[0]

    trace = tree.prune("+evictable")
    assert trace.op_kind == "delete"
    assert len(tree.select("+evictable")) == 0
    assert tree.select(f"#{keep_id}")


def test_trace_exposes_all_id_buckets():
    tree = ContextTree()
    trace = tree.insert(TextContent(content="x"), Placement(-1, 0))

    assert hasattr(trace, "produced_component_ids")
    assert hasattr(trace, "replaced_component_ids")
    assert hasattr(trace, "deleted_component_ids")


def test_local_id_minted_at_construction_resolves_into_context_on_insert():
    tree = ContextTree()
    component = TextContent(content="echo")
    assert component.id
    trace = tree.insert(component, Placement(-1, 0))

    assert trace.produced_component_ids
    assert trace.produced_component_ids[0].endswith(component.id)


def test_trace_ref_serializes_to_dict_and_json():
    tree = ContextTree()
    trace = tree.insert(TextContent(content="hello"), Placement(-1, 0))

    payload = trace.to_dict()
    encoded = trace.to_json()
    restored = TraceRef.from_json(encoded)

    assert payload["trace_id"] == trace.trace_id
    assert "component" not in payload
    assert restored.trace_id == trace.trace_id
    assert restored.op_kind == "insert"
    assert restored.produced_component_ids == trace.produced_component_ids


def test_trace_ref_can_include_serialized_component():
    trace = TraceRef.from_dict(
        {
            "trace_id": "trace_1",
            "op_kind": "insert",
            "created_at_ns": 123,
            "produced_component_ids": ["component:1"],
            "component": {
                "pact_id": "component:1",
                "componentType": "text_content",
                "content": "hello",
            },
        }
    )

    payload = trace.to_dict(include_component=True)

    assert trace.component.id == "component:1"
    assert payload["component"]["pact_id"] == "component:1"
    assert "id" not in payload["component"]
