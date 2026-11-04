from __future__ import annotations

import json
import os
import socket
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import pactree
from pactree.extensions import ExtensionDescriptor
from pactree.extensions import host as extension_host
from pactree.extensions import runtime_server as runtime_server_module
from pactree.extensions.runtime_protocol import (
    ExtensionRuntimeClient,
    RuntimeIdentity,
    RuntimeProtocolError,
    RuntimeScopeRef,
)
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
class _RuntimeExtension:
    context: Any
    finalized: list[str]
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
                    "payload": {"terminal": dict(terminal_evidence)},
                    "index_keys": [],
                }
            ],
            operation_key=operation_key,
            finalize=lambda _commit: self.finalized.append(operation_key),
            abort=lambda: self.aborted.append(operation_key),
        )


def _install(monkeypatch, observed: list[_RuntimeExtension]) -> None:
    finalized: list[str] = []
    aborted: list[str] = []

    def factory(context, _dependency):
        extension = _RuntimeExtension(context, finalized, aborted)
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


def test_local_runtime_protocol_co_seals_capture_with_owner_records(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "runtime.pact"
    tree = pactree.ContextTree.with_id("runtime-owner")
    tree.save(path)
    observed: list[_RuntimeExtension] = []
    _install(monkeypatch, observed)

    with ExtensionRuntimeServer(
        tree,
        extensions=["fixture"],
        runtime_root=tmp_path / "runtime",
    ) as server:
        assert stat.S_IMODE(server.runtime_root.stat().st_mode) == 0o700
        assert stat.S_IMODE(server.socket_path.stat().st_mode) == 0o600
        client = server.client()
        identity = _identity()
        scope = client.open_or_resume(
            identity,
            runtime_generation=f"runtime-{uuid.uuid4().hex}",
        )
        assert client.open_or_resume(
            identity,
            runtime_generation=f"runtime-{uuid.uuid4().hex}",
        ).scope_id == scope.scope_id

        capture = client.capture_begin(
            scope,
            extension="fixture",
            operation_key="operation-1",
            metadata={"source": "test"},
        )
        prepared = client.capture_prepare(
            scope,
            capture,
            terminal_evidence={"status": "completed"},
        )
        assert client.prepared_status(scope, prepared)["state"] == "prepared"
        commit = client.seal_prepared(
            scope,
            prepared,
            owner_records=[
                {
                    "schema": "owner.event.v1",
                    "kind": "owner.event",
                    "record_id": "owner:operation-1",
                    "payload": {},
                    "index_keys": [],
                }
            ],
            idempotency_key="seal-operation-1",
            boundary="adhoc",
        )
        repeated = client.seal_prepared(
            scope,
            prepared,
            owner_records=[
                {
                    "schema": "owner.event.v1",
                    "kind": "owner.event",
                    "record_id": "owner:operation-1",
                    "payload": {},
                    "index_keys": [],
                }
            ],
            idempotency_key="seal-operation-1",
            boundary="adhoc",
        )

        assert repeated == commit
        assert client.prepared_status(scope, prepared)["state"] == "committed"
        assert observed[0].finalized == ["operation-1"]
        assert client.probe()["peer_uid"] == os.geteuid()

    handle = pactree.open_handle(path)
    assert handle.info()["revision"] == 1
    assert handle.extension("record:operation-1")["revision"] == 1
    assert handle.extension("owner:operation-1")["revision"] == 1


def test_runtime_protocol_rejects_alias_identity_and_stale_capability(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "security.pact"
    tree = pactree.ContextTree.with_id("security-owner")
    tree.save(path)
    observed: list[_RuntimeExtension] = []
    _install(monkeypatch, observed)

    with pytest.raises(ValueError, match="stable"):
        RuntimeIdentity(
            authority="test.runtime",
            principal_id="bob",
            session_id=str(uuid.uuid4()),
        )

    with ExtensionRuntimeServer(
        tree,
        extensions=["fixture"],
        runtime_root=tmp_path / "runtime",
    ) as server:
        stale = server.client(capability="not-the-server-capability")
        with pytest.raises(RuntimeProtocolError, match="authentication"):
            stale.probe()


def test_runtime_protocol_rejects_downgrade_stale_generation_and_scope_forgery(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "protocol-guards.pact"
    tree = pactree.ContextTree.with_id("protocol-guards-owner")
    tree.save(path)
    observed: list[_RuntimeExtension] = []
    _install(monkeypatch, observed)

    with ExtensionRuntimeServer(
        tree,
        extensions=["fixture"],
        runtime_root=tmp_path / "runtime",
    ) as server:
        descriptor = server.connection_descriptor()
        request = {
            "version": 0,
            "server_generation": descriptor["server_generation"],
            "capability": descriptor["capability"],
            "request_id": uuid.uuid4().hex,
            "operation": "probe",
            "payload": {},
        }
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
            connection.connect(str(descriptor["socket_path"]))
            connection.send(json.dumps(request).encode())
            response = json.loads(connection.recv(64 * 1024))
        assert response["error"]["code"] == "PROTOCOL_MISMATCH"

        replay_id = uuid.uuid4().hex
        canonical = {
            **request,
            "version": 1,
            "request_id": replay_id,
        }
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
            connection.connect(str(descriptor["socket_path"]))
            connection.send(json.dumps(canonical).encode())
            assert json.loads(connection.recv(64 * 1024))["ok"] is True
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
            connection.connect(str(descriptor["socket_path"]))
            connection.send(
                json.dumps({**canonical, "operation": "open_or_resume"}).encode()
            )
            replay = json.loads(connection.recv(64 * 1024))
        assert replay["error"]["code"] == "REQUEST_REPLAY_MISMATCH"

        stale = ExtensionRuntimeClient(
            socket_path=descriptor["socket_path"],
            capability=descriptor["capability"],
            server_generation="server-stale-generation",
        )
        with pytest.raises(RuntimeProtocolError, match="stale"):
            stale.probe()

        client = server.client()
        scope = client.open_or_resume(
            _identity(),
            runtime_generation=f"runtime-{uuid.uuid4().hex}",
        )
        forged = RuntimeScopeRef(
            scope_id=scope.scope_id,
            identity_digest="0" * 64,
            server_generation=scope.server_generation,
            extension_generation=scope.extension_generation,
        )
        with pytest.raises(RuntimeProtocolError, match="unknown"):
            client.scope_resolve(forged)

    assert pactree.open_handle(path).info()["revision"] == 0


def test_runtime_protocol_rejects_a_different_os_user_without_mutation(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "peer-guard.pact"
    tree = pactree.ContextTree.with_id("peer-guard-owner")
    tree.save(path)
    observed: list[_RuntimeExtension] = []
    _install(monkeypatch, observed)

    with ExtensionRuntimeServer(
        tree,
        extensions=["fixture"],
        runtime_root=tmp_path / "runtime",
    ) as server:
        monkeypatch.setattr(
            runtime_server_module,
            "_peer_credentials",
            lambda _connection: (os.getpid(), os.geteuid() + 1, os.getegid()),
        )
        with pytest.raises(RuntimeProtocolError, match="peer uid"):
            server.client().probe()

    assert pactree.open_handle(path).info()["revision"] == 0
