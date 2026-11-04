from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pactree
from pactree.extensions import ExtensionDescriptor, bind_context_tree
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
class _Probe:
    context: Any

    def activate(self):
        return self

    def close(self) -> None:
        return None


def _install(monkeypatch, observed: list[_Probe]) -> None:
    descriptor = ExtensionDescriptor(
        name="probe",
        authority="derived",
        fork_strategy="follow",
        record_prefixes=("probe.",),
        factory=lambda context, _dependency: observed.append(_Probe(context))
        or observed[-1],
    )
    entry_points = _EntryPoints((_EntryPoint(descriptor),))
    monkeypatch.setattr(
        extension_host.importlib.metadata,
        "entry_points",
        lambda: entry_points,
    )


def test_scope_views_share_writer_but_keep_namespaces_and_generations_isolated(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "scopes.pact"
    tree = pactree.ContextTree.with_id("scope-owner")
    tree.save(path)
    observed: list[_Probe] = []
    _install(monkeypatch, observed)

    with bind_context_tree(tree, extensions=["probe"]) as host:
        lane_a = host.view_for("lane-a")
        lane_b = host.view_for("lane-b")
        a_context = lane_a.probe.context
        b_context = lane_b.probe.context

        assert a_context.scope_id != b_context.scope_id
        assert a_context.data_dir != b_context.data_dir
        assert a_context.transaction._coordinator is b_context.transaction._coordinator
        old_a_snapshot = lane_a.snapshot
        old_b_snapshot = lane_b.snapshot

        pactree.open_extension_writer(path).append(
            [
                {
                    "schema": "probe.event.v1",
                    "kind": "probe.event",
                    "record_id": "new-head",
                    "payload": {},
                    "index_keys": [],
                }
            ]
        )
        refreshed_a = lane_a.refresh()

        assert refreshed_a.snapshot.context.revision == 1
        assert lane_a.snapshot == old_a_snapshot
        assert lane_b.snapshot == old_b_snapshot
        assert lane_a.read_handle.info()["revision"] == 0
        assert lane_b.read_handle.info()["revision"] == 0
        lane_a.close()
        assert lane_b.extension_records(kind="probe.event", limit=10)["rows"] == []
        assert host.view_for("lane-c").snapshot.context.revision == 1


def test_closing_a_scope_view_does_not_close_the_host(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "scope-close.pact"
    tree = pactree.ContextTree.with_id("scope-close-owner")
    tree.save(path)
    observed: list[_Probe] = []
    _install(monkeypatch, observed)

    host = bind_context_tree(tree, extensions=["probe"])
    first = host.view_for("lane-a")
    first.close()
    second = host.view_for("lane-b")
    assert second.snapshot.context.revision == 0
    host.close()
