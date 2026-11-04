from __future__ import annotations

from pathlib import Path

from pactree import ContextTree


def test_ctx_search_returns_hydrated_hits(tmp_path: Path) -> None:
    pact_path = tmp_path / "session.pact"




    tree = ContextTree()
    tree.user("historical pager needle")
    tree.send()
    tree.save(pact_path)

    result = tree.search(
        {
            "text": "needle",
            "scope": "all",
            "limit": 5,
            "preview_chars": 80,
        },
        mode="text",
    )

    assert result["schema"] == "pact.file.v0.text_search.v1"
    assert result["query"]["scope"] == "all"
    assert result["hits"], "expected at least one text-search hit"
    hit = result["hits"][0]
    assert hit["component_id"]
    assert hit["exact_text_preview"] == "historical pager needle"


def test_search_is_not_a_free_file_admin_helper() -> None:

    import pactree.file as pfile

    for removed in ("text_search", "content_payload", "rewind_to", "query"):
        assert not hasattr(pfile, removed), f"pactree.file.{removed} must be removed"
        assert removed not in getattr(pfile, "__all__", [])
    assert hasattr(ContextTree, "search"), "ctx.search is the public search surface"
    assert hasattr(ContextTree, "file"), "ctx.file is the public file-admin surface"
