from __future__ import annotations

import json

import pytest

from pactree.components import (
    AudioContent,
    Component,
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
    component_from_dict,
    component_from_json,
    component_to_dict,
    component_to_json,
    deserialize_component,
    serialize_component,
)

_STORAGE_FIELDS = {
    "id",
    "parent_id",
    "offset",
    "pact_depth",
    "priority",
    "transaction_index",
    "created_at_ns",
    "creation_index",
    "child_ids",
    "children",
}


def _authoring_payload(component: Component) -> dict:
    return {
        key: value
        for key, value in component.model_dump().items()
        if key not in _STORAGE_FIELDS and value is not None
    }


@pytest.mark.parametrize(
    "component",
    [
        TextContent("hello", ttl=2, cad=3, key="text:key", tags=["chat"], aux={"tone": "calm"}),
        ReasoningContent(
            "thinking",
            visibility="displayable",
            aux={"provider": "test"},
        ),
        ToolCall("call_1", "lookup", parameters={"q": "x"}, content="checking"),
        ToolResult(
            "call_1",
            tool_name="lookup",
            success=True,
            result={"value": 42},
            content="done",
        ),
        SystemHeader("system", aux={"scope": "root"}),
        MessageTurn("turn", role="user", aux={"source": "test"}),
        MessageContainer("container", aux={"slot": "core"}),
        XMLComponent("body", type="note", attrs={"priority": "high"}),
        MediaContent(kind="spectrogram", url="https://example.com/spec.json"),
        ImageContent(url="https://example.com/image.png", mime_type="image/png"),
        AudioContent(data=b"RIFF", mime_type="audio/wav"),
    ],
)
def test_component_dict_roundtrip(component: Component) -> None:
    restored = component_from_dict(component_to_dict(component))

    assert isinstance(restored, Component)
    assert _authoring_payload(restored) == _authoring_payload(component)


def test_component_json_roundtrip() -> None:
    component = ToolCall("call_1", "lookup", parameters={"q": "x"})
    encoded = component_to_json(component)

    assert json.loads(encoded)["componentType"] == "tool_call"
    assert _authoring_payload(component_from_json(encoded)) == _authoring_payload(component)


def test_component_serialize_deserialize_roundtrip() -> None:
    component = TextContent("hello")
    payload = serialize_component(component)

    assert payload["componentType"] == "text_content"
    assert _authoring_payload(deserialize_component(payload)) == _authoring_payload(component)


def test_component_from_json_accepts_mapping() -> None:
    component = component_from_json(
        {"componentType": "text_content", "content": "from mapping"}
    )

    assert isinstance(component, Component)
    assert component.component_type == "text_content"
    assert component.content == "from mapping"


def test_component_from_dict_preserves_pact_id_as_native_identity() -> None:
    component = component_from_dict(
        {
            "pact_id": "component:restored",
            "componentType": "text_content",
            "parent_id": "message",
            "offset": 99,
            "created_at_ns": 123,
            "content": "portable",
        }
    )

    dumped = component.model_dump()
    assert dumped["content"] == "portable"
    assert dumped["id"] == "component:restored"
    assert component.id == "component:restored"
    assert dumped["parent_id"] == "message"
    assert dumped["offset"] == 99
    assert dumped["created_at_ns"] == 123


def test_component_to_dict_serializes_native_id_as_pact_id() -> None:
    component = component_from_dict(
        {"pact_id": "component:serialized", "componentType": "text_content", "content": "x"}
    )
    payload = component_to_dict(component)

    assert payload["pact_id"] == "component:serialized"
    assert "id" not in payload


def test_component_pact_id_projects_as_internal_id() -> None:
    component = component_from_dict(
        {"pact_id": "component:rendered", "componentType": "note", "content": "body"}
    )

    assert component.id == "component:rendered"
    assert component.model_dump()["id"] == "component:rendered"


def test_component_from_dict_rejects_conflicting_id_and_pact_id() -> None:
    with pytest.raises(ValueError, match="conflicting"):
        component_from_dict(
            {
                "id": "native:id",
                "pact_id": "public:id",
                "componentType": "text_content",
                "content": "x",
            }
        )


def test_component_from_dict_rejects_missing_component_type() -> None:
    with pytest.raises(ValueError, match="componentType"):
        component_from_dict({"content": "missing type"})


def test_component_from_json_rejects_non_object_json() -> None:
    with pytest.raises(TypeError, match="object"):
        component_from_json("[1, 2, 3]")
