from __future__ import annotations

import pytest

import pactree
from pactree import file as pf
from pactree import pactree_engine
from pactree import ContextTree, Placement
from pactree.components import TextContent


def _saved_tree(tmp_path, *, content: str = "before"):
    path = tmp_path / "session.pact"
    ctx = ContextTree.with_id("rh2_ctx")
    ctx.save(path)
    trace = ctx.insert(TextContent(content=content), Placement(-1, 0))
    component_id = trace.produced_component_ids[0]
    ctx.send()
    return ctx, path, component_id


def _saved_stream_tree(tmp_path, *messages: str):
    path = tmp_path / "stream_session.pact"
    ctx = ContextTree.with_id("rh3_py_stream_ctx")
    ctx.save(path)
    for message in messages:
        ctx.user(message)
        ctx.send()
    return ctx, path


def _text_component_id(ctx: ContextTree, content: str) -> str:
    return [
        node.id
        for node in ctx.view().nodes.values()
        if getattr(node, "content", None) == content
    ][0]


def test_open_handle_reads_components_without_path_reread(tmp_path):
    _, path, component_id = _saved_tree(tmp_path)
    handle = pf.open_handle(path)
    info = handle.info()
    path.rename(path.with_suffix(".renamed"))

    component = handle.component(component_id)
    projection = handle.project()

    assert component["id"] == component_id
    assert component["content"] == "before"
    assert info["branch_id"] == "main"
    assert info["revision"] == 1
    assert "component" in info["capabilities"]
    assert "stream" in info["capabilities"]
    assert "rewind_targets" in info["capabilities"]
    assert projection["branch"]["head_revision"] == 1


def test_open_handle_reads_content_payload_without_path_reread(tmp_path):
    _, path, component_id = _saved_tree(tmp_path, content="payload " * 300)
    handle = pf.open_handle(path)
    component_before_rename = handle.component(component_id)
    content_id = component_before_rename["content_ref"]["id"]
    path.rename(path.with_suffix(".renamed"))

    payload = handle.content_payload(content_id)

    assert payload["content_id"] == content_id
    assert payload["text"] == component_before_rename["content"]


def test_open_handle_snapshot_refresh_and_context_manager(tmp_path):
    ctx, path, component_id = _saved_tree(tmp_path)
    handle = pactree.open_handle(path)

    ctx.insert(TextContent(content="after"), Placement(-1, 0))
    ctx.send()
    after_id = [
        node.id
        for node in ctx.view().nodes.values()
        if getattr(node, "content", None) == "after"
    ][0]

    assert handle.component(component_id)["content"] == "before"
    with pytest.raises(pactree.PactNotFound):
        handle.component(after_id)

    handle.refresh()
    assert handle.component(after_id)["content"] == "after"

    with pactree.open_handle(path) as scoped:
        assert scoped.component(component_id)["content"] == "before"
    assert scoped.closed is True
    scoped.close()
    assert scoped.closed is True
    with pytest.raises(pactree.PactError):
        scoped.component(component_id)


def test_open_handle_traces_expose_transaction_identity(tmp_path):
    _, path, _ = _saved_tree(tmp_path)

    traces = pactree.open_handle(path).traces()

    assert traces
    assert traces[0]["branch_id"] == "main"
    assert traces[0]["transaction_id"]


def test_open_handle_stream_returns_cursor_windows(tmp_path):
    _, path = _saved_stream_tree(tmp_path, "first", "second", "third")
    handle = pf.open_handle(path)

    tail = handle.stream(limit=2)
    assert tail["schema"] == "pact.stream.v1"
    assert tail["mode"] == "current_surface"
    assert tail["source"] == "held_tree"
    assert tail["fallback_used"] is False
    assert tail["position"]["total_rows"] == 4
    assert [row["seq"] for row in tail["rows"]] == [2, 3]
    assert tail["has_before"] is True
    assert tail["has_after"] is False

    first_row_id = handle.stream(direction="after", limit=10)["rows"][0]["row_id"]
    after_first = handle.stream(
        anchor_row_id=first_row_id,
        direction="after",
        limit=1,
    )
    assert [row["seq"] for row in after_first["rows"]] == [1]
    assert after_first["cursors"]["before"]["row_id"] == after_first["rows"][0]["row_id"]

    around_middle = handle.stream(anchor_seq=1, direction="around", limit=3)
    assert [row["seq"] for row in around_middle["rows"]] == [0, 1, 2]

    clamped = handle.stream(limit=0)
    assert len(clamped["rows"]) == 1


def test_open_handle_stream_supports_live_alias_and_bad_mode(tmp_path):
    _, path = _saved_stream_tree(tmp_path, "first", "second")
    handle = pactree.open_handle(path)

    live = handle.stream(mode="live-surface", direction="after", limit=10)
    assert live["mode"] == "activity_surface"
    assert live["position"]["total_rows"] >= 2

    with pytest.raises(pactree.PactInvalidInput) as exc:
        handle.stream(mode="wrong-mode")
    assert "live_surface" in str(exc.value)


def test_open_handle_stream_snapshot_refresh_and_close(tmp_path):
    ctx, path = _saved_stream_tree(tmp_path, "before")
    handle = pactree.open_handle(path)

    ctx.user("after")
    ctx.send()

    assert handle.stream(direction="after", limit=10)["position"]["total_rows"] == 2
    handle.refresh()
    assert handle.stream(direction="after", limit=10)["position"]["total_rows"] == 3

    handle.close()
    with pytest.raises(pactree.PactError):
        handle.stream()


def test_open_handle_stream_composes_overlay_child(tmp_path):
    base_path = tmp_path / "stream_base.pact"
    child_path = tmp_path / "stream_child.pact"
    base_ctx = ContextTree.with_id("rh3_stream_overlay_base")
    base_ctx.save(base_path)
    base_ctx.user("RH3_PY_BASE_MARKER")
    base_ctx.send()
    base_component_id = _text_component_id(base_ctx, "RH3_PY_BASE_MARKER")

    base_core = pactree_engine.ContextTree.open_file(str(base_path))
    base_core.fork(str(base_path), str(child_path), "overlay", None)

    stream = pf.open_handle(child_path).stream(direction="after", limit=10)
    assert any(
        base_component_id in row.get("payload", {}).get("child_ids", [])
        for row in stream["rows"]
    )


def test_open_handle_project_matches_one_shot_open(tmp_path):
    _, path, _ = _saved_tree(tmp_path)

    assert pf.open_handle(path).project() == pf.open(path)


def test_open_handle_project_composes_overlay_child(tmp_path):
    base_path = tmp_path / "base.pact"
    child_path = tmp_path / "child.pact"
    base_ctx = ContextTree.with_id("rh2_overlay_base")
    base_ctx.save(base_path)
    base_ctx.insert(TextContent(content="RH2_BASE_MARKER"), Placement(-1, 0))
    base_ctx.send()
    base_component_id = _text_component_id(base_ctx, "RH2_BASE_MARKER")

    base_core = pactree_engine.ContextTree.open_file(str(base_path))
    base_core.fork(str(base_path), str(child_path), "overlay", None)



    assert "RH2_BASE_MARKER" not in str(pf.open(child_path))

    handle = pf.open_handle(child_path)
    assert "RH2_BASE_MARKER" in str(handle.project())
    assert handle.component(base_component_id)["content"] == "RH2_BASE_MARKER"


def test_open_handle_project_revision_matches_one_shot_open(tmp_path):
    path = tmp_path / "revision_session.pact"
    ctx = pactree_engine.ContextTree.with_id("rh4_py_revision_ctx")
    ctx.save_file(str(path))
    trace = ctx.insert(
        {"id": "rh4_item", "componentType": "text_content", "content": "a" * 300},
        "root",
    )
    component_id = trace["produced_component_ids"][0]
    ctx.send()
    ctx.update(component_id, content="b" * 300)
    ctx.send()
    ctx.insert(
        {"id": "rh4_tail", "componentType": "text_content", "content": "tail"},
        "root",
    )
    ctx.send()
    handle = pf.open_handle(path)
    head_revision = pf.open(path)["revision"]

    for revision in (0, 2, head_revision):
        handle_projection = handle.project(revision=revision)
        one_shot_projection = pf.open(path, revision=revision)
        assert handle_projection == one_shot_projection
        if revision == 2:
            assert "b" * 300 in str(handle_projection)

    with pytest.raises(pactree.PactInvalidInput) as exc:
        handle.project(revision=head_revision + 1)
    assert "after branch main head" in str(exc.value)


def test_open_handle_project_revision_rules_out_overlay_child(tmp_path):
    base_path = tmp_path / "revision_base.pact"
    child_path = tmp_path / "revision_child.pact"
    base_ctx = ContextTree.with_id("rh4_revision_overlay_base")
    base_ctx.save(base_path)
    base_ctx.insert(TextContent(content="RH4_REVISION_BASE"), Placement(-1, 0))
    base_ctx.send()

    base_core = pactree_engine.ContextTree.open_file(str(base_path))
    base_core.fork(str(base_path), str(child_path), "overlay", None)

    with pytest.raises(pactree.PactError) as exc:
        pf.open_handle(child_path).project(revision=0)
    assert "at-revision projection not supported on overlay handles yet" in str(exc.value)


def test_open_handle_rewind_targets_follow_message_order(tmp_path):
    path = tmp_path / "rewind_targets.pact"
    ctx = ContextTree.with_id("rh4_py_targets_ctx")
    ctx.save(path)
    ctx.user("first user")
    ctx.send()
    ctx.receive("first assistant")
    ctx.user("second user")
    ctx.send()

    envelope = pf.open_handle(path).rewind_targets()

    assert envelope["schema"] == "pact.rewind_targets.v1"
    assert envelope["branch_id"] == "main"
    assert envelope["head_revision"] == pf.open(path)["revision"]
    targets = envelope["targets"]
    assert [target["role"] for target in targets] == ["user", "assistant", "user"]
    assert [target["order"] for target in targets] == [0, 1, 2]

    first_user = targets[0]
    assert first_user["message_id"]
    assert first_user["transaction_id"]
    assert first_user["from_revision"] == 0
    assert first_user["to_revision"] == 1
    assert first_user["before_ref"]["revision"] == 0
    assert first_user["at_ref"]["revision"] == 1
    assert first_user["at_ref"]["branch_id"] == "main"

    first_assistant = targets[1]
    assert first_assistant["from_revision"] == 1
    assert first_assistant["to_revision"] == 2

    handle = pf.open_handle(path)
    handle.close()
    with pytest.raises(pactree.PactError):
        handle.rewind_targets()


def test_open_head_fast_matches_open_head_metadata(tmp_path):
    _, path, _ = _saved_tree(tmp_path)

    fast = pactree.open_head_fast(path)
    module_fast = pf.open_head_fast(path)
    opened = pf.open(path)

    assert module_fast == fast
    assert fast["schema"] == "pact.fast_head.v1"
    assert fast["head"]["branch_id"] == opened["branch_id"]
    assert fast["head"]["head_revision"] == opened["revision"]
    assert fast["branch_head"]["head_revision"] == opened["revision"]


def test_batch_open_head_fast_matches_single_call_and_inlines_errors(tmp_path):
    valid_paths = []
    for index, content in enumerate(["batch a", "batch b", "batch c"], start=1):
        session_dir = tmp_path / f"batch_{index}"
        session_dir.mkdir()
        _, path, _ = _saved_tree(session_dir, content=content)
        valid_paths.append(path)
    missing = tmp_path / "missing.pact"
    corrupt = tmp_path / "corrupt.pact"
    corrupt.write_bytes(b"PACTPKV1truncated")
    paths = [valid_paths[0], missing, valid_paths[1], corrupt, valid_paths[2]]

    batch = pactree.batch_open_head_fast(paths)
    module_batch = pf.batch_open_head_fast(paths)

    assert module_batch == batch
    assert getattr(pf, "batch_open_head_fast", None) is not None
    assert len(batch) == len(paths)
    assert [entry["path"] for entry in batch] == [str(path) for path in paths]

    for entry, path in [
        (batch[0], valid_paths[0]),
        (batch[2], valid_paths[1]),
        (batch[4], valid_paths[2]),
    ]:
        assert entry["ok"] is True
        assert entry["head"] == pactree.open_head_fast(path)

    for entry in [batch[1], batch[3]]:
        assert entry["ok"] is False
        assert isinstance(entry["error"], str)
        assert entry["error"]

    assert pactree.batch_open_head_fast([]) == []
    duplicate = pf.batch_open_head_fast([valid_paths[0], valid_paths[0]])
    assert len(duplicate) == 2
    assert duplicate[0]["ok"] is True
    assert duplicate[1]["ok"] is True
    assert duplicate[0]["path"] == duplicate[1]["path"] == str(valid_paths[0])
    assert (
        duplicate[0]["head"]
        == duplicate[1]["head"]
        == pactree.open_head_fast(valid_paths[0])
    )


def test_read_handle_missing_ids_raise_typed_errors(tmp_path):
    _, path, _ = _saved_tree(tmp_path)
    handle = pactree.open_handle(path)

    with pytest.raises(pactree.PactNotFound) as missing_component:
        handle.component("missing_component_rh2")
    assert "missing_component_rh2" in str(missing_component.value)

    with pytest.raises(pactree.PactNotFound) as missing_content:
        handle.content_payload("missing_content_rh2")
    assert "missing_content_rh2" in str(missing_content.value)


def test_read_handle_surface_is_read_only(tmp_path):
    _, path, _ = _saved_tree(tmp_path)
    handle = pf.open_handle(path)

    for mutation in ("insert", "append", "update", "replace", "upsert", "prune", "save_file"):
        assert not hasattr(handle, mutation), f"read handle exposes mutation {mutation}"
