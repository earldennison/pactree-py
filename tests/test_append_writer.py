"""Actual native round trips through the bounded append writer binding."""

from __future__ import annotations

from pathlib import Path

import pytest

import pactree


def _message(row_id: str, role: str, text: str, **aux):
    return {
        "row_id": row_id,
        "role": role,
        "parts": [{"componentType": "text_content", "content": text}],
        "aux": aux,
    }


def _batch(writer, batch_id: str, previous, checkpoint, messages, source: str = "lens"):
    return {
        "source": source,
        "batch_id": batch_id,
        "expected": writer.version(),
        "previous_checkpoint": previous,
        "checkpoint": checkpoint,
        "messages": messages,
    }


def _recorded_rows(path: Path, limit: int = 20) -> list[tuple[str, str]]:
    """(role, text) of the newest `limit` message turns through the indexed read handle."""

    handle = pactree.open_handle(path)
    page = handle.stream(mode="message_surface", direction="before", limit=limit)
    rows = []
    for row in page["rows"]:
        payload = row["payload"]
        assert payload["componentType"] == "message_turn"
        text = "|".join(part["content"] for part in payload["children"])
        rows.append((payload["role"], text))
    return rows


def test_capability_and_native_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "recording.pact"
    writer = pactree.open_append_writer(path)
    assert writer.capability == "pact.append.v1"
    assert pactree.PACT_APPEND_CAPABILITY == "pact.append.v1"
    opened = writer.version()
    assert opened["revision"] == 0
    assert len(opened["file_identity"]) == 64

    first = writer.append_batch(
        _batch(
            writer,
            "b1",
            None,
            "cp1",
            [
                _message("r1", "user", "How do I open a file?", when_ms=1),
                _message("r2", "assistant", "Use the read handle."),
            ],
        )
    )
    assert first["status"] == "committed"
    receipt = first["receipt"]
    assert receipt["version"]["revision"] == 1
    assert receipt["row_ids"] == ["r1", "r2"]
    assert receipt["checkpoint"] == "cp1"
    assert len(receipt["request_digest"]) == 64
    assert writer.checkpoint("lens") == "cp1"
    assert writer.lookup("lens", "b1") == receipt
    assert writer.lookup("lens", "missing") is None

    second = writer.append_batch(
        _batch(writer, "b2", "cp1", "cp2", [_message("r3", "user", "second")])
    )
    assert second["receipt"]["version"]["revision"] == 2
    writer.close()

    handle = pactree.open_handle(path)
    assert handle.info()["revision"] == 2
    assert _recorded_rows(path) == [
        ("user", "How do I open a file?"),
        ("assistant", "Use the read handle."),
        ("user", "second"),
    ]


def test_duplicate_receipt_wrong_digest_and_conflict(tmp_path: Path) -> None:
    path = tmp_path / "retry.pact"
    writer = pactree.open_append_writer(path)
    first = _batch(writer, "b1", None, "cp1", [_message("r1", "user", "one")])
    original = writer.append_batch(first)["receipt"]
    writer.append_batch(_batch(writer, "b2", "cp1", "cp2", [_message("r2", "user", "two")]))

    retry = writer.append_batch(first)
    assert retry == {"status": "already_committed", "receipt": original}

    changed = dict(first)
    changed["messages"] = [_message("r1", "user", "one, edited")]
    with pytest.raises(pactree.PactAppendError) as reused:
        writer.append_batch(changed)
    assert reused.value.category == "idempotency_conflict"
    assert reused.value.actual is None

    stale = _batch(writer, "b3", "cp2", "cp3", [_message("r3", "user", "three")])
    stale["expected"] = dict(stale["expected"], revision=1)
    with pytest.raises(pactree.PactAppendError) as conflict:
        writer.append_batch(stale)
    assert conflict.value.category == "conflict"
    assert conflict.value.actual == writer.version()
    assert conflict.value.source is None
    assert isinstance(conflict.value, pactree.PactError)

    with pytest.raises(pactree.PactAppendError) as empty:
        writer.append_batch(_batch(writer, "b4", "cp2", "cp3", []))
    assert empty.value.category == "invalid_input"
    writer.close()


def test_unsupported_semantics_surface_their_own_category(tmp_path: Path) -> None:
    path = tmp_path / "semantics.pact"
    writer = pactree.open_append_writer(path)


    writer.append_batch(_batch(writer, "b0", None, "cp0", [_message("r0", "user", "plain")]))
    with_ttl = _batch(
        writer,
        "b1",
        "cp0",
        "cp1",
        [
            {
                "row_id": "r1",
                "role": "user",
                "parts": [{"componentType": "text_content", "content": "x", "ttl": 2}],
                "aux": {},
            }
        ],
    )
    with pytest.raises(pactree.PactAppendError) as unsupported:
        writer.append_batch(with_ttl)
    assert unsupported.value.category == "unsupported_semantics"
    assert unsupported.value.source is None and unsupported.value.batch_id is None
    writer.close()


_CHILD = """
import os, sys, pactree
writer = pactree.open_append_writer(sys.argv[1])
writer.append_batch({
    "source": "lens", "batch_id": "b2", "expected": writer.version(),
    "previous_checkpoint": "cp1", "checkpoint": "cp2",
    "messages": [{"row_id": "r2", "role": "user",
                  "parts": [{"componentType": "text_content", "content": "two"}], "aux": {}}],
})
os._exit(0)  # the outcome never reaches the caller: no close, no receipt returned
"""


def test_unknown_outcome_reconciles_by_reopen_and_lookup(tmp_path: Path) -> None:
    import subprocess
    import sys

    path = tmp_path / "unknown.pact"
    writer = pactree.open_append_writer(path)
    writer.append_batch(_batch(writer, "b1", None, "cp1", [_message("r1", "user", "one")]))
    writer.close()


    subprocess.run([sys.executable, "-c", _CHILD, str(path)], check=True, timeout=60)

    reopened = pactree.open_append_writer(path)
    receipt = reopened.lookup("lens", "b2")
    assert receipt is not None
    assert receipt["row_ids"] == ["r2"]
    assert receipt["version"]["revision"] == 2
    assert reopened.checkpoint("lens") == "cp2"
    retry = reopened.append_batch(
        {
            "source": "lens",
            "batch_id": "b2",
            "expected": reopened.version(),
            "previous_checkpoint": "cp1",
            "checkpoint": "cp2",
            "messages": [_message("r2", "user", "two")],
        }
    )
    assert retry == {"status": "already_committed", "receipt": receipt}
    reopened.close()
    assert _recorded_rows(path) == [("user", "one"), ("user", "two")]


def test_large_referenced_content_round_trips_through_indexed_reads(tmp_path: Path) -> None:
    path = tmp_path / "large.pact"
    writer = pactree.open_append_writer(path)
    body = "large tool payload line\n" * 8192
    writer.append_batch(_batch(writer, "seed", None, "cp0", [_message("r0", "user", "seed")]))
    outcome = writer.append_batch(
        _batch(
            writer,
            "big",
            "cp0",
            "cp1",
            [
                {
                    "row_id": "r1",
                    "role": "assistant",
                    "parts": [
                        {
                            "componentType": "tool_result",
                            "tool_call_id": "call-1",
                            "tool_name": "Read",
                            "success": True,
                            "content": body,
                        }
                    ],
                    "aux": {},
                }
            ],
        )
    )
    assert outcome["status"] == "committed"
    writer.close()
    assert _recorded_rows(path) == [("user", "seed"), ("assistant", body)]


def test_independent_handles_exclusion_and_close(tmp_path: Path) -> None:
    first = pactree.open_append_writer(tmp_path / "a.pact")
    second = pactree.open_append_writer(tmp_path / "b.pact")
    first.append_batch(_batch(first, "b1", None, "a1", [_message("r1", "user", "a")]))
    second.append_batch(_batch(second, "b1", None, "b1", [_message("r1", "user", "b")]))
    assert first.version()["file_identity"] != second.version()["file_identity"]
    with pytest.raises(pactree.PactAppendError) as contended:
        pactree.open_append_writer(tmp_path / "a.pact")
    assert contended.value.category == "resource_unavailable"

    first.close()
    first.close()
    assert first.closed
    with pytest.raises(pactree.PactInvalidInput):
        first.version()
    with pytest.raises(pactree.PactInvalidInput):
        first.append_batch(_batch(second, "x", None, None, [_message("r", "user", "x")]))
    reopened = pactree.open_append_writer(tmp_path / "a.pact")
    assert reopened.lookup("lens", "b1") is not None
    with reopened as managed:
        assert managed.version()["revision"] == 1
    assert reopened.closed
    second.close()


def test_memory_options_are_validated_exactly(tmp_path: Path) -> None:
    path = tmp_path / "options.pact"
    good = {
        "page_cache_bytes": 0,
        "component_cache_bytes": 0,
        "scratch_cache_bytes": 1 << 20,
        "io_chunk_bytes": 4096,
        "in_flight_bytes": 1 << 20,
    }
    writer = pactree.open_append_writer(path, memory_options=good)
    writer.close()
    for bad in (
        dict(good, io_chunk_bytes=0),
        dict(good, page_cache_bytes=True),
        dict(good, extra=1),
        {key: value for key, value in good.items() if key != "in_flight_bytes"},
        dict(good, in_flight_bytes=-1),
    ):
        with pytest.raises(pactree.PactInvalidInput):
            pactree.open_append_writer(path, memory_options=bad)


def test_derived_depth_writer_ranks_turns_without_recording_depth_ops(tmp_path: Path) -> None:
    path = tmp_path / "derived.pact"
    writer = pactree.open_append_writer(path, derived_depth=True)
    previous = None
    for round_index in range(4):
        checkpoint = f"cp{round_index}"
        outcome = writer.append_batch(
            _batch(
                writer,
                f"b{round_index}",
                previous,
                checkpoint,
                [
                    _message(f"r{round_index}u", "user", f"user {round_index}"),
                    _message(f"r{round_index}a", "assistant", f"assistant {round_index}"),
                ],
            )
        )
        assert outcome["status"] == "committed"
        previous = checkpoint
    writer.close()


    handle = pactree.open_handle(path)
    page = handle.stream(mode="message_surface", direction="before", limit=20)
    rows = page["rows"]
    assert len(rows) == 8
    total = page["position"]["total_rows"]
    for row in rows:
        expected = total - 1 - row["seq"]
        assert row["payload"]["pact_depth"] == expected
        assert all(child["pact_depth"] == expected for child in row["payload"]["children"])
    oldest = handle.component(rows[0]["component_id"])
    assert oldest["pact_depth"] == 7
    assert rows[-1]["payload"]["children"][0]["content"] == "assistant 3"


    ctx = pactree.ContextTree.from_file(path)
    newest = ctx.select("d0")
    assert len(newest) == 1 and newest[0].pact_depth == 0
    assert len(ctx.select("d7")) == 1
    assert ctx.select("d8") == []


    again = pactree.open_append_writer(path)
    outcome = again.append_batch(
        _batch(again, "b4", "cp3", "cp4", [_message("r4u", "user", "user 4")])
    )
    assert outcome["status"] == "committed"
    again.close()
    page = pactree.open_handle(path).stream(mode="message_surface", direction="before", limit=2)
    assert [row["payload"]["pact_depth"] for row in page["rows"]] == [1, 0]


def test_latest_receipt_distinguishes_a_fresh_source_from_a_null_checkpoint(tmp_path: Path) -> None:
    path = tmp_path / "latest.pact"
    writer = pactree.open_append_writer(path)
    assert writer.latest_receipt("lens") is None
    assert writer.checkpoint("lens") is None
    first = writer.append_batch(_batch(writer, "b0", None, None, [_message("r0", "user", "no checkpoint")]))
    assert first["status"] == "committed"
    assert writer.checkpoint("lens") is None
    latest = writer.latest_receipt("lens")
    assert latest == first["receipt"]
    assert latest["checkpoint"] is None and latest["row_ids"] == ["r0"]
    assert writer.latest_receipt("other") is None
    second = writer.append_batch(_batch(writer, "b1", None, "cp1", [_message("r1", "assistant", "with")]))
    assert writer.latest_receipt("lens") == second["receipt"]
    assert writer.checkpoint("lens") == "cp1"
    writer.close()
