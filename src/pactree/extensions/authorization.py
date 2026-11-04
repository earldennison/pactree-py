"""Opaque, host-issued capabilities for explicit extension side effects."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from typing import Any

from .errors import ExtensionAuthorizationError


_CONSTRUCTION_KEY = object()


class ApplyAuthorization:
    """Opaque single-use token; only :class:`AuthorizationIssuer` can mint one."""

    __slots__ = ("_issuer_id", "_token_id")

    def __init__(
        self,
        issuer_id: str,
        token_id: str | None = None,
        *,
        _key: object | None = None,
    ) -> None:
        if _key is not _CONSTRUCTION_KEY or token_id is None:
            raise TypeError("ApplyAuthorization values are issued by a Pactree host")
        self._issuer_id = issuer_id
        self._token_id = token_id

    def __repr__(self) -> str:
        return "ApplyAuthorization(<opaque>)"


@dataclass(frozen=True, slots=True)
class AuthorizationAttestation:
    actor: str
    capability: str
    scope_id: str
    operation_fingerprint: str
    snapshot_fingerprint: str
    target_fingerprint: str


@dataclass(slots=True)
class _Grant:
    capability: str
    scope_id: str
    operation_fingerprint: str
    snapshot_fingerprint: str
    target_fingerprint: str
    actor: str
    expires_at: float
    consumed: bool = False


class AuthorizationVerifier:
    """Restricted extension surface that can consume but never mint grants."""

    def __init__(self, issuer: "AuthorizationIssuer") -> None:
        self._issuer = issuer

    def consume(
        self,
        authorization: ApplyAuthorization,
        *,
        capability: str,
        scope_id: str,
        expected_operation: Any,
        expected_snapshot: Any,
        expected_target_head: Any,
    ) -> AuthorizationAttestation:
        return self._issuer.consume(
            authorization,
            capability=capability,
            scope_id=scope_id,
            expected_operation=expected_operation,
            expected_snapshot=expected_snapshot,
            expected_target_head=expected_target_head,
        )


class AuthorizationIssuer:
    """In-memory authority owned by one open extension host."""

    def __init__(self, *, max_grants: int = 4096) -> None:
        self._issuer_id = uuid.uuid4().hex
        self._grants: dict[str, _Grant] = {}
        self._max_grants = max_grants
        self._lock = threading.RLock()

    @property
    def verifier(self) -> AuthorizationVerifier:
        return AuthorizationVerifier(self)

    def issue(
        self,
        *,
        capability: str,
        scope_id: str,
        expected_operation: Any,
        expected_snapshot: Any,
        expected_target_head: Any,
        actor: str,
        ttl_seconds: float = 30.0,
    ) -> ApplyAuthorization:
        if not capability or not scope_id or not actor:
            raise ExtensionAuthorizationError(
                "authorization capability, scope, and actor are required"
            )
        if ttl_seconds <= 0:
            raise ExtensionAuthorizationError("authorization TTL must be positive")
        token_id = secrets.token_urlsafe(32)
        grant = _Grant(
            capability=capability,
            scope_id=scope_id,
            operation_fingerprint=_fingerprint(expected_operation),
            snapshot_fingerprint=_fingerprint(expected_snapshot),
            target_fingerprint=_fingerprint(expected_target_head),
            actor=actor,
            expires_at=time.monotonic() + ttl_seconds,
        )
        with self._lock:
            self._prune_locked()
            if len(self._grants) >= self._max_grants:
                raise ExtensionAuthorizationError(
                    "authorization capacity is exhausted"
                )
            self._grants[token_id] = grant
        return ApplyAuthorization(
            self._issuer_id,
            token_id,
            _key=_CONSTRUCTION_KEY,
        )

    def consume(
        self,
        authorization: ApplyAuthorization,
        *,
        capability: str,
        scope_id: str,
        expected_operation: Any,
        expected_snapshot: Any,
        expected_target_head: Any,
    ) -> AuthorizationAttestation:
        if not isinstance(authorization, ApplyAuthorization):
            raise ExtensionAuthorizationError("authorization is not host-issued")
        if authorization._issuer_id != self._issuer_id:
            raise ExtensionAuthorizationError("authorization belongs to another host")
        with self._lock:
            grant = self._grants.get(authorization._token_id)
            if grant is None:
                raise ExtensionAuthorizationError("authorization is unknown")
            if grant.consumed:
                raise ExtensionAuthorizationError("authorization was already consumed")
            if time.monotonic() >= grant.expires_at:
                raise ExtensionAuthorizationError("authorization expired")
            if grant.capability != capability:
                raise ExtensionAuthorizationError("authorization capability does not match")
            if grant.scope_id != scope_id:
                raise ExtensionAuthorizationError("authorization scope does not match")
            if grant.operation_fingerprint != _fingerprint(expected_operation):
                raise ExtensionAuthorizationError(
                    "authorization operation does not match"
                )
            if grant.snapshot_fingerprint != _fingerprint(expected_snapshot):
                raise ExtensionAuthorizationError("authorization snapshot does not match")
            if grant.target_fingerprint != _fingerprint(expected_target_head):
                raise ExtensionAuthorizationError("authorization target does not match")
            grant.consumed = True
            return AuthorizationAttestation(
                actor=grant.actor,
                capability=grant.capability,
                scope_id=grant.scope_id,
                operation_fingerprint=grant.operation_fingerprint,
                snapshot_fingerprint=grant.snapshot_fingerprint,
                target_fingerprint=grant.target_fingerprint,
            )

    def close(self) -> None:
        with self._lock:
            self._grants.clear()

    def _prune_locked(self) -> None:
        now = time.monotonic()
        expired = [
            token_id
            for token_id, grant in self._grants.items()
            if grant.consumed or now >= grant.expires_at
        ]
        for token_id in expired:
            self._grants.pop(token_id, None)


def _fingerprint(value: Any) -> str:
    payload = _jsonable(value)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=repr,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {
            field.name: _jsonable(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _jsonable(to_dict())
    return repr(value)
