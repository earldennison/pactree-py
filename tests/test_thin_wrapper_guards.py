"""Architecture checks for the Python facade and native Rust bindings."""

from __future__ import annotations

import inspect
import pathlib

import pactree

_SRC = pathlib.Path(pactree.__file__).parent
_BASE = (_SRC / "context" / "base.py").read_text()




_RUST_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
_RUST = [_RUST_SRC / "rust_lib.rs", *sorted((_RUST_SRC / "bindings").glob("*.rs"))]
_NATIVE = "\n".join(p.read_text() for p in _RUST)


def test_phase1_native_bootstrap_no_python_root_construction():


    assert "_initial_tree" not in _BASE
    assert '"id": "root"' not in _BASE
    ctx = pactree.ContextTree()
    assert not hasattr(ctx, "_initial_tree")
    view = ctx.preview(as_view=True)
    node_types = {getattr(n, "node_type", None) for n in view.nodes.values()}
    assert "system_header" in node_types


def test_context_tree_has_no_agent_identity_leak():


    assert "agent_id" not in _BASE
    ctx = pactree.ContextTree()
    assert not hasattr(ctx, "agent_id")
    assert not hasattr(ctx, "_agent_id")


def test_phase2_no_python_message_part_classification():



    assert "_is_tool_call" not in _BASE
    assert "_is_tool_result" not in _BASE
    assert "_is_reasoning_component" not in _BASE

    from pactree.components.core import TextContent, ToolCall, ReasoningContent

    ctx = pactree.ContextTree()
    ctx.add_user("q")
    ctx.send()
    ctx.receive(
        [
            ReasoningContent(content="thinking"),
            TextContent(content="answer"),
            ToolCall(tool_call_id="c1", tool_name="grep", parameters={"q": "x"}),
        ]
    )
    kinds = {getattr(n, "node_type", None) for n in ctx.view().nodes.values()}
    assert {"tool_call", "reasoning_content", "text_content"} <= kinds


def test_phase3_rust_owns_node_identity():


    from pactree.components import core as _core
    from pactree.components.core import TextContent

    assert "generate_hash_id" not in _core.__all__
    assert not hasattr(_core, "generate_hash_id")
    assert "default_factory=lambda: generate_hash_id()" not in (_SRC / "components" / "core.py").read_text()
    ctx = pactree.ContextTree()
    comp = TextContent(content="x")
    local_id = comp.id
    assert local_id
    trace = ctx.insert(comp, pactree.Placement(-1, 0))

    assert trace.produced_component_ids
    assert trace.produced_component_ids[0].endswith(local_id)


def test_phase7_no_legacy_history_mutation_noop():

    assert "_record_history_mutation" not in _BASE


def test_phase5_store_module_nuked():


    import importlib.util

    assert importlib.util.find_spec("pactree.store") is None


def test_no_public_patch_runtime_api():


    assert "pact_store_append_patch" not in _NATIVE
    assert "append_patch_json" not in _NATIVE
    assert "ledger_append_branch_patch_json" not in _NATIVE
    assert "pact_store_import_legacy_patch" not in _NATIVE


def test_no_node_cache_shadow():


    assert "_node_cache" not in _BASE
    ctx = pactree.ContextTree()
    assert not hasattr(ctx, "_node_cache")


def test_no_duplicate_canonical_node_module():


    assert not (_SRC / "canonical_nodes.py").exists()
    assert "canonical_nodes" not in getattr(pactree, "__all__", [])
    assert not hasattr(pactree, "canonical_nodes")


def test_no_to_canonical_node_boundary_path():


    assert "to_canonical_node" not in _NATIVE


def test_one_dto_surface_is_components_core():
    from pactree.components.core import (
        TextContent,
        ReasoningContent,
        ToolCall,
        ToolResult,
    )



    assert hasattr(TextContent(content="x"), "model_dump")
    assert hasattr(ToolCall(tool_call_id="c", tool_name="t", parameters={}), "model_dump")
    assert ReasoningContent is not None and ToolResult is not None


def test_authoring_components_do_not_inline_placement_or_storage_fields():
    from pactree.components.core import TextContent

    signature = inspect.signature(TextContent)
    forbidden = {
        "parent_id",
        "offset",
        "pact_depth",
        "transaction_index",
        "created_at_ns",
        "creation_index",
        "org",
        "content_ref",
        "content_capture",
        "source_ref",
        "content_status",
    }
    leaked = forbidden & set(signature.parameters)
    assert not leaked, f"TextContent inlines placement/storage fields: {sorted(leaked)}"

    for name in forbidden:
        try:
            TextContent(content="x", **{name: "internal"})
        except TypeError:
            continue
        raise AssertionError(f"TextContent accepted inline placement/storage field {name!r}")


def test_authoring_components_are_not_pydantic_models():
    core_source = (_SRC / "components" / "core.py").read_text()
    project_source = pathlib.Path("pyproject.toml").read_text()

    for forbidden in ("pydantic", "BaseModel", "ConfigDict", "Field"):
        assert forbidden not in core_source
    assert "pydantic" not in project_source





def test_public_data_models_namespace_is_deleted():


    import importlib.util

    assert importlib.util.find_spec("pactree.data_models") is None
    assert "data_models" not in getattr(pactree, "__all__", [])
    assert not hasattr(pactree, "data_models")


def test_contexttree_is_the_public_entrypoint():


    from pactree import ContextTree  # noqa: F401

    public = set(getattr(pactree, "__all__", []))
    assert "ContextTree" in public
    for forbidden in ("NativeResolver", "PactRoot", "PACTNode", "materialize"):
        assert forbidden not in public, f"{forbidden} leaked into pactree.__all__"


def test_no_python_root_subclassing_in_facade():

    assert "class ContextTree(PactRoot)" not in _BASE


def test_file_module_has_no_tree_operations():



    import pactree.file as pfile

    for forbidden in (
        "materialize",
        "commit_context_tree",
        "buffered_read",
        "fork_at",
        "projection",


        "create",
    ):
        assert not hasattr(pfile, forbidden), f"file.{forbidden} must be removed"
        assert forbidden not in getattr(pfile, "__all__", [])


def test_pactroot_not_publicly_exported():



    import pactree.components as comps
    from pactree.components import core as comps_core

    for name in ("PactRoot", "PACTNode"):
        assert name not in getattr(comps, "__all__", []), f"{name} in components.__all__"
        assert not hasattr(comps, name), f"components.{name} is importable"
        assert name not in comps_core.__all__, f"{name} in components.core.__all__"
    assert not hasattr(comps_core, "PactRoot"), "PactRoot class still defined"



    assert not hasattr(comps_core, "PACTNode"), "PACTNode still defined"
    assert not hasattr(comps_core, "_PACTNode"), "leftover _PACTNode shim"
    assert not hasattr(comps_core, "PACTCore"), "PACTCore alias still defined"
    assert issubclass(comps_core.TextContent, comps_core.Component)



    for name in ("PACTContainer", "Metadata", "XMLAttributeAccessor", "PACTCore"):
        assert name not in comps_core.__all__, f"{name} advertised in components.core.__all__"


def test_no_raw_store_or_materialize_in_native_module():


    assert "fn pact_store_materialize" not in _NATIVE
    assert "pact_file_materialize" not in _NATIVE


def test_native_bridge_module_is_private():


    import importlib.util

    assert importlib.util.find_spec("pactree.native") is None
    assert not hasattr(pactree, "native")




_FILE = (_SRC / "file.py").read_text()


_FORBIDDEN_FILE_NAMES = {
    "open",
    "project",
    "root_store",
    "merge",
    "append_commit",
    "append_views",
    "append_content_manifest",
    "append_content_store",
    "view_detach",
    "view_merge",
    "view_merge_preflight",
    "view_projection",
    "view_query",
    "view_rebase",
    "repair",
    "cycles",
    "bundle_create",
    "content_manifest_payload",
    "import_slice",
}


_FORBIDDEN_CONTEXTTREE_NAMES = {
    "buffered_read",
    "fork_at",
    "fork_at_node",
    "fork_at_ref",
    "current_ref",
    "head_ref",
    "replace_head",
    "bind_file",
    "bind_session_path",
    "branch_id",
}


def test_file_all_names_all_exist():

    from pactree import file as pfile

    missing = [name for name in getattr(pfile, "__all__", []) if not hasattr(pfile, name)]
    assert not missing, f"file.__all__ advertises missing names: {missing}"


def test_file_all_has_no_tree_operations():


    from pactree import file as pfile

    leaked = _FORBIDDEN_FILE_NAMES & set(getattr(pfile, "__all__", []))
    assert not leaked, f"file.__all__ still exposes tree/store ops: {sorted(leaked)}"


def test_contexttree_source_has_no_raw_plumbing():


    forbidden = (
        "_native_pact_root",
        "context_tree_project_branch",
        "context_tree_from_store",
        "context_tree_new",
        "pact_file_commit_context_tree",
        "pact_store_new",
        "pact_store_checkpoint",
        "pact_store_fork",
    )
    for needle in forbidden:
        assert needle not in _BASE, f"base.py still references {needle}"
        assert needle not in _FILE, f"file.py still references {needle}"


def test_contexttree_runtime_surface_has_no_raw_primitives():



    from pactree import ContextTree

    leaked = _FORBIDDEN_CONTEXTTREE_NAMES & set(dir(ContextTree))
    assert not leaked, f"ContextTree exposes raw primitives: {sorted(leaked)}"


def test_view_reads_staged_tree_not_manual_branch_projection():


    assert "context_tree_project_branch(" not in _BASE
    assert "def view(self)" in _BASE or "def view(" in _BASE


def test_load_contract_is_from_file_not_from_json():




    from pactree import ContextTree

    assert hasattr(ContextTree, "from_file")
    assert not hasattr(ContextTree, "from_json")


def test_builder_is_thin_fluent_authoring():


    from pactree import ContextTree, Placement
    from pactree.components import TextContent

    tree = (
        ContextTree.builder()
        .system("you are helpful")
        .user("hello")
        .append(TextContent(content="note"), Placement(-1, 0))
        .build()
    )
    assert isinstance(tree, ContextTree)
    kinds = {getattr(n, "node_type", None) for n in tree.view().nodes.values()}
    assert "text_content" in kinds


def test_provider_thread_read_routes_through_project():



    from pactree import ContextTree

    ctx = ContextTree()
    ctx.user("q")
    assert ctx.project("provider_thread") == ctx.provider_thread()


def test_native_contexttree_hides_branch_and_storage_internals():



    import inspect

    import pactree.pactree_engine as engine

    assert "branch_id" not in str(inspect.signature(engine.ContextTree.open_file))
    assert "content_ref" not in str(inspect.signature(engine.ContextTree.update))
    assert not hasattr(engine.ContextTree(), "branch_id")


def test_engine_does_not_expose_raw_file_mutation_primitives():



    import pactree.pactree_engine as engine

    for raw in (
        "pact_file_append_commit",
        "pact_file_append_commit_with_content",
        "pact_file_append_views",
        "pact_file_append_content_store",
        "pact_file_append_content_manifest",
        "pact_file_content_manifest_payload",
        "pact_file_content_payload",
        "pact_file_commit_context_tree",
        "pact_file_fork",
    ):
        assert not hasattr(engine, raw), f"pactree_engine still exposes raw mutation fn {raw}"


def test_fork_uses_native_tree_method_not_file_fork_or_branch_id():



    assert "pactree_engine.pact_file_fork" not in _BASE, "fork() still calls raw pact_file_fork"
    assert "_tree.branch_id" not in _BASE, "fork() still threads the native branch id"
    assert "ContextTreeCore.fork(" in _BASE, "fork() should call the native tree fork method"


def test_update_does_not_expose_content_ref():


    import inspect

    from pactree import ContextTree

    params = inspect.signature(ContextTree.update).parameters
    assert "content_ref" not in params, "ContextTree.update still exposes content_ref"
    assert "content_ref=" not in _BASE.replace(" ", ""), (
        "ContextTree.update source still threads content_ref into the native call"
    )
