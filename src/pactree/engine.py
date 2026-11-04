"""Pactree engine availability."""

from __future__ import annotations


class EngineUnavailableError(RuntimeError):
    """Raised when an early package release needs the Pactree engine."""


def engine_available() -> bool:
    """Return whether the Pactree engine is bundled with this Python package."""

    try:
        from . import pactree_engine  # noqa: F401
    except Exception:
        return False
    return True
