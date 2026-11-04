"""Thin Python selector wrapper for Rust PACT positions."""

from __future__ import annotations

from typing import Any, Optional


class Pos:
    """Ergonomic host wrapper around canonical PACT selector text.

    Python does not validate, resolve, or apply coordinates. It only builds the
    selector string; Rust PACT owns selector semantics.
    """

    def __init__(
        self,
        *selector_parts: str,
        ttl: Optional[int] = None,
        cad: Optional[int] = None,
        cadence: Optional[int] = None,
        **attrs: Any,
    ):
        if not selector_parts:
            raise ValueError("Position selector cannot be empty")

        selector = " ".join(str(part) for part in selector_parts).strip()
        if not selector:
            raise ValueError("Position selector cannot be empty")

        self.ttl = ttl
        self.cadence = cadence if cadence is not None else cad
        self.attrs = dict(attrs)
        self.coordinates = None

        if selector.startswith(("d", "(")) and not selector.startswith("("):
            base = f"({selector})"
        else:
            base = selector

        if attrs:
            attr_text = ", ".join(f"{key}='{value}'" for key, value in attrs.items())
            base = f"{base} {{{attr_text}}}"

        behaviors: list[str] = []
        if ttl is not None:
            behaviors.append(f"ttl={int(ttl)}")
        if self.cadence is not None:
            behaviors.append(f"cad={int(self.cadence)}")

        self.selector = f"{base} [{' '.join(behaviors)}]" if behaviors else base

    def __str__(self) -> str:
        return self.selector

    def __repr__(self) -> str:
        return f"Pos({self.selector!r})"

    @classmethod
    def from_string(cls, selector_string: str) -> "Pos":
        instance = cls.__new__(cls)
        instance.selector = str(selector_string)
        instance.ttl = None
        instance.cadence = None
        instance.attrs = {}
        instance.coordinates = None
        return instance
