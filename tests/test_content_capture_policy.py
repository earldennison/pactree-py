"""Regression tests for public component authoring payload boundaries."""

import pytest

from pactree.components.core import TextContent


def test_text_content_model_dump_preserves_external_refs_escape_hatch():
    node = TextContent(
        content="visible",
        external_refs=[{"kind": "file", "uri": "file:///tmp/a.py", "role": "source"}],
    )
    dumped = node.model_dump()
    assert dumped["content"] == "visible"
    assert dumped["external_refs"] == [
        {"kind": "file", "uri": "file:///tmp/a.py", "role": "source"}
    ]


def test_text_content_internal_storage_fields_are_not_public_kwargs():
    for field, value in {
        "content_capture": "reference_only",
        "source_ref": {"kind": "file", "path": "/tmp/a.py"},
        "content_status": "reference_only",
        "content_ref": "blob_1",
    }.items():
        with pytest.raises(TypeError, match=field):
            TextContent(content="visible", **{field: value})


def test_text_content_external_refs_default_to_empty_list():
    node = TextContent(content="x")
    dumped = node.model_dump()

    assert not dumped.get("external_refs")
