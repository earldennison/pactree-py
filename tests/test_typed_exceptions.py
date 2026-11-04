"""The native engine maps `pact::PactError` kinds onto a pact-specific Python
exception hierarchy. These tests pin that contract: malformed/missing native
input raises a `pactree.Pact*` exception (never a bare `ValueError`/`RuntimeError`),
the names are importable from the package, and the kind-specific classes all
derive from `pactree.PactError` so a host can catch the whole family at once.
"""

from __future__ import annotations

import os
import tempfile

import pytest

import pactree
from pactree import (
    PactCodecError,
    PactCorruptFile,
    PactError,
    PactInvalidInput,
    PactNotFound,
)
from pactree import pactree_engine

ROOT_TREE = '{"id":"root","componentType":"root","children":[]}'


def test_exception_names_importable_from_package():
    for exc in (PactError, PactInvalidInput, PactNotFound, PactCorruptFile, PactCodecError):
        assert isinstance(exc, type)


def test_kind_classes_derive_from_base():
    assert issubclass(PactInvalidInput, PactError)
    assert issubclass(PactNotFound, PactError)
    assert issubclass(PactCorruptFile, PactError)
    assert issubclass(PactCodecError, PactError)


def test_package_types_are_the_native_types():


    assert pactree.PactError is pactree_engine.PactError
    assert pactree.PactInvalidInput is pactree_engine.PactInvalidInput
    assert pactree.PactNotFound is pactree_engine.PactNotFound
    assert pactree.PactCorruptFile is pactree_engine.PactCorruptFile
    assert pactree.PactCodecError is pactree_engine.PactCodecError


def test_malformed_selector_raises_invalid_input():
    with pytest.raises(PactInvalidInput):
        pactree_engine._select_pact_tree("[1,2,3]", ".message")

    with pytest.raises(PactError):
        pactree_engine._select_pact_tree("[1,2,3]", ".message")


def test_malformed_mutation_json_raises_invalid_input():
    with pytest.raises(PactInvalidInput):
        pactree_engine._pact_mutation_request_validate("{ not json")


def test_missing_file_raises_pact_error_family():
    with pytest.raises(PactError):
        pactree_engine.ContextTree.open_file("/tmp/pactree_missing_not_found_probe.pact")


def test_corrupt_pact_file_raises_corrupt_file():
    path = os.path.join(tempfile.gettempdir(), "pactree_typed_exc_probe.pact")
    with open(path, "wb") as handle:
        handle.write(b"PACT1\0\0\0" + b"\xff" * 40)
    try:
        with pytest.raises(PactCorruptFile):
            pactree_engine._pact_file_open(path)
    finally:
        os.remove(path)


def test_message_text_is_preserved_on_exception():

    try:
        pactree_engine._pact_mutation_request_validate("{ not json")
    except PactInvalidInput as err:
        assert "invalid PACT mutation request JSON" in str(err)
    else:  # pragma: no cover
        pytest.fail("expected PactInvalidInput")
