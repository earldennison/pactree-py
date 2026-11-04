from __future__ import annotations

import subprocess
import sys
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


def _install(monkeypatch, descriptor: ExtensionDescriptor) -> None:
    entry_points = _EntryPoints((_EntryPoint(descriptor),))
    monkeypatch.setattr(
        extension_host.importlib.metadata,
        "entry_points",
        lambda: entry_points,
    )


@dataclass
class _Extension:
    context: Any

    def activate(self):
        return self

    def close(self) -> None:
        return None


def _descriptor(observed: list[_Extension]) -> ExtensionDescriptor:
    def factory(context, _dependency):
        instance = _Extension(context)
        observed.append(instance)
        return instance

    return ExtensionDescriptor(
        name="fixture",
        authority="derived",
        fork_strategy="follow",
        record_prefixes=("fixture.",),
        factory=factory,
    )


def _record(record_id: str) -> dict[str, Any]:
    return {
        "schema": "fixture.event.v1",
        "kind": "fixture.event",
        "record_id": record_id,
        "payload": {"ok": True},
        "index_keys": [],
    }


def test_embedded_batch_co_seals_with_owner_context_and_publishes_once(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "embedded.pact"
    tree = pactree.ContextTree.with_id("embedded-owner")
    tree.save(path)
    observed: list[_Extension] = []
    _install(monkeypatch, _descriptor(observed))

    with bind_context_tree(tree, extensions=["fixture"]) as host:
        view = host.view_for("lane-a")
        assert observed[0].context.transaction.owner_controlled is True
        finalized: list[int] = []
        batch = observed[0].context.transaction.prepare(
            [_record("co-sealed")],
            operation_key="operation-1",
            finalize=lambda commit: finalized.append(int(commit["revision"])),
        )
        tree.add_user("owner context fact")
        commit = host.seal_prepared([batch], boundary="adhoc")

        assert observed[0].context.transaction._coordinator.writer_opened is False
        assert finalized == [commit["revision"]]
        assert len(observed[0].context.commit_stream.after(0)) == 1
        assert view.snapshot.context.revision == 0

    handle = pactree.open_handle(path)
    row = handle.extension("co-sealed")
    assert row["revision"] == commit["revision"]
    assert "owner context fact" in str(handle.project())


def test_standalone_commit_delegates_through_prepared_batch(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "standalone.pact"
    pactree.ContextTree.with_id("standalone-owner").save(path)
    observed: list[_Extension] = []
    _install(monkeypatch, _descriptor(observed))

    with pactree.open(path, extensions=["fixture"]):
        assert observed[0].context.transaction.owner_controlled is False
        commit = observed[0].context.transaction.commit([_record("standalone")])

    assert commit["revision"] == 1
    assert pactree.open_handle(path).extension("standalone")["revision"] == 1


def test_embedded_owner_excludes_a_standalone_extension_writer(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "exclusive.pact"
    tree = pactree.ContextTree.with_id("exclusive-owner")
    tree.save(path)
    observed: list[_Extension] = []
    _install(monkeypatch, _descriptor(observed))

    host = bind_context_tree(tree, extensions=["fixture"])
    host.view_for("lane-a")
    standalone = pactree.open(path, extensions=["fixture"])
    standalone_transaction = observed[1].context.transaction

    with pytest.raises(ExtensionTransactionError, match="writer lease"):
        standalone_transaction.commit([_record("blocked")])

    host.close()
    commit = standalone_transaction.commit([_record("after-release")])
    standalone.close()

    assert commit["revision"] == 1
    assert pactree.open_handle(path).extension("after-release")["revision"] == 1


def test_embedded_writer_lease_excludes_another_process(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "cross-process.pact"
    tree = pactree.ContextTree.with_id("cross-process-owner")
    tree.save(path)
    observed: list[_Extension] = []
    _install(monkeypatch, _descriptor(observed))
    probe = """
import sys
from pathlib import Path
from pactree.extensions import ExtensionTransactionError
from pactree.extensions.services import WriterLease

lease = WriterLease(Path(sys.argv[1]), "main")
try:
    lease.acquire()
except ExtensionTransactionError:
    print("blocked")
else:
    print("acquired")
    lease.release()
"""

    host = bind_context_tree(tree, extensions=["fixture"])
    blocked = subprocess.run(
        [sys.executable, "-c", probe, str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert blocked.stdout.strip() == "blocked"

    host.close()
    acquired = subprocess.run(
        [sys.executable, "-c", probe, str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert acquired.stdout.strip() == "acquired"
