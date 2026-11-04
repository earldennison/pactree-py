"""Rust owns canonical message construction — verified through ContextTree.

Python forwards `role` + `parts` to Rust; Rust builds the message turn, types
each child (text/reasoning/tool_call/tool_result), assigns native ids, and owns
ordering + tool-pair retention. These tests exercise the public behavior
through `ContextTree` and its `view()`.
"""

from __future__ import annotations

import pactree
from pactree.components.core import ReasoningContent, TextContent, ToolCall


def _nodes_by_type(ctx: "pactree.ContextTree", node_type: str) -> list:
    return [n for n in ctx.view().nodes.values() if n.node_type == node_type]


def _kinds(ctx: "pactree.ContextTree") -> set[str]:
    return {n.node_type for n in ctx.view().nodes.values()}


def test_user_text_becomes_a_text_content_node() -> None:
    ctx = pactree.ContextTree()
    ctx.add_user("hello")
    ctx.send()
    contents = {n.content for n in _nodes_by_type(ctx, "text_content")}
    assert "hello" in contents


def test_assistant_reasoning_and_text_are_natively_classified() -> None:
    ctx = pactree.ContextTree()
    ctx.add_user("q")
    ctx.send()
    ctx.receive([ReasoningContent(content="thinking"), TextContent(content="answer")])
    assert {"reasoning_content", "text_content"} <= _kinds(ctx)
    assert "thinking" in {n.content for n in _nodes_by_type(ctx, "reasoning_content")}
    assert "answer" in {n.content for n in _nodes_by_type(ctx, "text_content")}


def test_assistant_tool_call_is_classified_with_native_ids() -> None:
    ctx = pactree.ContextTree()
    ctx.add_user("q")
    ctx.send()
    ctx.receive(
        [ToolCall(tool_call_id="c1", tool_name="shell", parameters={"cmd": "pwd"})]
    )
    calls = _nodes_by_type(ctx, "tool_call")
    assert len(calls) == 1
    assert calls[0].tool_call_id == "c1"
    assert calls[0].tool_name == "shell"

    call_id = next(
        nid for nid, n in ctx.view().nodes.items() if n.node_type == "tool_call"
    )
    assert call_id


def test_dict_and_component_input_lanes_classify_identically() -> None:


    via_component = pactree.ContextTree()
    via_component.add_user("q")
    via_component.send()
    via_component.receive([TextContent(content="answer")])

    via_dict = pactree.ContextTree()
    via_dict.add_user("q")
    via_dict.send()
    via_dict.receive([{"componentType": "text_content", "content": "answer"}])

    assert "answer" in {n.content for n in _nodes_by_type(via_component, "text_content")}
    assert "answer" in {n.content for n in _nodes_by_type(via_dict, "text_content")}
