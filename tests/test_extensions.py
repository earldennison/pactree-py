from __future__ import annotations

import pytest

import pactree


def _record(record_id: str, path: str) -> dict:
    return {
        "schema": "pactree.fs.change.v1",
        "kind": "pactree.fs.change",
        "record_id": record_id,
        "payload": {"path": path, "operation": "write"},
        "content_refs": [f"sha256:{record_id}"],
        "external_refs": [],
        "index_keys": [f"path:{path}"],
    }


def test_extension_records_share_a_transaction_and_use_bounded_reads(tmp_path):
    path = tmp_path / "extensions.pact"
    tree = pactree.ContextTree.with_id("extension-python-context")
    tree.save(path)
    tree.stage_extensions([_record("change-1", "one.txt"), _record("change-2", "two.txt")])

    commit = tree.seal_staged("adhoc")
    handle = pactree.open_handle(path)
    page = handle.extensions(kind="pactree.fs.change", limit=1)

    assert commit["revision"] == 1
    assert page["schema"] == "pact.extension.page.v1"
    assert [row["record"]["record_id"] for row in page["rows"]] == ["change-2"]
    assert page["has_before"] is True
    assert "extensions" in handle.info()["capabilities"]


def test_extension_cursor_backreads_every_record_without_overlap(tmp_path):
    path = tmp_path / "extension-pages.pact"
    tree = pactree.ContextTree.with_id("extension-python-pages")
    tree.save(path)
    for index in range(5):
        tree.stage_extensions([_record(f"change-{index}", f"{index}.txt")])
        tree.seal_staged("adhoc")

    handle = pactree.open_handle(path)
    newest = handle.extensions(kind="pactree.fs.change", limit=2)
    older = handle.extensions(
        kind="pactree.fs.change",
        cursor=newest["before_cursor"],
        direction="before",
        limit=3,
    )

    newest_ids = [row["record"]["record_id"] for row in newest["rows"]]
    older_ids = [row["record"]["record_id"] for row in older["rows"]]
    assert newest_ids == ["change-3", "change-4"]
    assert older_ids == ["change-0", "change-1", "change-2"]
    assert set(newest_ids).isdisjoint(older_ids)


def test_extension_duplicate_ids_remain_invalid_through_python(tmp_path):
    path = tmp_path / "extension-duplicates.pact"
    tree = pactree.ContextTree.with_id("extension-python-duplicates")
    tree.save(path)
    tree.stage_extensions([_record("duplicate", "one.txt")])
    tree.seal_staged("adhoc")

    with pytest.raises(pactree.PactInvalidInput, match="duplicate"):
        tree.stage_extensions([_record("duplicate", "two.txt")])


def test_extension_secondary_index_and_record_lookup_are_bounded(tmp_path):
    path = tmp_path / "extension-keyed.pact"
    tree = pactree.ContextTree.with_id("extension-python-keyed")
    tree.save(path)
    for record_id, record_path in [("a-1", "/a"), ("b-1", "/b"), ("a-2", "/a")]:
        tree.stage_extensions([_record(record_id, record_path)])
        tree.seal_staged("adhoc")

    handle = pactree.open_handle(path)
    page = handle.extensions(
        kind="pactree.fs.change",
        index_key="path:/a",
        limit=10,
    )

    assert [row["record"]["record_id"] for row in page["rows"]] == ["a-1", "a-2"]
    assert page["index_key"] == "path:/a"
    assert handle.extension("b-1")["record"]["payload"]["path"] == "/b"


def test_standalone_extension_writer_avoids_contexttree_reopen(tmp_path, monkeypatch):
    path = tmp_path / "extension-writer.pact"
    tree = pactree.ContextTree.with_id("extension-python-writer")
    tree.user("provider-visible")
    tree.send()
    tree.save(path)

    def fail_from_file(*_args, **_kwargs):
        raise AssertionError("extension writer must not hydrate through ContextTree.from_file")

    monkeypatch.setattr(pactree.ContextTree, "from_file", fail_from_file)
    writer = pactree.open_extension_writer(path)
    commit = writer.append([_record("writer-change", "/writer.txt")])

    assert commit["revision"] == 2
    assert pactree.open_handle(path).extension("writer-change")["record"]["record_id"] == "writer-change"
    assert "provider-visible" in str(pactree.open_handle(path).project())
