"""Authenticated Unix-socket host for private Pactree extension runtimes."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import stat
import struct
import threading
import uuid
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .host import EmbeddedExtensionHost, PactScopeView, bind_context_tree
from .runtime_protocol import (
    ExtensionRuntimeClient,
    PreparedCaptureRef,
    RUNTIME_PROTOCOL_VERSION,
    RuntimeCaptureRef,
    RuntimeIdentity,
    RuntimeProtocolError,
    RuntimeScopeRef,
    _close_fds,
    _recv_packet,
    _send_packet,
)
from .services import PreparedExtensionBatch, StateRoots, stable_token


@dataclass(slots=True)
class _ScopeState:
    identity_digest: str
    view: PactScopeView
    runtime_generations: set[str]


@dataclass(slots=True)
class _CaptureState:
    ref: RuntimeCaptureRef
    surface: Any
    prepared: PreparedCaptureRef | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


@dataclass(slots=True)
class _PreparedState:
    ref: PreparedCaptureRef
    batch: PreparedExtensionBatch
    idempotency_key: str | None = None
    seal_fingerprint: str | None = None
    commit: Mapping[str, Any] | None = None


class ExtensionRuntimeServer:
    """One process-local sidecar endpoint owning one embedded extension host."""

    def __init__(
        self,
        context_tree: Any,
        *,
        extensions: Sequence[str | Mapping[str, Any]],
        runtime_root: str | Path,
        state_roots: StateRoots | None = None,
        max_cached_requests: int = 1024,
        max_scopes: int = 1024,
        max_captures: int = 4096,
    ) -> None:
        self._context_tree = context_tree
        self._extensions = tuple(extensions)
        self._state_roots = state_roots
        self.runtime_root = Path(runtime_root).expanduser().resolve()
        self.socket_path = self.runtime_root / "runtime.sock"
        self.server_generation = f"server-{uuid.uuid4().hex}"
        self._capability = secrets.token_urlsafe(48)
        self._max_cached_requests = max_cached_requests
        self._max_scopes = max_scopes
        self._max_captures = max_captures
        self._host: EmbeddedExtensionHost | None = None
        self._listener: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._handlers: set[threading.Thread] = set()
        self._scopes: dict[str, _ScopeState] = {}
        self._captures: dict[tuple[str, str], _CaptureState] = {}
        self._operations: dict[tuple[str, str, str], RuntimeCaptureRef] = {}
        self._prepared: dict[tuple[str, str], _PreparedState] = {}
        self._seal_results: OrderedDict[
            tuple[str, str], tuple[str, Mapping[str, Any]]
        ] = OrderedDict()
        self._request_cache: OrderedDict[
            str, tuple[str, Mapping[str, Any]]
        ] = OrderedDict()
        self._lock = threading.RLock()
        self._seal_lock = threading.RLock()
        self._stop = threading.Event()
        self._started = False
        self._closed = False
        self._extension_generation = ""

    def start(self) -> "ExtensionRuntimeServer":
        with self._lock:
            if self._closed:
                raise RuntimeError("extension runtime server is closed")
            if self._started:
                return self
            self.runtime_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.runtime_root, 0o700)
            if len(os.fsencode(self.socket_path)) >= 104:
                raise RuntimeError("extension runtime socket path is too long")
            self._remove_stale_socket()
            host = bind_context_tree(
                self._context_tree,
                extensions=self._extensions,
                state_roots=self._state_roots,
            )
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            try:
                listener.bind(str(self.socket_path))
                os.chmod(self.socket_path, 0o600)
                listener.listen(32)
                listener.settimeout(0.2)
            except BaseException:
                listener.close()
                host.close()
                self.socket_path.unlink(missing_ok=True)
                raise
            self._host = host
            self._listener = listener
            self._extension_generation = _extension_generation(host)
            self._started = True
            self._accept_thread = threading.Thread(
                target=self._accept_loop,
                name="pactree-extension-runtime",
                daemon=True,
            )
            self._accept_thread.start()
            return self

    def client(
        self,
        *,
        capability: str | None = None,
        timeout: float = 10.0,
    ) -> ExtensionRuntimeClient:
        self._ensure_started()
        return ExtensionRuntimeClient(
            socket_path=self.socket_path,
            capability=self._capability if capability is None else capability,
            server_generation=self.server_generation,
            timeout=timeout,
        )

    def connection_descriptor(self) -> Mapping[str, Any]:
        """Return the private inherited-channel bootstrap payload."""

        self._ensure_started()
        return {
            "protocol_version": RUNTIME_PROTOCOL_VERSION,
            "socket_path": str(self.socket_path),
            "capability": self._capability,
            "server_generation": self.server_generation,
            "extension_generation": self._extension_generation,
        }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
            listener = self._listener
            self._listener = None
        if listener is not None:
            listener.close()
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=2)
        with self._lock:
            handlers = tuple(self._handlers)
        for handler in handlers:
            handler.join(timeout=2)
        first_error: BaseException | None = None
        if self._host is not None:
            try:
                self._host.close()
            except BaseException as error:
                first_error = error
        self._capability = ""
        self.socket_path.unlink(missing_ok=True)
        try:
            self.runtime_root.rmdir()
        except OSError:
            pass
        if first_error is not None:
            raise first_error

    def __enter__(self) -> "ExtensionRuntimeServer":
        return self.start()

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            listener = self._listener
            if listener is None:
                return
            try:
                connection, _address = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            handler = threading.Thread(
                target=self._handle_connection,
                args=(connection,),
                name="pactree-extension-runtime-request",
                daemon=True,
            )
            with self._lock:
                self._handlers.add(handler)
            handler.start()

    def _handle_connection(self, connection: socket.socket) -> None:
        fds: tuple[int, ...] = ()
        try:
            peer_pid, peer_uid, peer_gid = _peer_credentials(connection)
            request, received_fds = _recv_packet(connection)
            _close_fds(received_fds)
            request_id = str(request.get("request_id") or "")
            try:
                response, fds = self._serve_request(
                    request,
                    peer_pid=peer_pid,
                    peer_uid=peer_uid,
                    peer_gid=peer_gid,
                )
            except RuntimeProtocolError as error:
                response = {
                    "version": RUNTIME_PROTOCOL_VERSION,
                    "request_id": request_id,
                    "ok": False,
                    "error": {"code": error.code, "message": str(error)},
                }
            except BaseException:
                response = {
                    "version": RUNTIME_PROTOCOL_VERSION,
                    "request_id": request_id,
                    "ok": False,
                    "error": {
                        "code": "OPERATION_FAILED",
                        "message": "extension runtime operation failed",
                    },
                }
            _send_packet(connection, response, fds=fds)
        except (OSError, RuntimeProtocolError):
            pass
        finally:
            _close_fds(fds)
            connection.close()
            current = threading.current_thread()
            with self._lock:
                self._handlers.discard(current)

    def _serve_request(
        self,
        request: Mapping[str, Any],
        *,
        peer_pid: int,
        peer_uid: int,
        peer_gid: int,
    ) -> tuple[Mapping[str, Any], tuple[int, ...]]:
        request_id = str(request.get("request_id") or "")
        if not request_id:
            raise RuntimeProtocolError("INVALID_REQUEST", "request id is required")
        fingerprint = _fingerprint_request(request)
        with self._lock:
            cached = self._request_cache.get(request_id)
            if cached is not None:
                prior_fingerprint, response = cached
                if prior_fingerprint != fingerprint:
                    raise RuntimeProtocolError(
                        "REQUEST_REPLAY_MISMATCH",
                        "request id was replayed with different content",
                    )
                self._request_cache.move_to_end(request_id)
                return response, ()
        self._authenticate(request, peer_uid=peer_uid)
        payload = request.get("payload")
        if not isinstance(payload, Mapping):
            raise RuntimeProtocolError("INVALID_REQUEST", "request payload is invalid")
        operation = str(request.get("operation") or "")
        result, fds = self._dispatch(
            operation,
            payload,
            peer_pid=peer_pid,
            peer_uid=peer_uid,
            peer_gid=peer_gid,
        )
        response = {
            "version": RUNTIME_PROTOCOL_VERSION,
            "request_id": request_id,
            "ok": True,
            "result": dict(result),
        }
        if not fds:
            with self._lock:
                self._request_cache[request_id] = (fingerprint, response)
                self._request_cache.move_to_end(request_id)
                while len(self._request_cache) > self._max_cached_requests:
                    self._request_cache.popitem(last=False)
        return response, fds

    def _authenticate(self, request: Mapping[str, Any], *, peer_uid: int) -> None:
        if peer_uid != os.geteuid():
            raise RuntimeProtocolError(
                "PEER_REJECTED",
                "runtime peer uid does not match the server owner",
            )
        if int(request.get("version", -1)) != RUNTIME_PROTOCOL_VERSION:
            raise RuntimeProtocolError(
                "PROTOCOL_MISMATCH",
                "runtime protocol version is not supported",
            )
        if request.get("server_generation") != self.server_generation:
            raise RuntimeProtocolError(
                "STALE_GENERATION",
                "runtime request targets a stale server generation",
            )
        capability = request.get("capability")
        if not isinstance(capability, str) or not hmac.compare_digest(
            capability,
            self._capability,
        ):
            raise RuntimeProtocolError(
                "AUTHENTICATION_FAILED",
                "runtime capability authentication failed",
            )

    def _dispatch(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        peer_pid: int,
        peer_uid: int,
        peer_gid: int,
    ) -> tuple[Mapping[str, Any], tuple[int, ...]]:
        if operation == "probe":
            return (
                {
                    "protocol_version": RUNTIME_PROTOCOL_VERSION,
                    "server_generation": self.server_generation,
                    "extension_generation": self._extension_generation,
                    "peer_pid": peer_pid,
                    "peer_uid": peer_uid,
                    "peer_gid": peer_gid,
                },
                (),
            )
        if operation == "open_or_resume":
            return self._open_or_resume(payload), ()
        scope, state = self._require_scope(payload)
        if operation == "scope_resolve":
            return asdict(scope), ()
        if operation == "capture_begin":
            return self._capture_begin(scope, state, payload), ()
        if operation == "capture_prepare":
            return self._capture_prepare(scope, state, payload), ()
        if operation == "prepared_status":
            return self._prepared_status(scope, payload), ()
        if operation == "seal_prepared":
            return self._seal_prepared(scope, payload), ()
        if operation == "abort_prepared":
            return self._abort_prepared(scope, payload), ()
        if operation == "namespace_fds":
            return self._namespace_fds(state, payload)
        if operation == "recover":
            return self._recover(scope, state, payload), ()
        if operation == "terminate":
            return self._terminate(scope, state, payload), ()
        if operation == "close":
            return self._close_scope(scope), ()
        raise RuntimeProtocolError("UNKNOWN_OPERATION", "runtime operation is unknown")

    def _open_or_resume(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        raw_identity = payload.get("identity")
        if not isinstance(raw_identity, Mapping):
            raise RuntimeProtocolError("INVALID_IDENTITY", "runtime identity is invalid")
        try:
            identity = RuntimeIdentity.from_dict(raw_identity)
        except ValueError as error:
            raise RuntimeProtocolError("INVALID_IDENTITY", str(error)) from error
        runtime_generation = str(payload.get("runtime_generation") or "")
        if len(runtime_generation) < 16:
            raise RuntimeProtocolError(
                "INVALID_GENERATION",
                "runtime generation is not a stable identifier",
            )
        identity_digest = _identity_digest(identity)
        scope_id = stable_token(identity_digest, prefix="scope")
        with self._lock:
            state = self._scopes.get(scope_id)
            if state is None:
                if len(self._scopes) >= self._max_scopes:
                    raise RuntimeProtocolError(
                        "SCOPE_CAPACITY",
                        "runtime scope capacity is exhausted",
                    )
                host = self._require_host()
                view = host.view_for(scope_id)
                state = _ScopeState(
                    identity_digest=identity_digest,
                    view=view,
                    runtime_generations=set(),
                )
                self._scopes[scope_id] = state
            elif state.identity_digest != identity_digest:
                raise RuntimeProtocolError(
                    "SCOPE_COLLISION",
                    "runtime scope identity collision",
                )
            state.runtime_generations.add(runtime_generation)
        return asdict(
            RuntimeScopeRef(
                scope_id=scope_id,
                identity_digest=identity_digest,
                server_generation=self.server_generation,
                extension_generation=self._extension_generation,
            )
        )

    def _require_scope(
        self,
        payload: Mapping[str, Any],
    ) -> tuple[RuntimeScopeRef, _ScopeState]:
        raw = payload.get("scope")
        if not isinstance(raw, Mapping):
            raise RuntimeProtocolError("INVALID_SCOPE", "runtime scope is required")
        try:
            scope = RuntimeScopeRef.from_dict(raw)
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeProtocolError("INVALID_SCOPE", "runtime scope is invalid") from error
        if (
            scope.server_generation != self.server_generation
            or scope.extension_generation != self._extension_generation
        ):
            raise RuntimeProtocolError(
                "STALE_GENERATION",
                "runtime scope belongs to a stale generation",
            )
        with self._lock:
            state = self._scopes.get(scope.scope_id)
        if state is None or state.identity_digest != scope.identity_digest:
            raise RuntimeProtocolError("UNKNOWN_SCOPE", "runtime scope is unknown")
        return scope, state

    def _capture_begin(
        self,
        scope: RuntimeScopeRef,
        state: _ScopeState,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        extension = str(payload.get("extension") or "")
        operation_key = str(payload.get("operation_key") or "")
        metadata = payload.get("metadata")
        if not isinstance(metadata, Mapping):
            raise RuntimeProtocolError("INVALID_CAPTURE", "capture metadata is invalid")
        surface = _surface(state.view, extension)
        begin = getattr(surface, "runtime_capture_begin", None)
        if not callable(begin):
            raise RuntimeProtocolError(
                "UNSUPPORTED_OPERATION",
                "extension does not support persistent capture",
            )
        operation_identity = (scope.scope_id, extension, operation_key)
        with self._lock:
            existing_ref = self._operations.get(operation_identity)
            if existing_ref is not None:
                return asdict(existing_ref)
            if len(self._captures) >= self._max_captures:
                raise RuntimeProtocolError(
                    "CAPTURE_CAPACITY",
                    "runtime capture capacity is exhausted",
                )
        capture_id = str(begin(operation_key, dict(metadata)))
        if not capture_id:
            raise RuntimeProtocolError("INVALID_CAPTURE", "extension returned no capture id")
        ref = RuntimeCaptureRef(
            scope_id=scope.scope_id,
            extension=extension,
            capture_id=capture_id,
            operation_key=operation_key,
            server_generation=self.server_generation,
        )
        with self._lock:
            key = (scope.scope_id, capture_id)
            existing = self._captures.get(key)
            if existing is not None:
                if existing.ref.operation_key != operation_key:
                    raise RuntimeProtocolError(
                        "CAPTURE_COLLISION",
                        "capture id was reused for another operation",
                    )
                return asdict(existing.ref)
            self._captures[key] = _CaptureState(ref=ref, surface=surface)
            self._operations[operation_identity] = ref
        return asdict(ref)

    def _capture_prepare(
        self,
        scope: RuntimeScopeRef,
        state: _ScopeState,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        capture = _capture_ref(payload, self.server_generation)
        if capture.scope_id != scope.scope_id:
            raise RuntimeProtocolError("WRONG_SCOPE", "capture belongs to another scope")
        terminal = payload.get("terminal_evidence")
        if not isinstance(terminal, Mapping):
            raise RuntimeProtocolError(
                "INVALID_TERMINAL_EVIDENCE",
                "terminal evidence is invalid",
            )
        key = (scope.scope_id, capture.capture_id)
        with self._lock:
            capture_state = self._captures.get(key)
        if capture_state is None or capture_state.ref != capture:
            raise RuntimeProtocolError("UNKNOWN_CAPTURE", "runtime capture is unknown")
        with capture_state.lock:
            if capture_state.prepared is not None:
                return asdict(capture_state.prepared)
            prepare = getattr(capture_state.surface, "runtime_capture_prepare", None)
            if not callable(prepare):
                raise RuntimeProtocolError(
                    "UNSUPPORTED_OPERATION",
                    "extension cannot prepare a persistent capture",
                )
            batch = prepare(capture.capture_id, dict(terminal))
            if not isinstance(batch, PreparedExtensionBatch):
                raise RuntimeProtocolError(
                    "INVALID_PREPARED_CAPTURE",
                    "extension returned an invalid prepared capture",
                )
            if batch.scope_id != scope.scope_id:
                batch.abort()
                raise RuntimeProtocolError(
                    "WRONG_SCOPE",
                    "prepared capture belongs to another scope",
                )
            ref = PreparedCaptureRef(
                scope_id=scope.scope_id,
                extension=capture.extension,
                prepared_id=batch.batch_id,
                operation_key=batch.operation_key,
                content_hash=batch.content_hash,
                server_generation=self.server_generation,
            )
            with self._lock:
                capture_state.prepared = ref
                self._prepared[(scope.scope_id, ref.prepared_id)] = _PreparedState(
                    ref=ref,
                    batch=batch,
                )
            return asdict(ref)

    def _prepared_status(
        self,
        scope: RuntimeScopeRef,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        state = self._require_prepared(scope, payload)
        return {
            **asdict(state.ref),
            "state": state.batch.state,
            "commit": dict(state.batch.commit or {}),
        }

    def _seal_prepared(
        self,
        scope: RuntimeScopeRef,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        state = self._require_prepared(scope, payload)
        owner_records = payload.get("owner_records")
        if not isinstance(owner_records, list) or not all(
            isinstance(record, Mapping) for record in owner_records
        ):
            raise RuntimeProtocolError("INVALID_OWNER_RECORDS", "owner records are invalid")
        boundary = str(payload.get("boundary") or "adhoc")
        idempotency_key = str(payload.get("idempotency_key") or "")
        fingerprint = _seal_fingerprint(state.ref, owner_records, boundary)
        seal_key = (scope.scope_id, idempotency_key)
        with self._seal_lock:
            if state.commit is not None:
                if (
                    state.idempotency_key != idempotency_key
                    or state.seal_fingerprint != fingerprint
                ):
                    raise RuntimeProtocolError(
                        "IDEMPOTENCY_MISMATCH",
                        "prepared capture was already sealed with different content",
                    )
                return dict(state.commit)
            with self._lock:
                prior = self._seal_results.get(seal_key)
                if prior is not None:
                    prior_fingerprint, commit = prior
                    if prior_fingerprint != fingerprint:
                        raise RuntimeProtocolError(
                            "IDEMPOTENCY_MISMATCH",
                            "seal key was replayed with different content",
                        )
                    self._seal_results.move_to_end(seal_key)
                    return dict(commit)
            if owner_records:
                self._context_tree.stage_extensions(
                    [dict(record) for record in owner_records]
                )
            commit = self._require_host().seal_prepared(
                [state.batch],
                boundary=boundary,
            )
            state.idempotency_key = idempotency_key
            state.seal_fingerprint = fingerprint
            state.commit = dict(commit)
            with self._lock:
                self._seal_results[seal_key] = (fingerprint, dict(commit))
                self._seal_results.move_to_end(seal_key)
                while len(self._seal_results) > self._max_cached_requests:
                    self._seal_results.popitem(last=False)
            return dict(commit)

    def _abort_prepared(
        self,
        scope: RuntimeScopeRef,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        state = self._require_prepared(scope, payload)
        state.batch.abort()
        return {"prepared_id": state.ref.prepared_id, "state": state.batch.state}

    def _namespace_fds(
        self,
        state: _ScopeState,
        payload: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], tuple[int, ...]]:
        extension = str(payload.get("extension") or "")
        surface = _surface(state.view, extension)
        provider = getattr(surface, "runtime_namespace_fds", None)
        if not callable(provider):
            raise RuntimeProtocolError(
                "UNSUPPORTED_OPERATION",
                "extension does not expose namespace descriptors",
            )
        result = provider()
        if (
            not isinstance(result, tuple)
            or len(result) != 2
            or not isinstance(result[0], Mapping)
            or not isinstance(result[1], Sequence)
        ):
            raise RuntimeProtocolError(
                "INVALID_NAMESPACE_FDS",
                "extension returned invalid namespace descriptors",
            )
        fds = tuple(int(fd) for fd in result[1])
        if any(fd < 0 for fd in fds):
            raise RuntimeProtocolError(
                "INVALID_NAMESPACE_FDS",
                "extension returned an invalid descriptor",
            )
        return dict(result[0]), fds

    def _recover(
        self,
        scope: RuntimeScopeRef,
        state: _ScopeState,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        extension = str(payload.get("extension") or "")
        surface = _surface(state.view, extension)
        recover = getattr(surface, "runtime_recover", None)
        if not callable(recover):
            return {"scope_id": scope.scope_id, "prepared": []}
        recovered = tuple(recover())
        refs = []
        for batch in recovered:
            if not isinstance(batch, PreparedExtensionBatch):
                raise RuntimeProtocolError(
                    "INVALID_RECOVERY",
                    "extension returned invalid recovery state",
                )
            ref = PreparedCaptureRef(
                scope_id=scope.scope_id,
                extension=extension,
                prepared_id=batch.batch_id,
                operation_key=batch.operation_key,
                content_hash=batch.content_hash,
                server_generation=self.server_generation,
            )
            self._prepared[(scope.scope_id, ref.prepared_id)] = _PreparedState(
                ref=ref,
                batch=batch,
            )
            refs.append(asdict(ref))
        return {"scope_id": scope.scope_id, "prepared": refs}

    def _terminate(
        self,
        scope: RuntimeScopeRef,
        state: _ScopeState,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        extension = str(payload.get("extension") or "")
        surface = _surface(state.view, extension)
        terminate = getattr(surface, "runtime_terminate", None)
        if callable(terminate):
            terminate()
        return self._close_scope(scope)

    def _close_scope(self, scope: RuntimeScopeRef) -> Mapping[str, Any]:
        with self._lock:
            state = self._scopes.pop(scope.scope_id, None)
            capture_keys = [key for key in self._captures if key[0] == scope.scope_id]
            operation_keys = [key for key in self._operations if key[0] == scope.scope_id]
            prepared_keys = [key for key in self._prepared if key[0] == scope.scope_id]
            for key in capture_keys:
                self._captures.pop(key, None)
            for key in operation_keys:
                self._operations.pop(key, None)
            for key in prepared_keys:
                self._prepared.pop(key, None)
        if state is not None:
            state.view.close()
        return {"scope_id": scope.scope_id, "state": "closed"}

    def _require_prepared(
        self,
        scope: RuntimeScopeRef,
        payload: Mapping[str, Any],
    ) -> _PreparedState:
        prepared = _prepared_ref(payload, self.server_generation)
        if prepared.scope_id != scope.scope_id:
            raise RuntimeProtocolError(
                "WRONG_SCOPE",
                "prepared capture belongs to another scope",
            )
        with self._lock:
            state = self._prepared.get((scope.scope_id, prepared.prepared_id))
        if state is None or state.ref != prepared:
            raise RuntimeProtocolError(
                "UNKNOWN_PREPARED_CAPTURE",
                "prepared capture is unknown",
            )
        return state

    def _remove_stale_socket(self) -> None:
        try:
            details = self.socket_path.lstat()
        except FileNotFoundError:
            return
        if details.st_uid != os.geteuid() or not stat.S_ISSOCK(details.st_mode):
            raise RuntimeError("extension runtime socket path is not owner-controlled")
        self.socket_path.unlink()

    def _require_host(self) -> EmbeddedExtensionHost:
        if self._host is None:
            raise RuntimeError("extension runtime host is not started")
        return self._host

    def _ensure_started(self) -> None:
        if not self._started or self._closed:
            raise RuntimeError("extension runtime server is not running")


def _surface(view: PactScopeView, extension: str) -> Any:
    try:
        return view.extensions[extension]
    except KeyError as error:
        raise RuntimeProtocolError(
            "UNKNOWN_EXTENSION",
            "runtime extension is not active",
        ) from error


def _capture_ref(
    payload: Mapping[str, Any],
    generation: str,
) -> RuntimeCaptureRef:
    raw = payload.get("capture")
    if not isinstance(raw, Mapping):
        raise RuntimeProtocolError("INVALID_CAPTURE", "capture reference is invalid")
    try:
        ref = RuntimeCaptureRef.from_dict(raw)
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeProtocolError("INVALID_CAPTURE", "capture reference is invalid") from error
    if ref.server_generation != generation:
        raise RuntimeProtocolError("STALE_GENERATION", "capture reference is stale")
    return ref


def _prepared_ref(
    payload: Mapping[str, Any],
    generation: str,
) -> PreparedCaptureRef:
    raw = payload.get("prepared")
    if not isinstance(raw, Mapping):
        raise RuntimeProtocolError(
            "INVALID_PREPARED_CAPTURE",
            "prepared capture reference is invalid",
        )
    try:
        ref = PreparedCaptureRef.from_dict(raw)
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeProtocolError(
            "INVALID_PREPARED_CAPTURE",
            "prepared capture reference is invalid",
        ) from error
    if ref.server_generation != generation:
        raise RuntimeProtocolError("STALE_GENERATION", "prepared capture is stale")
    return ref


def _peer_credentials(connection: socket.socket) -> tuple[int, int, int]:
    if not hasattr(socket, "SO_PEERCRED"):
        raise RuntimeProtocolError(
            "PEER_CREDENTIALS_UNAVAILABLE",
            "runtime peer credentials are unavailable",
        )
    size = struct.calcsize("3i")
    raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, size)
    return struct.unpack("3i", raw)


def _identity_digest(identity: RuntimeIdentity) -> str:
    encoded = json.dumps(
        identity.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _extension_generation(host: EmbeddedExtensionHost) -> str:
    values = [
        (descriptor.name, descriptor.api_version, descriptor.package_version)
        for descriptor in host._descriptors
    ]
    encoded = json.dumps(values, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _fingerprint_request(request: Mapping[str, Any]) -> str:
    redacted = dict(request)
    redacted.pop("capability", None)
    encoded = json.dumps(
        redacted,
        sort_keys=True,
        separators=(",", ":"),
        default=repr,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _seal_fingerprint(
    prepared: PreparedCaptureRef,
    owner_records: Sequence[Mapping[str, Any]],
    boundary: str,
) -> str:
    encoded = json.dumps(
        {
            "prepared": asdict(prepared),
            "owner_records": list(owner_records),
            "boundary": boundary,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=repr,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["ExtensionRuntimeServer"]
