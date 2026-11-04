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
    descriptor = ExtensionDescriptor(
        name="fixture",
        authority="derived",
        fork_strategy="follow",
        record_prefixes=("fixture.",),
        factory=lambda context, _dependency: observed.append(_Extension(context))
        or observed[-1],
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
        "payload": {},
        "index_keys": [],
    }


def test_pre_seal_failure_aborts_once_and_advances_nothing(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "pre-seal.pact"
    tree = pactree.ContextTree.with_id("pre-seal-owner")
    tree.save(path)
    observed: list[_Extension] = []
    _install(monkeypatch, observed)
    aborted: list[str] = []

    with bind_context_tree(tree, extensions=["fixture"]) as host:
        host.view_for("lane-a")
        batch = observed[0].context.transaction.prepare(
            [_record("never-sealed")],
            operation_key="operation-pre-seal",
            abort=lambda: aborted.append("abort"),
        )
        monkeypatch.setattr(
            tree,
            "seal_staged",
            lambda _boundary: (_ for _ in ()).throw(RuntimeError("seal failed")),
        )
        with pytest.raises(ExtensionTransactionError, match="before canonical seal"):
            host.seal_prepared([batch], boundary="adhoc")

    assert aborted == ["abort"]
    assert pactree.open_handle(path).info()["revision"] == 0
    with pytest.raises(pactree.PactNotFound):
        pactree.open_handle(path).extension("never-sealed")


def test_host_close_aborts_unsealed_prepared_batch_exactly_once(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "close.pact"
    tree = pactree.ContextTree.with_id("close-owner")
    tree.save(path)
    observed: list[_Extension] = []
    _install(monkeypatch, observed)
    aborted: list[str] = []

    host = bind_context_tree(tree, extensions=["fixture"])
    host.view_for("lane-a")
    batch = observed[0].context.transaction.prepare(
        [_record("aborted-on-close")],
        abort=lambda: aborted.append("abort"),
    )
    host.close()
    host.close()
    batch.abort()

    assert aborted == ["abort"]
    assert pactree.open_handle(path).info()["revision"] == 0


def test_post_seal_callback_failure_keeps_one_readable_idempotent_commit(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "post-seal.pact"
    tree = pactree.ContextTree.with_id("post-seal-owner")
    tree.save(path)
    observed: list[_Extension] = []
    _install(monkeypatch, observed)

    with bind_context_tree(tree, extensions=["fixture"]) as host:
        host.view_for("lane-a")
        batch = observed[0].context.transaction.prepare(
            [_record("committed-before-callback-failure")],
            operation_key="post-seal-operation",
            finalize=lambda _commit: (_ for _ in ()).throw(
                RuntimeError("materializer failed")
            ),
        )
        with pytest.raises(ExtensionTransactionError, match="finalization"):
            host.seal_prepared([batch], boundary="adhoc")

        assert batch.state == "committed"
        commit = host.seal_prepared([batch], boundary="adhoc")
        assert len(observed[0].context.commit_stream.after(0)) == 1
        assert commit["revision"] == 1

    with bind_context_tree(tree, extensions=["fixture"]) as recovered:
        view = recovered.view_for("lane-a")
        row = view.extension_record("committed-before-callback-failure")
        assert row["revision"] == 1


def test_canonical_head_reconciles_one_missed_wake(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "reconcile.pact"
    tree = pactree.ContextTree.with_id("reconcile-owner")
    tree.save(path)
    observed: list[_Extension] = []
    _install(monkeypatch, observed)

    with bind_context_tree(tree, extensions=["fixture"]) as host:
        host.view_for("lane-a")
        writer = pactree.open_extension_writer(path)
        writer.append([_record("external-commit")])

        event = host.reconcile()
        assert event is not None
        assert event.revision == 1
        assert host.reconcile() is None
        assert len(observed[0].context.commit_stream.after(0)) == 1
