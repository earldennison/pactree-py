"""Authoring components are Rust-owned wrappers over ``pact::Component``.

Every name here is a PyO3 class defined in the compiled engine
(``pactree.pactree_engine``). This module only re-exports them — there is no Python
component model, taxonomy, validation, or rendering. The engine owns all of
that, and Pact mints ids on ingest.
"""

from __future__ import annotations

from ..pactree_engine import (
    Component,
    AudioContent,
    ImageContent,
    MediaContent,
    MessageContainer,
    MessageTurn,
    ReasoningContent,
    SystemHeader,
    TextContent,
    ToolCall,
    ToolResult,
    XMLComponent,
    external_ref,
)

__all__ = [
    "Component",
    "MediaContent",
    "ImageContent",
    "AudioContent",
    "MessageTurn",
    "MessageContainer",
    "SystemHeader",
    "TextContent",
    "ReasoningContent",
    "ToolCall",
    "ToolResult",
    "XMLComponent",
    "external_ref",
]
