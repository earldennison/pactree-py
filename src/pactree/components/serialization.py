"""JSON serialization for public Pactree authoring components."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TypeAlias, cast

from .. import pactree_engine
from .core import Component

JsonValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | None
    | list["JsonValue"]
    | dict[str, "JsonValue"]
)
JsonMap: TypeAlias = dict[str, JsonValue]

def component_to_dict(component: Component) -> JsonMap:
    """Serialize a native authoring component to a JSON-shaped mapping."""

    return _public_component_payload(_json_map(component.model_dump()))


def serialize_component(component: Component) -> JsonMap:
    """Serialize a native authoring component to a JSON-shaped mapping."""

    return component_to_dict(component)


def component_from_dict(payload: Mapping[str, JsonValue]) -> Component:
    """Deserialize a JSON-shaped component payload into a native component.

    Public component JSON uses ``pact_id`` for first-class PACT identity so it
    cannot be confused with XML/application attributes named ``id``.
    """

    return cast(Component, pactree_engine.component_from_dict(_native_component_payload(payload)))


def deserialize_component(payload: Mapping[str, JsonValue]) -> Component:
    """Deserialize a JSON-shaped component payload into a native component."""

    return component_from_dict(payload)


def component_to_json(component: Component) -> str:
    """Serialize a native authoring component to compact JSON."""

    return json.dumps(component_to_dict(component), ensure_ascii=False, separators=(",", ":"))


def component_from_json(payload: str | Mapping[str, JsonValue]) -> Component:
    """Deserialize a component from a JSON string or JSON-shaped mapping."""

    if isinstance(payload, str):
        decoded = json.loads(payload)
        if not isinstance(decoded, Mapping):
            raise TypeError("component JSON must decode to an object")
        return component_from_dict(cast(Mapping[str, JsonValue], decoded))
    return component_from_dict(payload)


def _public_component_payload(payload: JsonMap) -> JsonMap:
    out = dict(payload)
    native_id = out.pop("id", None)
    if native_id not in (None, ""):
        out["pact_id"] = native_id
    else:
        out.pop("pact_id", None)
    return out


def _native_component_payload(payload: Mapping[str, JsonValue]) -> JsonMap:
    out = {str(key): _json_value(value) for key, value in payload.items()}
    pact_id = out.pop("pact_id", None)
    if pact_id not in (None, ""):
        if "id" in out and out["id"] not in (None, "", pact_id):
            raise ValueError("component payload cannot contain conflicting id and pact_id")
        out["id"] = pact_id
    return out


def _json_map(value: JsonValue | Mapping[str, JsonValue]) -> JsonMap:
    if not isinstance(value, Mapping):
        raise TypeError("component model_dump must return an object")
    return {str(key): _json_value(item) for key, item in value.items()}


def _json_value(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value
