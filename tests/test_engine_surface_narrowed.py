"""The native module exposes a bounded public surface.

Normal users should reach Pact through ContextTree, ctx.file, and ctx.search.
The extension may keep private underscored diagnostics for the Python facades,
but it must not expose raw path-first pact_file_* admin functions as public API.
"""

from __future__ import annotations

import pactree.pactree_engine as engine

FORBIDDEN = {
    "pact_file_buffered_read",
    "pact_file_rewind_to",
    "pact_file_open_head_fast",
    "pact_file_batch_open_head_fast",
    "pact_file_cycles",
    "pact_file_context_tree",
    "pact_file_query",
    "pact_file_repair",
    "pact_file_import",
    "pact_file_merge",
    "pact_file_create",
    "pact_file_bundle_create",
    "pact_file_project_range",
    "pact_file_view_projection",
    "pact_file_view_query",
    "pact_file_view_merge",
    "pact_file_view_merge_preflight",
    "pact_file_view_detach",
    "pact_file_view_rebase",
    "render_xml_component",
    "select_pact_subtrees",
}

def test_no_raw_file_view_query_admin_sprawl():
    for name in FORBIDDEN:
        assert not hasattr(engine, name), f"engine still exposes raw {name}"


def test_no_public_pact_file_surface():
    exposed = {n for n in dir(engine) if n.startswith("pact_file_")}
    assert not exposed, f"unexpected public pact_file_* exports: {sorted(exposed)}"


def test_raw_diagnostic_helpers_are_private():
    assert not hasattr(engine, "select_pact_tree")
    assert not hasattr(engine, "pact_mutation_request_validate")
    assert hasattr(engine, "_select_pact_tree")
    assert hasattr(engine, "_pact_mutation_request_validate")
