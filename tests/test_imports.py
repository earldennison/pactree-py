from __future__ import annotations

import inspect
import pkgutil

import pactree
from pactree import ContextTree, Placement
from pactree.components import TextContent, ToolCall, ToolResult

def test_import_surface() -> None:
    assert pactree.__version__ == "0.1.0"
    assert pactree.engine_available() is True
    assert ContextTree.__name__ == "ContextTree"
    assert pactree.ReadHandle.__name__ == "ReadHandle"
    assert callable(pactree.open_handle)


def test_package_has_no_subprocess_binary_bridge() -> None:
    modules = {
        module.name
        for module in pkgutil.iter_modules(pactree.__path__)
        if not module.ispkg
    }

    assert "binary" not in modules


def test_engine_backed_context_tree_smoke() -> None:
    ctx = ContextTree()

    result = ctx.user_message("hello")

    assert result.success is True
    assert ctx.send()["messages"]


def test_context_tree_provider_api_splits_projection_send_and_receive() -> None:
    assert hasattr(ContextTree, "send")
    assert hasattr(ContextTree, "receive")



    assert hasattr(ContextTree, "provider_thread")
    assert not hasattr(ContextTree, "provider_thread_readonly")
    assert not hasattr(ContextTree, "seal")
    assert not hasattr(ContextTree, "pact_seal_boundary")
    assert not hasattr(ContextTree, "add_provider_response")


def test_context_tree_canonical_ops_only_no_deprecated_pact_wrappers() -> None:

    for name in (
        "insert",
        "append",
        "replace",
        "upsert",
        "update",
        "prune",
        "select",
        "traces",
        "discard_trace",
        "discard",
    ):
        assert hasattr(ContextTree, name), f"missing canonical op {name!r}"

    for name in (
        "pact_insert",
        "pact_update",
        "pact_delete",
        "_coordinate_parent_and_offset",
        "_deprecated_insert_target",
        "_canonical_node_selector",
        "_resolve_node_id_if_concrete",
    ):
        assert not hasattr(ContextTree, name), f"deprecated member {name!r} still present"


def test_context_tree_receive_seals_post_response() -> None:
    ctx = ContextTree()

    result = ctx.receive(
        [
            TextContent(content="I need a tool"),
            ToolCall(
                tool_call_id="call_context_response",
                tool_name="shell_execute",
                parameters={"command": "pwd"},
            ),
        ]
    )

    assert result.success is True
    assert result.metadata["commit_ref"]["boundary"] == "PostResponse"
    assert "call_context_response" in str(ctx.preview())


def test_context_message_staging_delegates_tree_construction_to_native_pact() -> None:
    assert not hasattr(ContextTree, "_add_message_batch")
    source = inspect.getsource(ContextTree._stage_message)

    assert "ContextTreeCore.add_message" in source
    assert "\"kind\": \"insert\"" not in source
    assert "message_turn" not in source
    assert "parent_id" not in source


def test_context_delete_bare_node_id_removes_tool_pair_and_empty_turn() -> None:
    ctx = ContextTree()
    ctx.receive(
        [
            ToolCall(
                tool_call_id="call_shell",
                tool_name="shell_execute",
                parameters={"command": "pwd"},
            )
        ]
    )
    ctx.add_user_batch(
        [
            ToolResult(
                tool_call_id="call_shell",
                tool_name="shell_execute",
                success=True,
                content="ok",
            )
        ]
    )
    ctx.send()

    call = ctx.select(".tool_call")[0]
    ctx.prune(call.id)

    assert ctx.select(".tool_call") == []
    assert ctx.select(".tool_result") == []
    assert ctx.select(".message_turn") == []


def test_context_prune_selector_resolves_to_concrete_tool_pair_id() -> None:
    ctx = ContextTree()
    for index in range(2):
        ctx.receive(
            [
                ToolCall(
                    tool_call_id=f"call_{index}",
                    tool_name="shell_execute",
                    parameters={"command": "pwd"},
                    key="surface:shell",
                )
            ]
        )
        ctx.add_user_batch(
            [
                ToolResult(
                    tool_call_id=f"call_{index}",
                    tool_name="shell_execute",
                    success=True,
                    content="ok",
                    key="surface:shell",
                )
            ]
        )
        ctx.send()


    target = ctx.select("{tool_call_id=call_0}")[0]
    ctx.prune(target.id)

    assert [call.tool_call_id for call in ctx.select(".tool_call")] == ["call_1"]
    assert [result.tool_call_id for result in ctx.select(".tool_result")] == ["call_1"]


def test_insert_system_lane_shows_in_provider_thread() -> None:
    ctx = ContextTree()

    trace = ctx.insert(TextContent(content="system surface"), Placement(-1, 0))

    assert trace.produced_component_ids
    assert ctx.send()["messages"][0]["content"][0]["content"] == "system surface"


def test_replace_component_id_updates_existing_surface() -> None:
    ctx = ContextTree()
    trace = ctx.insert(TextContent(content="old"), Placement(-1, 0))
    component_id = trace.produced_component_ids[0]

    ctx.replace(TextContent(content="new"), component_id)

    assert ctx.send()["messages"][0]["content"][0]["content"] == "new"


def test_context_can_reserve_empty_assistant_text_node_and_replace_it() -> None:
    ctx = ContextTree()
    reserved = {
        "componentType": "text_content",
        "content": "",
        "content_status": "streaming",
    }

    result = ctx.receive([reserved])
    node_id = result.metadata["produced_message_child_ids"][0]

    assert node_id
    assert result.metadata["produced_message_ids"]
    assert result.metadata["produced_message_child_ids"] == [node_id]
    assert [node.id for node in ctx.select(f"#{node_id}")] == [node_id]

    ctx.replace(
        {
            "componentType": "text_content",
            "content": "stream complete",
            "content_status": "complete",
        },
        node_id,
    )

    closed = ctx.receive()
    assert closed.success is True
    assert (
        ctx.provider_thread()["messages"][-1]["content"][0]["content"]
        == "stream complete"
    )


def test_upsert_inserts_then_replaces_by_minted_id() -> None:
    ctx = ContextTree()

    inserted = ctx.upsert(
        TextContent(content="old"), component_id=None, placement=Placement(-1, 0)
    )
    component_id = inserted.produced_component_ids[0]
    ctx.upsert(
        TextContent(content="new"), component_id=component_id, placement=Placement(-1, 0)
    )

    assert [node.id for node in ctx.select(f"#{component_id}")] == [component_id]
    assert ctx.send()["messages"][0]["content"][0]["content"] == "new"


def test_selected_tool_components_reconstruct_key_from_dto() -> None:



    ctx = ContextTree()
    ctx.receive(
        [
            ToolCall(
                tool_call_id="call_shell",
                tool_name="shell_execute",
                parameters={"command": "pwd"},
                key="surface:shell",
            )
        ]
    )
    ctx.add_user_batch(
        [
            ToolResult(
                tool_call_id="call_shell",
                tool_name="shell_execute",
                success=True,
                content="ok",
                key="surface:shell",
            )
        ]
    )
    ctx.send()


    assert not hasattr(ctx, "_node_cache")

    assert ctx.select(".tool_call")[0].key == "surface:shell"
    assert ctx.select(".tool_result")[0].key == "surface:shell"


def test_tool_pair_key_metadata_does_not_imply_native_retention() -> None:
    ctx = ContextTree()
    for index in range(3):
        ctx.receive(
            [
                ToolCall(
                    tool_call_id=f"call_{index}",
                    tool_name="shell_execute",
                    parameters={"command": "pwd"},
                    key="tool:shell_execute",
                )
            ]
        )
        ctx.add_user_batch(
            [
                ToolResult(
                    tool_call_id=f"call_{index}",
                    tool_name="shell_execute",
                    success=True,
                    content="ok",
                    key="tool:shell_execute",
                )
            ]
        )
        ctx.send()

    assert not hasattr(ctx, "pact_enforce_replace_retention")
    assert [call.tool_call_id for call in ctx.select(".tool_call")] == [
        "call_0",
        "call_1",
        "call_2",
    ]
    assert [result.tool_call_id for result in ctx.select(".tool_result")] == [
        "call_0",
        "call_1",
        "call_2",
    ]
