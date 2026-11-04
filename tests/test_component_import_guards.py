"""`pactree.components` is the public authoring import path;
public code must not rely on `pactree.components.core`.
"""

from __future__ import annotations

import pathlib

import pactree


def test_official_component_import_path_works():
    from pactree.components import TextContent, ToolCall, ToolResult, ReasoningContent  # noqa: F401


def test_components_all_lists_official_surface():
    import pactree.components as comps

    for name in (
        "Component",
        "TextContent",
        "ReasoningContent",
        "ToolCall",
        "ToolResult",
        "component_from_dict",
        "component_from_json",
        "component_to_dict",
        "component_to_json",
        "deserialize_component",
        "serialize_component",
    ):
        assert name in comps.__all__, f"{name} missing from pactree.components.__all__"


def test_no_authoritative_id_in_public_components_all():
    import pactree.components as comps


    assert "PACTNode" not in comps.__all__
    assert "PactRoot" not in comps.__all__


def test_production_does_not_import_components_core():


    src = pathlib.Path(pactree.__file__).parent
    base = (src / "context" / "base.py").read_text()
    assert "from ..components.core import" not in base, (
        "facade should import from pactree.components, not components.core"
    )
