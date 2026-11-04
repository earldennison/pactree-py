"""File administration and search use the bound ContextTree.

`ctx.file.*` is the file-admin surface (bound to the tree's `.pact` path) and
`ctx.search(...)` is the single search dispatcher (text mode today). Neither is a
free path-taking helper.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pactree import ContextTree


def _saved_tree(tmp_path: Path) -> ContextTree:
    tree = ContextTree()
    tree.user("historical pager needle")
    tree.send()
    tree.save(tmp_path / "session.pact")
    return tree


def test_ctx_search_text_mode_returns_hydrated_hits(tmp_path: Path) -> None:
    tree = _saved_tree(tmp_path)


    scoped = tree.search({"text": "needle", "scope": "all", "limit": 5}, mode="text")
    assert scoped["query"]["scope"] == "all"
    assert scoped["hits"], "expected at least one text-search hit"
    assert scoped["hits"][0]["exact_text_preview"] == "historical pager needle"


    bare = tree.search("needle")
    assert bare["schema"].startswith("pact.file")
    assert "hits" in bare


def test_ctx_search_reserves_unsupported_modes(tmp_path: Path) -> None:
    tree = _saved_tree(tmp_path)
    with pytest.raises(NotImplementedError):
        tree.search("needle", mode="vector")
    with pytest.raises(ValueError):
        tree.search("needle", mode="bogus")


def test_ctx_file_is_bound_admin_facade(tmp_path: Path) -> None:
    tree = _saved_tree(tmp_path)
    assert isinstance(tree.file.info(), dict)
    assert isinstance(tree.file.validate(), dict)
    assert isinstance(tree.file.inspect(), dict)


def test_ctx_file_requires_a_bound_path() -> None:


    tree = ContextTree()
    with pytest.raises(RuntimeError):
        tree.file.info()


def test_file_admin_paths_do_not_json_decode_in_python() -> None:



    from pathlib import Path as _Path

    file_src = (_Path(__file__).resolve().parent.parent / "src/pactree/file.py").read_text()
    assert "import pactree._native" not in file_src
    assert "from ._native" not in file_src
    assert "json.loads" not in file_src, "file.py must not json-decode engine output"
