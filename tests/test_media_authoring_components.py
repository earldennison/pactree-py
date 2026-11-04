from __future__ import annotations

from pactree.components import AudioContent, Component, ImageContent, MediaContent


def test_media_authoring_classes_are_public_rust_components():
    assert MediaContent.__module__ == "pactree.pactree_engine"
    assert ImageContent.__module__ == "pactree.pactree_engine"
    assert AudioContent.__module__ == "pactree.pactree_engine"
    assert issubclass(MediaContent, Component)
    assert issubclass(ImageContent, Component)
    assert issubclass(AudioContent, Component)


def test_pact_media_authoring_does_not_coerce_local_files():
    assert not hasattr(MediaContent, "from_file")
    assert not hasattr(ImageContent, "from_file")
    assert not hasattr(AudioContent, "from_file")

    image = ImageContent(
        external_ref={
            "kind": "egregore_media",
            "uri": "egregore://media/session-1/sha256-abc",
            "role": "media_source",
        },
        mime_type="image/png",
    )
    audio = AudioContent(data=b"RIFF", mime_type="audio/wav")

    assert isinstance(image, ImageContent)
    assert isinstance(audio, AudioContent)
    assert image.component_type == "media_content"
    assert audio.component_type == "media_content"
    assert image.media_kind == "image"
    assert audio.media_kind == "audio"
    assert image.mime_type == "image/png"
    assert audio.mime_type == "audio/wav"

    dumped = image.model_dump()
    assert dumped["componentType"] == "media_content"
    assert dumped["media_kind"] == "image"
    assert dumped["external_ref"]["uri"] == "egregore://media/session-1/sha256-abc"
    assert "bytes" not in dumped


def test_media_authoring_accepts_normalized_pact_body_slots():
    image = ImageContent(url="https://example.com/screen.png", mime_type="image/png")
    audio = AudioContent(data=b"RIFF", mime_type="audio/wav")
    custom = MediaContent(kind="spectrogram", url="https://example.com/spec.json")

    assert image.model_dump()["url"] == "https://example.com/screen.png"
    assert audio.model_dump()["bytes"] == [82, 73, 70, 70]
    assert custom.media_kind == "spectrogram"
