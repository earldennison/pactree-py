from __future__ import annotations

import pytest

import pactree


def _saved_messages(tmp_path, count: int = 5):
    path = tmp_path / "message_surface.pact"
    tree = pactree.ContextTree.with_id("py_message_surface")
    tree.save(path)
    for index in range(count):
        tree.user(f"message {index} " * 80)
        tree.send()
    return tree, path


def test_message_surface_returns_bounded_hydrated_turns(tmp_path):
    _, path = _saved_messages(tmp_path)
    handle = pactree.open_handle(path)

    page = handle.stream(mode="message_surface", direction="before", limit=2)

    assert page["schema"] == "pact.stream.v1"
    assert page["mode"] == "message_surface"
    assert page["source"] == "held_tree"
    assert page["position"]["total_rows"] == 5
    seqs = [row["seq"] for row in page["rows"]]
    assert seqs == [3, 4]
    assert seqs == [row["cursor"]["seq"] for row in page["rows"]]
    assert all(
        row["cursor"]["revision"] == page["head"]["revision"]
        for row in page["rows"]
    )
    assert all(row["row_id"] == f"node:{row['component_id']}" for row in page["rows"])
    assert page["has_before"] is True
    assert page["has_after"] is False
    assert page["rows"][0]["payload"]["componentType"] == "message_turn"
    assert "message 3" in page["rows"][0]["payload"]["children"][0]["content"]


def test_message_surface_after_cursor_reads_only_new_rows_after_refresh(tmp_path):
    tree, path = _saved_messages(tmp_path, 2)
    handle = pactree.open_handle(path)
    cursor = handle.stream(mode="message_surface", limit=2)["cursors"]["after"]
    tree.user("new message")
    tree.send()
    handle.refresh()

    delta = handle.stream(
        mode="message_surface",
        anchor_row_id=cursor["row_id"],
        anchor_seq=cursor["seq"],
        direction="after",
        limit=80,
    )

    assert len(delta["rows"]) == 1
    assert delta["rows"][0]["payload"]["children"][0]["content"] == "new message"
    assert cursor["seq"] < delta["rows"][0]["seq"]
    assert delta["rows"][0]["seq"] == delta["rows"][0]["cursor"]["seq"]
    assert delta["has_after"] is False


def test_message_surface_rejects_missing_and_stale_anchors(tmp_path):
    _, path = _saved_messages(tmp_path, 3)
    handle = pactree.open_handle(path)
    row = handle.stream(mode="message_surface", direction="after", limit=1)["rows"][0]

    with pytest.raises(pactree.PactNotFound):
        handle.stream(
            mode="message_surface",
            anchor_row_id="node:missing",
            direction="after",
            limit=1,
        )

    with pytest.raises(pactree.PactInvalidInput, match="stale message stream anchor"):
        handle.stream(
            mode="message_surface",
            anchor_row_id=row["row_id"],
            anchor_seq=row["seq"] + 1,
            direction="after",
            limit=1,
        )


def test_message_surface_cursor_rejects_delete_before_anchor(tmp_path):
    tree, path = _saved_messages(tmp_path, 5)
    handle = pactree.open_handle(path)
    initial = handle.stream(mode="message_surface", direction="before", limit=10)
    anchor_row = initial["rows"][2]
    cursor = anchor_row["cursor"]

    tree.prune(f"#{initial['rows'][0]['component_id']}")
    tree.send()
    handle.refresh()

    with pytest.raises(pactree.PactInvalidInput, match="stale message stream anchor"):
        handle.stream(
            mode="message_surface",
            anchor_row_id=cursor["row_id"],
            anchor_seq=cursor["seq"],
            direction="after",
            limit=10,
        )

    after_delete = handle.stream(mode="message_surface", direction="before", limit=10)
    rebased_anchor = next(
        row for row in after_delete["rows"] if row["row_id"] == cursor["row_id"]
    )
    assert rebased_anchor["seq"] != cursor["seq"]
