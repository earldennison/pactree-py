"""ContextTree.select wraps native queries.

Rust owns selector parsing, coordinate resolution, the SelectedNode DTO shape,
and the archived/deleted visibility filter. Python only wraps the returned DTOs
into ergonomic component objects via the mechanical ``_component_from_node``
mapper. These tests assert that:

* selecting ``tool_call`` / ``tool_result`` nodes yields ToolCall / ToolResult
  wrappers carrying the native protocol fields, and
* archived/deleted nodes are excluded by default and only surfaced when
  ``include_archived=True`` is requested.

"""

from __future__ import annotations

import inspect

import pactree
from pactree import ContextTree, Placement
from pactree.components import ReasoningContent, TextContent, ToolCall, ToolResult


def _tool_pair_context() -> ContextTree:
    ctx = ContextTree()
    ctx.receive(
        [
            ToolCall(
                tool_call_id="call_alpha",
                tool_name="shell_execute",
                parameters={"command": "pwd"},
                key="surface:shell",
            )
        ]
    )
    ctx.add_user_batch(
        [
            ToolResult(
                tool_call_id="call_alpha",
                tool_name="shell_execute",
                success=True,
                content="ready",
                key="surface:shell",
            )
        ]
    )
    ctx.send(fail_on_orphan_tool_calls=False)
    return ctx


def test_select_tool_call_returns_toolcall_wrapper_with_native_fields() -> None:
    ctx = _tool_pair_context()

    selected = ctx.select(".tool_call")

    assert len(selected) == 1
    call = selected[0]
    assert call.__class__.__name__ == "ToolCallView"
    assert call.tool_call_id == "call_alpha"
    assert call.tool_name == "shell_execute"
    assert call.parameters == {"command": "pwd"}
    assert call.key == "surface:shell"


def test_select_tool_result_returns_toolresult_wrapper_with_native_fields() -> None:
    ctx = _tool_pair_context()

    selected = ctx.select(".tool_result")

    assert len(selected) == 1
    result = selected[0]
    assert result.__class__.__name__ == "ToolResultView"
    assert result.tool_call_id == "call_alpha"
    assert result.tool_name == "shell_execute"
    assert result.success is True
    assert result.result == "ready"
    assert result.key == "surface:shell"


def test_select_text_content_returns_textcontent_wrapper() -> None:
    ctx = ContextTree()
    ctx.insert(TextContent(content="system surface"), Placement(-1, 0))

    selected = ctx.select(".text_content")

    assert [node.__class__.__name__ for node in selected] == ["TextContentView"]
    assert selected[0].content == "system surface"


def test_select_id_selector_resolves_concrete_node() -> None:
    ctx = _tool_pair_context()
    call_id = ctx.select(".tool_call")[0].id

    selected = ctx.select(f"#{call_id}")

    assert [node.id for node in selected] == [call_id]


def test_select_excludes_archived_nodes_by_default() -> None:
    ctx = ContextTree()
    ctx.insert(TextContent(content="visible"), Placement(-1, 0))


    ctx.insert(
        TextContent(content="archived", aux={"archived": True}),
        Placement(-1, 1),
    )

    default = ctx.select(".text_content")

    assert [node.content for node in default] == ["visible"]


def test_select_includes_archived_nodes_when_requested() -> None:
    ctx = ContextTree()
    ctx.insert(TextContent(content="visible"), Placement(-1, 0))
    ctx.insert(
        TextContent(content="archived", aux={"archived": True}),
        Placement(-1, 1),
    )

    everything = ctx.select(".text_content", include_archived=True)

    assert sorted(node.content for node in everything) == ["archived", "visible"]


def test_select_excludes_deleted_nodes_by_default_but_include_archived_surfaces_them() -> None:
    ctx = ContextTree()
    ctx.insert(TextContent(content="kept"), Placement(-1, 0))
    ctx.insert(
        TextContent(content="tombstoned", aux={"deleted": True}),
        Placement(-1, 1),
    )

    assert [node.content for node in ctx.select(".text_content")] == ["kept"]
    assert sorted(
        node.content for node in ctx.select(".text_content", include_archived=True)
    ) == ["kept", "tombstoned"]


def test_select_unknown_node_type_falls_back_to_pactnode() -> None:
    ctx = ContextTree()
    ctx.insert(ReasoningContent(content="thinking"), Placement(-1, 0))

    selected = ctx.select(".reasoning_content")

    assert [node.__class__.__name__ for node in selected] == ["ReasoningContentView"]
    assert selected[0].content == "thinking"


def test_context_select_delegates_to_native_resolver_without_python_fallback() -> None:
    source = inspect.getsource(ContextTree.select)



    assert "ContextTreeCore.select" in source
    assert "preview_view" not in source
    assert "_component_from_node" not in source
    assert "select_pact_subtrees" not in source
    assert "+tool_call" not in source
    assert "+tool_result" not in source


def test_native_select_pact_subtrees_wrapper_is_removed() -> None:


    import importlib.util

    assert importlib.util.find_spec("pactree._native") is None


def test_projected_objects_use_component_view_vocabulary() -> None:



    ctx = _tool_pair_context()
    selected = ctx.select(".tool_call") + ctx.select(".tool_result")
    assert selected, "expected selected component views"
    for obj in selected:
        name = type(obj).__name__
        assert name.endswith("View"), f"projected object {name!r} is not a *View"
        for forbidden in ("Record", "NodeMeta", "NodeRecord"):
            assert forbidden not in name, f"projected class {name!r} leaks {forbidden!r}"

    view = ctx.view()
    assert type(view).__name__ == "PactView"
