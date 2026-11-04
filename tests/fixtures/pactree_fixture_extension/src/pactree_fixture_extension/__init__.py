"""Pure-Python fixture proving extensions install without rebuilding Pactree."""

from __future__ import annotations

from typing import Any

from pactree.extensions import ExtensionDescriptor


class FixtureExtension:
    def __init__(self, context: Any, dependency: Any) -> None:
        self.context = context
        self.dependency = dependency
        self.active = False

    def activate(self) -> "FixtureExtension":
        self.active = True
        return self

    def close(self) -> None:
        self.active = False


def extension() -> ExtensionDescriptor:
    return ExtensionDescriptor(
        name="fixture",
        package_version="0.0.1",
        authority="derived",
        record_prefixes=("fixture.",),
        fork_strategy="follow",
        factory=FixtureExtension,
    )
