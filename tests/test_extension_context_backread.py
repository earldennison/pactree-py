from pathlib import Path

import pactree
from pactree.extensions import bind_context_tree


def test_scope_context_stream_pages_the_pinned_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "context-backread.pact"
    tree = pactree.ContextTree.with_id("context-backread")
    tree.save(path)
    for index in range(5):
        tree.user(f"message {index}")
        tree.send()

    with bind_context_tree(tree) as host:
        view = host.view_for("lane-a")
        pinned = view.context.stream(direction="before", limit=2)
        cursor = pinned["cursors"]["before"]
        older = view.context.stream(
            anchor_row_id=cursor["row_id"],
            anchor_seq=cursor["seq"],
            direction="before",
            limit=10,
        )

        assert [row["seq"] for row in pinned["rows"]] == [3, 4]
        assert [row["seq"] for row in older["rows"]] == [0, 1, 2]

        tree.user("message after pin")
        tree.send()
        assert all(
            "message after pin" not in str(row["payload"])
            for row in view.context.stream(direction="before", limit=10)["rows"]
        )
