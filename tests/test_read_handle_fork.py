from __future__ import annotations

import pactree


def _record(record_id: str) -> dict[str, object]:
    return {
        "kind": "pactree.fs.checkpoint",
        "schema": "pactree.fs.checkpoint.v1",
        "record_id": record_id,
        "payload": {"checkpoint": record_id},
        "external_refs": [],
        "index_keys": ["namespace:test"],
    }


def test_indexed_read_handle_creates_a_pinned_delta_child_without_parent_write(tmp_path):
    parent = tmp_path / "parent.pact"
    child = tmp_path / "child.pact"
    tree = pactree.ContextTree.with_id("read-handle-fork")
    tree.save(parent)
    writer = pactree.open_extension_writer(parent)
    writer.append([_record("parent-checkpoint")])
    before = pactree.open_handle(parent).info()

    report = pactree.open_handle(parent).fork(child, revision=before["revision"])

    after = pactree.open_handle(parent).info()
    child_info = pactree.open_handle(child).info()
    assert after == before
    assert report["mode"] == "delta"
    assert report["base_mode"] == "pinned"
    assert report["created_from_head"]["revision"] == before["revision"]
    assert report["child_path"] == str(child)
    assert child_info["context_tree_id"] != before["context_tree_id"]


def test_extension_queries_are_branch_and_revision_pinned(tmp_path):
    path = tmp_path / "revision-pinned.pact"
    tree = pactree.ContextTree.with_id("revision-pinned")
    tree.save(path)
    writer = pactree.open_extension_writer(path)
    writer.append([_record("old")])
    writer.append([_record("new")])
    handle = pactree.open_handle(path, branch_id="main")

    page = handle.extensions(
        kind="pactree.fs.checkpoint",
        limit=10,
        at_revision=1,
    )

    assert [row["record"]["record_id"] for row in page["rows"]] == ["old"]
    assert handle.extension("old", at_revision=1)["revision"] == 1
    try:
        handle.extension("new", at_revision=1)
    except Exception as error:
        assert "not visible" in str(error)
    else:
        raise AssertionError("future extension was visible in a revision-pinned read")
