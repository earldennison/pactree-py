from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import pactree
from pactree.extensions import (
    ExtensionDescriptor,
    ExtensionTransactionError,
    bind_context_tree,
)
from pactree.extensions import host as extension_host


class _EntryPoint:
    def __init__(self, descriptor: ExtensionDescriptor):
        self.name = descriptor.name
        self._descriptor = descriptor

    def load(self):
        return lambda: self._descriptor


class _EntryPoints(tuple):
    def select(self, *, group: str):
        assert group == "pactree.extensions"
        return self


@dataclass
class _Extension:
    context: Any

    def activate(self):
        return self

    def close(self) -> None:
        return None


def _install(monkeypatch, observed: list[_Extension]) -> None:
    def factory(context, _dependency):
        instance = _Extension(context)
        observed.append(instance)
        return instance

    descriptor = ExtensionDescriptor(
        name="fixture",
        authority="derived",
        fork_strategy="follow",
        record_prefixes=("fixture.",),
        factory=factory,
    )
    entry_points = _EntryPoints((_EntryPoint(descriptor),))
    monkeypatch.setattr(
        extension_host.importlib.metadata,
        "entry_points",
        lambda: entry_points,
    )


def _record(record_id: str) -> dict[str, Any]:
    return {
        "schema": "fixture.event.v1",
        "kind": "fixture.event",
        "record_id": record_id,
        "payload": {"ok": True},
        "index_keys": [],
    }


def test_receive_prepared_co_seals_postresponse_and_extension_once(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "embedded-receive.pact"
    tree = pactree.ContextTree.with_id("embedded-receive-owner")
    tree.save(path)
    tree.add_user("question")
    tree.send()
    observed: list[_Extension] = []
    _install(monkeypatch, observed)

    with bind_context_tree(tree, extensions=["fixture"]) as host:
        host.view_for("lane-a")
        finalized: list[int] = []
        batch = observed[0].context.transaction.prepare(
            [_record("co-received")],
            operation_key="operation-1",
            finalize=lambda commit: finalized.append(int(commit["revision"])),
        )

        commit = host.receive_prepared([batch], contents=["answer"])
        repeated = host.receive_prepared(
            [batch],
            contents=["must not be received twice"],
        )

        assert batch.state == "committed"
        assert repeated == commit
        assert finalized == [commit["revision"]]
        assert len(observed[0].context.commit_stream.after(0)) == 1

    handle = pactree.open_handle(path)
    assert handle.info()["revision"] == commit["revision"]
    assert handle.extension("co-received")["revision"] == commit["revision"]
    assert "answer" in str(handle.project())
    assert "must not be received twice" not in str(handle.project())


def test_receive_prepared_aborts_and_discards_when_owner_receive_fails(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "embedded-receive-failure.pact"
    tree = pactree.ContextTree.with_id("embedded-receive-failure-owner")
    tree.save(path)
    observed: list[_Extension] = []
    _install(monkeypatch, observed)

    with bind_context_tree(tree, extensions=["fixture"]) as host:
        host.view_for("lane-a")
        batch = observed[0].context.transaction.prepare(
            [_record("must-abort")],
            operation_key="operation-1",
        )

        def fail_receive(_tree, _contents=None):
            raise RuntimeError("receive failed")

        monkeypatch.setattr(pactree.ContextTree, "receive", fail_receive)
        with pytest.raises(
            ExtensionTransactionError,
            match="failed before PostResponse seal",
        ):
            host.receive_prepared([batch], contents=["answer"])

        assert batch.state == "aborted"
        assert tree.traces() == []

    assert pactree.open_handle(path).info()["revision"] == 0
