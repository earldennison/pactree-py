"""Private, versioned local protocol for long-lived extension session hosts."""

from __future__ import annotations

import array
import json
import os
import socket
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


RUNTIME_PROTOCOL_VERSION = 1
MAX_RUNTIME_FRAME_BYTES = 1024 * 1024
MAX_RUNTIME_FDS = 8


class RuntimeProtocolError(RuntimeError):
    """Typed, redacted failure returned by the private runtime sidecar."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    """Stable caller identity; display names, paths, and thread aliases are invalid."""

    authority: str
    principal_id: str
    session_id: str

    def __post_init__(self) -> None:
        if not _authority(self.authority):
            raise ValueError("runtime identity authority is invalid")
        for label, value in (
            ("principal", self.principal_id),
            ("session", self.session_id),
        ):
            if not _stable_identifier(value):
                raise ValueError(
                    f"runtime identity {label} must be a stable opaque identifier"
                )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RuntimeIdentity":
        if set(value) != {"authority", "principal_id", "session_id"}:
            raise ValueError("runtime identity contains aliases or unknown fields")
        return cls(
            authority=str(value["authority"]),
            principal_id=str(value["principal_id"]),
            session_id=str(value["session_id"]),
        )


@dataclass(frozen=True, slots=True)
class RuntimeScopeRef:
    scope_id: str
    identity_digest: str
    server_generation: str
    extension_generation: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RuntimeScopeRef":
        return cls(
            scope_id=str(value["scope_id"]),
            identity_digest=str(value["identity_digest"]),
            server_generation=str(value["server_generation"]),
            extension_generation=str(value["extension_generation"]),
        )


@dataclass(frozen=True, slots=True)
class RuntimeCaptureRef:
    scope_id: str
    extension: str
    capture_id: str
    operation_key: str
    server_generation: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RuntimeCaptureRef":
        return cls(**{field: str(value[field]) for field in cls.__dataclass_fields__})


@dataclass(frozen=True, slots=True)
class PreparedCaptureRef:
    scope_id: str
    extension: str
    prepared_id: str
    operation_key: str
    content_hash: str
    server_generation: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PreparedCaptureRef":
        return cls(**{field: str(value[field]) for field in cls.__dataclass_fields__})


class ExtensionRuntimeClient:
    """Short-lived-request client; no capability or secret is persisted."""

    def __init__(
        self,
        *,
        socket_path: str | Path,
        capability: str,
        server_generation: str,
        timeout: float = 10.0,
    ) -> None:
        self.socket_path = Path(socket_path)
        self._capability = capability
        self.server_generation = server_generation
        self.timeout = timeout

    def probe(self) -> Mapping[str, Any]:
        return self._result_only(self._call("probe", {}))

    def open_or_resume(
        self,
        identity: RuntimeIdentity,
        *,
        runtime_generation: str,
    ) -> RuntimeScopeRef:
        if not _stable_identifier(runtime_generation):
            raise ValueError("runtime generation must be a stable opaque identifier")
        result = self._result_only(self._call(
            "open_or_resume",
            {
                "identity": identity.to_dict(),
                "runtime_generation": runtime_generation,
            },
        ))
        return RuntimeScopeRef.from_dict(result)

    def scope_resolve(self, scope: RuntimeScopeRef) -> Mapping[str, Any]:
        return self._scope_call("scope_resolve", scope, {})[0]

    def capture_begin(
        self,
        scope: RuntimeScopeRef,
        *,
        extension: str,
        operation_key: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeCaptureRef:
        if not _stable_identifier(operation_key, minimum=3):
            raise ValueError("operation key is invalid")
        result = self._result_only(self._scope_call(
            "capture_begin",
            scope,
            {
                "extension": extension,
                "operation_key": operation_key,
                "metadata": dict(metadata or {}),
            },
        ))
        return RuntimeCaptureRef.from_dict(result)

    def capture_prepare(
        self,
        scope: RuntimeScopeRef,
        capture: RuntimeCaptureRef,
        *,
        terminal_evidence: Mapping[str, Any],
    ) -> PreparedCaptureRef:
        self._require_generation(capture.server_generation)
        result = self._result_only(self._scope_call(
            "capture_prepare",
            scope,
            {
                "capture": asdict(capture),
                "terminal_evidence": dict(terminal_evidence),
            },
        ))
        return PreparedCaptureRef.from_dict(result)

    def prepared_status(
        self,
        scope: RuntimeScopeRef,
        prepared: PreparedCaptureRef,
    ) -> Mapping[str, Any]:
        self._require_generation(prepared.server_generation)
        return self._result_only(self._scope_call(
            "prepared_status",
            scope,
            {"prepared": asdict(prepared)},
        ))

    def seal_prepared(
        self,
        scope: RuntimeScopeRef,
        prepared: PreparedCaptureRef,
        *,
        owner_records: Sequence[Mapping[str, Any]],
        idempotency_key: str,
        boundary: str = "adhoc",
    ) -> Mapping[str, Any]:
        self._require_generation(prepared.server_generation)
        if not _stable_identifier(idempotency_key, minimum=3):
            raise ValueError("seal idempotency key is invalid")
        return self._result_only(self._scope_call(
            "seal_prepared",
            scope,
            {
                "prepared": asdict(prepared),
                "owner_records": [dict(record) for record in owner_records],
                "idempotency_key": idempotency_key,
                "boundary": boundary,
            },
        ))

    def abort_prepared(
        self,
        scope: RuntimeScopeRef,
        prepared: PreparedCaptureRef,
    ) -> Mapping[str, Any]:
        self._require_generation(prepared.server_generation)
        return self._result_only(self._scope_call(
            "abort_prepared",
            scope,
            {"prepared": asdict(prepared)},
        ))

    def namespace_fds(
        self,
        scope: RuntimeScopeRef,
        *,
        extension: str,
    ) -> tuple[Mapping[str, Any], tuple[int, ...]]:
        return self._scope_call(
            "namespace_fds",
            scope,
            {"extension": extension},
        )

    def recover(self, scope: RuntimeScopeRef, *, extension: str) -> Mapping[str, Any]:
        return self._result_only(self._scope_call(
            "recover",
            scope,
            {"extension": extension},
        ))

    def terminate(self, scope: RuntimeScopeRef, *, extension: str) -> Mapping[str, Any]:
        return self._result_only(self._scope_call(
            "terminate",
            scope,
            {"extension": extension},
        ))

    def close_scope(self, scope: RuntimeScopeRef) -> Mapping[str, Any]:
        return self._result_only(self._scope_call("close", scope, {}))

    @staticmethod
    def _result_only(
        response: tuple[Mapping[str, Any], tuple[int, ...]],
    ) -> Mapping[str, Any]:
        result, fds = response
        if fds:
            _close_fds(fds)
            raise RuntimeProtocolError(
                "UNEXPECTED_FDS",
                "runtime returned descriptors for a non-descriptor operation",
            )
        return result

    def _scope_call(
        self,
        operation: str,
        scope: RuntimeScopeRef,
        payload: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], tuple[int, ...]]:
        self._require_generation(scope.server_generation)
        return self._call(
            operation,
            {"scope": asdict(scope), **dict(payload)},
        )

    def _require_generation(self, generation: str) -> None:
        if generation != self.server_generation:
            raise RuntimeProtocolError(
                "STALE_GENERATION",
                "runtime reference belongs to a stale server generation",
            )

    def _call(
        self,
        operation: str,
        payload: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], tuple[int, ...]]:
        request_id = uuid.uuid4().hex
        request = {
            "version": RUNTIME_PROTOCOL_VERSION,
            "server_generation": self.server_generation,
            "capability": self._capability,
            "request_id": request_id,
            "operation": operation,
            "payload": dict(payload),
        }
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
            connection.settimeout(self.timeout)
            connection.connect(str(self.socket_path))
            _send_packet(connection, request)
            response, fds = _recv_packet(connection)
        if response.get("request_id") != request_id:
            _close_fds(fds)
            raise RuntimeProtocolError("MALFORMED_RESPONSE", "response id mismatch")
        if not response.get("ok"):
            _close_fds(fds)
            error = response.get("error") or {}
            raise RuntimeProtocolError(
                str(error.get("code") or "RUNTIME_ERROR"),
                str(error.get("message") or "runtime request failed"),
            )
        result = response.get("result")
        if not isinstance(result, Mapping):
            _close_fds(fds)
            raise RuntimeProtocolError("MALFORMED_RESPONSE", "result is not an object")
        return dict(result), fds


def _send_packet(
    connection: socket.socket,
    payload: Mapping[str, Any],
    *,
    fds: Sequence[int] = (),
) -> None:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    if len(encoded) > MAX_RUNTIME_FRAME_BYTES:
        raise RuntimeProtocolError("FRAME_TOO_LARGE", "runtime frame exceeds limit")
    ancillary = []
    if fds:
        if len(fds) > MAX_RUNTIME_FDS:
            raise RuntimeProtocolError("TOO_MANY_FDS", "runtime fd count exceeds limit")
        ancillary.append(
            (socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds).tobytes())
        )
    connection.sendmsg([encoded], ancillary)


def _recv_packet(
    connection: socket.socket,
) -> tuple[dict[str, Any], tuple[int, ...]]:
    payload, ancillary, flags, _address = connection.recvmsg(
        MAX_RUNTIME_FRAME_BYTES + 1,
        socket.CMSG_SPACE(MAX_RUNTIME_FDS * array.array("i").itemsize),
    )
    if flags & socket.MSG_TRUNC or len(payload) > MAX_RUNTIME_FRAME_BYTES:
        raise RuntimeProtocolError("FRAME_TOO_LARGE", "runtime frame exceeds limit")
    if not payload:
        raise RuntimeProtocolError("EMPTY_FRAME", "runtime peer closed the connection")
    received: list[int] = []
    for level, kind, data in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            values = array.array("i")
            values.frombytes(data[: len(data) - (len(data) % values.itemsize)])
            received.extend(values)
    if len(received) > MAX_RUNTIME_FDS:
        _close_fds(received)
        raise RuntimeProtocolError("TOO_MANY_FDS", "runtime fd count exceeds limit")
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        _close_fds(received)
        raise RuntimeProtocolError("INVALID_JSON", "runtime frame is invalid") from error
    if not isinstance(decoded, dict):
        _close_fds(received)
        raise RuntimeProtocolError("INVALID_FRAME", "runtime frame is not an object")
    return decoded, tuple(received)


def _close_fds(fds: Sequence[int]) -> None:
    for fd in fds:
        try:
            os.close(fd)
        except OSError:
            pass


def _authority(value: str) -> bool:
    return (
        1 <= len(value) <= 128
        and value[0].isalpha()
        and all(character.isalnum() or character in "._-" for character in value)
    )


def _stable_identifier(value: str, *, minimum: int = 16) -> bool:
    return (
        minimum <= len(value) <= 256
        and not any(character.isspace() for character in value)
        and "/" not in value
        and "\\" not in value
        and value not in {".", ".."}
    )


__all__ = [
    "ExtensionRuntimeClient",
    "MAX_RUNTIME_FDS",
    "MAX_RUNTIME_FRAME_BYTES",
    "PreparedCaptureRef",
    "RUNTIME_PROTOCOL_VERSION",
    "RuntimeCaptureRef",
    "RuntimeIdentity",
    "RuntimeProtocolError",
    "RuntimeScopeRef",
]
