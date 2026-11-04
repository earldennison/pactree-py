from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pactree
from pactree.extensions import ExtensionDescriptor
from pactree.extensions import host as extension_host
from pactree.extensions.runtime_protocol import RuntimeIdentity
from pactree.extensions.runtime_server import ExtensionRuntimeServer


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
class _RecoverableExtension:
    context: Any
    aborted: list[str]

    def activate(self):
        return self

    def close(self) -> None:
        return None

    def runtime_capture_begin(self, operation_key, metadata):
        return f"capture:{operation_key}"

    def runtime_capture_prepare(self, capture_id, terminal_evidence):
        operation_key = capture_id.removeprefix("capture:")
        return self.context.transaction.prepare(
            [
                {
                    "schema": "fixture.capture.v1",
                    "kind": "fixture.capture",
                    "record_id": f"record:{operation_key}",
                    "payload": {},
                    "index_keys": [],
                }
            ],
            operation_key=operation_key,
            abort=lambda: self.aborted.append(operation_key),
        )


def _install(monkeypatch, observed: list[_RecoverableExtension]) -> None:
    aborted: list[str] = []

    def factory(context, _dependency):
        extension = _RecoverableExtension(context, aborted)
        observed.append(extension)
        return extension

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


def _identity() -> RuntimeIdentity:
    return RuntimeIdentity(
        authority="test.runtime",
        principal_id=str(uuid.uuid4()),
        session_id=str(uuid.uuid4()),
    )


def test_server_close_aborts_unsealed_capture_once(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "abort.pact"
    tree = pactree.ContextTree.with_id("abort-owner")
    tree.save(path)
    observed: list[_RecoverableExtension] = []
    _install(monkeypatch, observed)

    server = ExtensionRuntimeServer(
        tree,
        extensions=["fixture"],
        runtime_root=tmp_path / "runtime",
    )
    server.start()
    client = server.client()
    scope = client.open_or_resume(
        _identity(),
        runtime_generation=f"runtime-{uuid.uuid4().hex}",
    )
    capture = client.capture_begin(
        scope,
        extension="fixture",
        operation_key="operation-abort",
    )
    client.capture_prepare(
        scope,
        capture,
        terminal_evidence={"status": "cancelled"},
    )
    server.close()
    server.close()

    assert observed[0].aborted == ["operation-abort"]
    assert pactree.open_handle(path).info()["revision"] == 0


def test_request_replay_returns_one_canonical_seal(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "replay.pact"
    tree = pactree.ContextTree.with_id("replay-owner")
    tree.save(path)
    observed: list[_RecoverableExtension] = []
    _install(monkeypatch, observed)

    with ExtensionRuntimeServer(
        tree,
        extensions=["fixture"],
        runtime_root=tmp_path / "runtime",
    ) as server:
        client = server.client()
        scope = client.open_or_resume(
            _identity(),
            runtime_generation=f"runtime-{uuid.uuid4().hex}",
        )
        capture = client.capture_begin(
            scope,
            extension="fixture",
            operation_key="operation-replay",
        )
        prepared = client.capture_prepare(
            scope,
            capture,
            terminal_evidence={"status": "completed"},
        )
        first = client.seal_prepared(
            scope,
            prepared,
            owner_records=[],
            idempotency_key="seal-replay",
        )
        second = client.seal_prepared(
            scope,
            prepared,
            owner_records=[],
            idempotency_key="seal-replay",
        )
        assert second == first

    assert pactree.open_handle(path).info()["revision"] == 1
