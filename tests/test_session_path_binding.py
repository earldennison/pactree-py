"""File lifecycle through ContextTree.

The host owns the session `.pact` path and passes it to `ContextTree.save(path)`;
`ContextTree.from_file(path)` reopens it. pactree-py binds exactly the path it
is given (no derived session-directory layout), and Rust owns file creation and
persistence.
"""

from __future__ import annotations

from pathlib import Path

from pactree import Placement
from pactree.components import TextContent, ToolCall
from pactree.context import ContextTree


def test_save_persists_and_reopens(tmp_path: Path) -> None:
    pact_path = tmp_path / "host" / "owned" / "place.pact"
    ctx = ContextTree()
    ctx.save(pact_path)

    ctx.add_user("hello from a host-owned path")
    ctx.send()
    assert pact_path.exists()

    reopened = ContextTree.from_file(pact_path)
    assert "hello from a host-owned path" in str(reopened.preview())


def test_save_binds_exact_host_path(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit.pact"
    ctx = ContextTree()
    ctx.save(explicit)


    assert ctx._pact_file_path == explicit
    assert explicit.exists()


def test_send_recreates_bound_pact_file(tmp_path: Path) -> None:
    pact_path = tmp_path / "host" / "owned" / "place.pact"
    ctx = ContextTree()
    ctx.save(pact_path)
    pact_path.unlink()

    ctx.send()

    assert pact_path.exists()


def test_receive_recreates_bound_pact_file(tmp_path: Path) -> None:
    pact_path = tmp_path / "host" / "owned" / "place.pact"
    ctx = ContextTree()
    ctx.save(pact_path)
    pact_path.unlink()

    ctx.receive()

    assert pact_path.exists()


def test_append_tool_call_keeps_existing_message_turn() -> None:
    ctx = ContextTree()
    result = ctx.receive([TextContent(content="thinking first")])
    message_id = result.metadata["produced_message_ids"][0]

    ctx.append(
        ToolCall(
            tool_call_id="call_1",
            tool_name="shell_execute",
            parameters={"command": "pwd"},
        ),
        Placement(message_id),
    )

    assistant_turns = [
        node for node in ctx.preview()["children"]
        if node.get("role") == "assistant"
    ]

    assert len(assistant_turns) == 1
    assert [
        child.get("componentType") or child.get("type")
        for child in assistant_turns[0]["children"]
    ] == ["text_content", "tool_call"]
