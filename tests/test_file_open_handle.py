from __future__ import annotations

from pathlib import Path

import pactree


def test_file_open_handle_preserves_the_selected_branch(monkeypatch, tmp_path):
    observed: dict[str, object] = {}
    sentinel = object()

    def fake_open_handle(path: str | Path, branch_id: str | None = None):
        observed["path"] = path
        observed["branch_id"] = branch_id
        return sentinel

    monkeypatch.setattr(pactree, "open_handle", fake_open_handle)

    result = pactree.file.open_handle(tmp_path / "session.pact", branch_id="feature")

    assert result is sentinel
    assert observed == {
        "path": tmp_path / "session.pact",
        "branch_id": "feature",
    }
