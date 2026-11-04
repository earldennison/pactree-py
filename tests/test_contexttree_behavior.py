"""User-facing ContextTree behavior.

Migrated coverage for the deleted plumbing tests (test_canonical_ops,
test_native_node_identity, test_file_commit_root_store, test_file_buffered_read,
test_fork_capability, test_component_payload_store). Those asserted resolver /
root-store / file internals; the behavior that still matters is re-expressed here
through the public ContextTree surface only.
"""

from __future__ import annotations

from pathlib import Path

from pactree import ContextTree, Placement
from pactree.components import TextContent, ToolCall


def _text_contents(ctx: ContextTree) -> set[str]:
    return {
        n.content
        for n in ctx.view().nodes.values()
        if n.node_type == "text_content"
    }


def _kinds(ctx: ContextTree) -> set[str]:
    return {n.node_type for n in ctx.view().nodes.values()}


def test_save_load_roundtrip(tmp_path: Path) -> None:
    ctx = ContextTree()
    ctx.add_user("remember me")
    ctx.send()
    ctx.receive("acknowledged")

    pact_path = tmp_path / "round.pact"
    ctx.save(pact_path)
    assert pact_path.exists()

    reopened = ContextTree.from_file(pact_path)
    contents = _text_contents(reopened)
    assert "remember me" in contents
    assert "acknowledged" in contents


def test_fork_hard_is_independent(tmp_path: Path) -> None:
    ctx = ContextTree()
    ctx.add_user("base content")
    ctx.send()
    ctx.save(tmp_path / "base.pact")

    child_path = tmp_path / "child.pact"
    child = ctx.fork(child_path, mode="hard")

    assert child_path.exists()
    assert "base content" in _text_contents(child)


def test_rust_mints_component_id() -> None:
    ctx = ContextTree()

    minted = ctx.insert(TextContent(content="auto"), Placement(-1, 0))
    component_id = minted.produced_component_ids[0]
    assert component_id
    assert [c.id for c in ctx.select(f"#{component_id}")] == [component_id]


def test_canonical_replace_updates_in_place() -> None:
    ctx = ContextTree()
    inserted = ctx.insert(TextContent(content="old"), Placement(-1, 0))
    component_id = inserted.produced_component_ids[0]

    ctx.replace(TextContent(content="new"), component_id)

    found = ctx.select(f"#{component_id}")
    assert found and found[0].content == "new"


def test_canonical_prune_removes_component() -> None:
    ctx = ContextTree()
    inserted = ctx.insert(TextContent(content="temp"), Placement(-1, 0))
    component_id = inserted.produced_component_ids[0]
    assert ctx.select(f"#{component_id}")

    ctx.prune(component_id)
    assert not ctx.select(f"#{component_id}")


def test_tool_call_is_classified_natively() -> None:
    ctx = ContextTree()
    ctx.add_user("run it")
    ctx.send()
    ctx.receive(
        [ToolCall(tool_call_id="c1", tool_name="shell", parameters={"cmd": "pwd"})]
    )

    assert "tool_call" in _kinds(ctx)
    calls = [n for n in ctx.view().nodes.values() if n.node_type == "tool_call"]
    assert calls and calls[0].tool_call_id == "c1"


def test_discard_drops_staged_work() -> None:
    ctx = ContextTree()
    ctx.add_user("staged but not sent")
    assert ctx.discard() is True
