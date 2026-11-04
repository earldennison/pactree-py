"""Public component authoring through `pactree.components`.

Components are thin authoring proxies that marshal into Rust. Public constructors
do not accept authoritative ids or placement/storage fields -- Pact mints the id
and owns placement when the component enters the tree.
"""

from __future__ import annotations

import pytest

from pactree import ContextTree, Placement
from pactree.components import (
    Component,
    TextContent,
    ReasoningContent,
    ToolCall,
    ToolResult,
)


def test_official_imports_from_components():
    assert TextContent is not None
    assert ReasoningContent is not None
    assert ToolCall is not None
    assert ToolResult is not None
    assert Component is not None


def test_constructor_rejects_authoritative_id():
    with pytest.raises(TypeError):
        TextContent(content="x", id="my_id")


def test_constructor_rejects_placement_and_storage_fields():
    for field in ("parent_id", "offset", "pact_depth", "transaction_index", "content_ref"):
        with pytest.raises(TypeError):
            TextContent(content="x", **{field: 1})


def test_authoring_component_mints_local_id_at_construction():


    assert TextContent(content="x").id


def test_tool_call_and_result_round_trip_into_provider_thread():
    ctx = ContextTree()
    ctx.receive(
        [
            ToolCall(
                tool_call_id="call_1",
                tool_name="shell_execute",
                parameters={"command": "pwd"},
            )
        ]
    )
    ctx.add_user_batch(
        [
            ToolResult(
                tool_call_id="call_1",
                tool_name="shell_execute",
                success=True,
                content="ok",
            )
        ]
    )
    ctx.send(fail_on_orphan_tool_calls=False)

    assert [c.tool_call_id for c in ctx.select(".tool_call")] == ["call_1"]
    assert [r.tool_call_id for r in ctx.select(".tool_result")] == ["call_1"]


def test_context_tree_accepts_component_objects_directly():
    ctx = ContextTree()
    trace = ctx.insert(TextContent(content="direct"), Placement(-1, 0))
    assert trace.produced_component_ids


def test_non_xml_components_reject_attrs():

    from pactree.components import TextContent, ToolCall

    with pytest.raises(TypeError, match="attrs"):
        TextContent("x", attrs={"priority": "high"})
    with pytest.raises(TypeError, match="attrs"):
        ToolCall(tool_call_id="c", tool_name="t", attrs={"k": "v"})


def test_xml_component_still_accepts_attrs():



    from pactree.components import XMLComponent

    block = XMLComponent("body", type="note", attrs={"priority": "high"})
    dumped = block.model_dump()
    assert dumped["componentType"] == "note"
    assert dumped["content"] == "body"

    assert dumped["attrs"]["priority"] == "high"
    assert dumped.get("aux", {}).get("priority") is None


def test_bare_component_is_abstract_and_emits_no_pact_node():


    from pactree.components.core import Component

    with pytest.raises(TypeError):
        Component("x")

    from pactree.components import (
        MessageTurn,
        ReasoningContent,
        TextContent,
        ToolCall,
        ToolResult,
    )

    for cls, kwargs in (
        (TextContent, {"content": "x"}),
        (ReasoningContent, {"content": "x"}),
        (ToolCall, {"tool_call_id": "c", "tool_name": "t"}),
        (ToolResult, {"tool_call_id": "c", "tool_name": "t"}),
        (MessageTurn, {}),
    ):
        payload = cls(**kwargs).model_dump()
        assert payload.get("componentType") != "pact_node"
        assert payload.get("type") != "pact_node"


def test_input_coercion_has_no_to_pact_node_bridge():

    from pathlib import Path

    base_src = (Path(__file__).resolve().parent.parent / "src/pactree/context/base.py").read_text()
    assert "to_pact_node" not in base_src
