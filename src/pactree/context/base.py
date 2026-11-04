"""Rust-backed PACT context facade.

This module is intentionally small. Python hosts pass typed intent to Pact and read
typed projections; Rust PACT owns mutation, selector, projection, lifecycle,
trace, seal, head, and storage semantics.

Host boundary
-------------
``ContextTree`` is the Python facade over the native ContextTree. Do not re-create
PACT behavior in Python; add the native operation first and keep this layer a
mechanical forwarder.

New behavior must land in the repository that owns the concern:

* PACT semantics (selectors, coordinate targets, node identity, message
  construction, boundary/lifecycle policy) belong in the Rust ``pactree``
  crate.
* Agent / session / scaffold / provider runtime behavior belongs in Egregore.
* ``ContextTree`` is the public Python surface. Resolver/ledger internals stay
  inside Rust; only add behavior here when it forwards directly to Rust.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from ..components import (
    Component,
    JsonMap,
    JsonValue,
    MessageTurn,
    SystemHeader,
)
from .. import pactree_engine
from ..pactree_engine import ContextTree as ContextTreeCore


@dataclass
class UpdateResult:
    success: bool = True
    updated_components: list[Any] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TraceRef:
    """Result of a public ContextTree write.

    Mirrors the native Rust ``TraceRef``: a trace id, the op kind, and the
    authoritative produced / replaced / deleted component ids. ``component`` is
    Pythonic sugar -- the host component passed to the write, with the minted id
    echoed on when applicable.
    """

    trace_id: str = ""
    op_kind: str = ""
    created_at_ns: int = 0
    produced_component_ids: list[str] = field(default_factory=list)
    replaced_component_ids: list[str] = field(default_factory=list)
    deleted_component_ids: list[str] = field(default_factory=list)
    component: Component | JsonMap | None = None

    @classmethod
    def from_native(cls, trace: JsonMap | None) -> "TraceRef":
        trace = trace or {}
        return cls(
            trace_id=str(trace.get("trace_id") or ""),
            op_kind=str(trace.get("op_kind") or ""),
            created_at_ns=int(trace.get("created_at_ns") or 0),
            produced_component_ids=list(trace.get("produced_component_ids") or []),
            replaced_component_ids=list(trace.get("replaced_component_ids") or []),
            deleted_component_ids=list(trace.get("deleted_component_ids") or []),
        )

    @classmethod
    def from_dict(cls, trace: JsonMap) -> "TraceRef":
        ref = cls.from_native(trace)
        component = trace.get("component")
        if isinstance(component, dict):
            from ..components import component_from_dict

            ref.component = component_from_dict(component)
        return ref

    @classmethod
    def from_json(cls, trace: str) -> "TraceRef":
        payload = json.loads(trace)
        if not isinstance(payload, dict):
            raise TypeError("trace JSON must decode to an object")
        return cls.from_dict(payload)

    def to_dict(self, *, include_component: bool = False) -> JsonMap:
        payload: JsonMap = {
            "trace_id": self.trace_id,
            "op_kind": self.op_kind,
            "created_at_ns": self.created_at_ns,
            "produced_component_ids": list(self.produced_component_ids),
            "replaced_component_ids": list(self.replaced_component_ids),
            "deleted_component_ids": list(self.deleted_component_ids),
        }
        if include_component and self.component is not None:
            from ..components import Component, component_to_dict

            if isinstance(self.component, Component):
                payload["component"] = component_to_dict(self.component)
            elif isinstance(self.component, dict):
                payload["component"] = dict(self.component)
        return payload

    def model_dump(self, **_: JsonValue) -> JsonMap:
        return self.to_dict()

    def to_json(self, *, include_component: bool = False) -> str:
        return json.dumps(
            self.to_dict(include_component=include_component),
            ensure_ascii=False,
            separators=(",", ":"),
        )


class _ContentFacade:
    def __init__(self, context: "ContextTree") -> None:
        self._context = context

    def iter_depth_items(self):
        yield -1, self._context.system_header
        yield 0, self._context.active_message

    def __iter__(self):
        return iter([self._context.system_header, self._context.active_message])

    def __len__(self) -> int:
        return 2

    def __getitem__(self, depth: int):
        if depth == -1:
            return self._context.system_header
        if depth == 0:
            return self._context.active_message
        raise IndexError(depth)

    def depths(self) -> list[int]:
        return [-1, 0]


class ContextTree(ContextTreeCore):
    """Python facade over the Rust-owned context tree.

    ``ContextTree`` is a session/facade object, not a Pact node. New PACT
    semantics belong in the Rust ``pactree`` crate; new agent/session/provider
    runtime belongs in Egregore. Methods here should only forward to the native
    engine -- do not add independent behavior.
    """

    def __new__(
        cls,
        template: Optional[Any] = None,
        *,
        context_tree_id: str | None = None,
        path: str | Path | None = None,
        **kwargs: Any,
    ):
        del template, kwargs
        return ContextTreeCore.__new__(
            cls,
            context_tree_id=context_tree_id,
            path=str(Path(path).expanduser()) if path is not None else None,
        )

    def __init__(
        self,
        template: Optional[Any] = None,
        *,
        context_tree_id: str | None = None,
        path: str | Path | None = None,
        **kwargs: Any,
    ):
        del template
        del context_tree_id
        del path
        if kwargs:
            unknown = ", ".join(sorted(str(key) for key in kwargs))
            warnings.warn(
                f"ContextTree ignored unsupported constructor option(s): {unknown}",
                DeprecationWarning,
                stacklevel=2,
            )
        self._cancelled = False
        self._hooks = None


        self._system_header = SystemHeader(content="")
        self._active_message = MessageTurn(role="user")
        self.content = _ContentFacade(self)


        self._explicit_pact_path: Path | None = None
        self._pact_file_path: Path | None = None

    @property
    def current_episode(self) -> int:
        return self._message_turn_count()

    @current_episode.setter
    def current_episode(self, value: int) -> None:
        del value
        return

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True

    def reset_cancel(self) -> None:
        self._cancelled = False

    @property
    def id(self) -> str:
        """The context-tree id (root component id). Equals the session id when
        constructed via :meth:`with_id`."""
        return str(ContextTreeCore.id.__get__(self, type(self)))

    @property
    def system_header(self) -> SystemHeader:
        return self._system_header

    @property
    def conversation_history(self) -> _ContentFacade:
        return self.content

    @property
    def active_message(self) -> MessageTurn:
        return self._active_message



    @classmethod
    def builder(cls, template: Optional[Any] = None, **kwargs: Any) -> "_ContextTreeBuilder":
        """Return a fluent authoring builder over a fresh ``ContextTree``.

        Pure input ergonomics: every builder method forwards to the underlying
        tree and returns ``self`` for chaining, then :meth:`_ContextTreeBuilder.build`
        hands back the tree. The builder owns NO PACT semantics -- Rust still
        validates, mints ids, places, and classifies on each forwarded call.

        >>> tree = (
        ...     ContextTree.builder()
        ...     .system("you are helpful")
        ...     .user("hello")
        ...     .build()
        ... )
        """
        return _ContextTreeBuilder(cls(template, **kwargs))












    @classmethod
    def with_id(cls, context_tree_id: str) -> "ContextTree":
        """Create a context tree bound to a host/session id.

        The root component id equals this id. Hosts (e.g. Egregore) pass their
        session id here -- it is the ContextTree id, never a branch id.
        """
        return cls(context_tree_id=str(context_tree_id))

    @classmethod
    def from_file(cls, path: str | Path) -> "ContextTree":
        """Open a context from an existing ``.pact`` file.

        Rust reconstructs the resolver from the file; later commits persist
        back to the same path. Python threads only the path. This is the only
        load path -- see the note above on why there is no ``from_json``.
        """
        target = Path(path).expanduser()
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(str(target))
        tree = cls(path=target)
        tree._pact_file_path = target
        tree._explicit_pact_path = target
        return tree

    def save(self, path: str | Path | None = None) -> Path:
        """Persist the context to ``path`` (or the already-bound path).

        Rust owns file creation and the ledger; Python passes only the path.
        After saving, later commits persist to this file.
        """
        if path is not None:
            self._explicit_pact_path = Path(path).expanduser()
        target = self._explicit_pact_path
        if target is None:
            raise RuntimeError("ContextTree.save() requires a path; none is bound yet")
        target.parent.mkdir(parents=True, exist_ok=True)
        ContextTreeCore.save_file(self, str(target))
        self._pact_file_path = target
        return target

    def _ensure_pact_file_bound(self, *, create: bool = False) -> Path | None:



        path = self._explicit_pact_path
        if path is None:
            return None
        if self._pact_file_path == path and path.exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or create:
            ContextTreeCore.save_file(self, str(path))
            self._pact_file_path = path
        return path

    def _node_payload(self, component: Any) -> dict[str, Any]:
        if isinstance(component, Component):
            return component
        if isinstance(component, dict):
            return {key: value for key, value in component.items() if value is not None}
        return str(component)

    def _message_parts_and_aux(self, components: list[Any]) -> tuple[list[Any], dict[str, Any]]:
        """Marshal host components to native message parts.

        Python does NO classification. Rust's ``add_message`` reads ``componentType``
        for object parts and wraps bare strings as ``text_content``; it owns
        text/tool/reasoning/media classification, tool pairing, and boundary
        semantics. This helper only adapts host objects to native values.
        """
        message_aux: dict[str, Any] = {}
        parts: list[Any] = []
        for component in components:
            metadata = getattr(component, "metadata", None)
            aux = getattr(metadata, "aux", None) if metadata is not None else None
            if isinstance(aux, dict):
                policies = aux.get("payload_slot_policies")
                if isinstance(policies, dict) and policies:
                    message_aux.setdefault("payload_slot_policies", {}).update(policies)




            parts.append(self._node_payload(component))
        return parts, message_aux

    def _message_update_result(self, components: list[Any], report: dict[str, Any]) -> UpdateResult:
        child_ids = list(report.get("produced_message_child_ids") or [])
        return UpdateResult(
            updated_components=components,
            metadata={
                "produced_message_ids": list(report.get("produced_message_ids") or []),
                "produced_message_child_ids": child_ids,
                "commit_ref": report.get("commit_ref"),
            },
        )

    @staticmethod
    def _as_component_list(contents: Any) -> list[Any]:
        """Normalize an input-lane value to a list of components.

        A bare string, dict, or component object is a SINGLE item; only an
        explicit list/tuple is expanded. (Without this, ``receive("answer")``
        would iterate the string into one node per character.)
        """
        if contents is None:
            return []
        if isinstance(contents, (list, tuple)):
            return list(contents)
        return [contents]

    def _stage_message(self, role: str, contents: Iterable[Any]) -> UpdateResult:
        """Stage a client-side message for the next send boundary.

        This does not resolve or seal. Pact resolves the staged operation when
        `send()` closes Preflight.
        """

        components = self._as_component_list(contents)
        if not components:
            return UpdateResult()
        parts, message_aux = self._message_parts_and_aux(components)
        self._ensure_pact_file_bound(create=True)
        outcome = ContextTreeCore.add_message(
            self,
            role,
            parts,
            message_aux,
        )
        rejected = outcome.get("rejected") or []
        if rejected:
            first = rejected[0]
            raise ValueError(first.get("reason") or f"native add_message rejected {len(rejected)} operation(s)")
        return self._message_update_result(components, outcome)

    def receive(self, contents: Iterable[Any] | None = None) -> UpdateResult:
        """Close the provider-response side of the lifecycle.

        If ``contents`` are provided, Pactree stages them as the assistant
        provider response and seals PostResponse internally. If streaming code
        has already staged response mutations, call ``receive()`` with no
        contents to close the staged response as-is.
        """

        components = self._as_component_list(contents)
        if not components:
            self._ensure_pact_file_bound(create=True)
            report = ContextTreeCore.receive(self)
            return self._message_update_result([], report)
        parts, message_aux = self._message_parts_and_aux(components)
        self._ensure_pact_file_bound(create=True)
        report = ContextTreeCore.receive(self, parts, message_aux)
        rejected = report.get("rejected") or []
        if rejected:
            first = rejected[0]
            raise ValueError(first.get("reason") or f"native provider response rejected {len(rejected)} operation(s)")
        return self._message_update_result(components, report)



    def add_message(self, role: str, content: Any) -> UpdateResult:
        return self._stage_message(role, self._as_component_list(content))

    def system(self, content: Any) -> UpdateResult:
        return self.add_system(content)

    def user(self, content: Any) -> UpdateResult:
        return self.add_user(content)

    def assistant(self, content: Any) -> UpdateResult:
        return self.add_assistant(content)

    def add_system(self, content: Any) -> UpdateResult:
        return self._stage_message("system", [content])

    def add_user(self, content: Any) -> UpdateResult:
        return self._stage_message("user", [content])

    def add_user_batch(self, contents: Iterable[Any]) -> UpdateResult:
        return self._stage_message("user", contents)

    def add_assistant(self, content: Any) -> UpdateResult:
        return self._stage_message("assistant", [content])

    def add_assistant_batch(self, contents: Iterable[Any]) -> UpdateResult:
        return self._stage_message("assistant", contents)

    def message(self, content: Any, *, role: str = "user", op: str = "add") -> UpdateResult:
        """Record a message in the context tree.

        `op` is reserved for future update/replace semantics; the current
        engine-backed Python API supports additive message operations.
        """

        if op != "add":
            raise ValueError(f"unsupported message op: {op}")
        if role == "system":
            return self.system_message(content)
        if role == "user":
            return self.user_message(content)
        if role == "assistant":
            return self.assistant_message(content)
        raise ValueError(f"unsupported message role: {role}")

    def system_message(self, content: Any) -> UpdateResult:
        return self._stage_message("system", [content])

    def user_message(self, content: Any) -> UpdateResult:
        return self._stage_message("user", [content])

    def assistant_message(self, content: Any) -> UpdateResult:
        return self._stage_message("assistant", [content])

    def user_messages(self, contents: Iterable[Any]) -> UpdateResult:
        return self._stage_message("user", contents)

    def assistant_messages(self, contents: Iterable[Any]) -> UpdateResult:
        return self._stage_message("assistant", contents)









    def _canonical_node(self, component: Any) -> dict[str, Any]:



        return self._node_payload(component)

    def _trace_result(self, component: Any, trace: dict[str, Any]) -> "TraceRef":
        """Wrap a native trace dict.

        The authoritative ids live on the returned ``TraceRef``. Python never
        writes those ids back into authoring components.
        """
        ref = TraceRef.from_native(trace)
        ref.component = component
        return ref

    def insert(self, component: Any, placement: Any) -> "TraceRef":
        """Insert ``component`` at ``placement`` (a :class:`Placement`).

        Rust resolves the placement and mints the component id; the public API
        never threads selectors or parent ids.
        """
        payload = self._canonical_node(component)
        self._ensure_pact_file_bound(create=True)
        trace = ContextTreeCore.insert(self, payload, placement)
        return self._trace_result(component, trace)

    def append(self, component: Any, placement: Any) -> "TraceRef":
        """Append ``component`` under ``placement`` (a :class:`Placement`)."""
        payload = self._canonical_node(component)
        self._ensure_pact_file_bound(create=True)
        trace = ContextTreeCore.append(self, payload, placement)
        return self._trace_result(component, trace)

    def update(
        self,
        component_id: str,
        *,
        content: Any = None,
        fields: dict[str, Any] | None = None,
        aux: dict[str, Any] | None = None,
    ) -> "TraceRef":
        """In-place update of the component named by ``component_id``.

        The target is always a plain component id. Rust owns the in-place
        field/content mutation. Storage refs are engine internals and are not a
        public update argument; use visible ``content``, ``fields``, or ``aux``.
        """
        self._ensure_pact_file_bound(create=True)
        trace = ContextTreeCore.update(
            self,
            component_id,
            content=content,
            fields=fields,
            aux=aux,
        )
        return TraceRef.from_native(trace)

    def replace(self, component: Any, component_id: str) -> "TraceRef":
        """Replace the component named by ``component_id`` with ``component``."""
        payload = self._canonical_node(component)
        self._ensure_pact_file_bound(create=True)
        trace = ContextTreeCore.replace(self, payload, component_id)
        return self._trace_result(component, trace)

    def upsert(
        self,
        component: Any,
        component_id: str | None = None,
        placement: Any = None,
    ) -> "TraceRef":
        """Replace ``component_id`` if present, else insert at ``placement``."""
        payload = self._canonical_node(component)
        self._ensure_pact_file_bound(create=True)
        trace = ContextTreeCore.upsert(self, payload, component_id, placement)
        return self._trace_result(component, trace)

    def prune(self, component_id: str) -> "TraceRef":
        """Prune (delete) the component/subtree named by ``component_id``."""
        self._ensure_pact_file_bound(create=True)
        trace = ContextTreeCore.prune(self, component_id)
        return TraceRef.from_native(trace)

    def move(
        self,
        target: str,
        destination: str,
        *,
        mode: str | None = None,
        offset: int | None = None,
    ) -> "TraceRef":
        """Move (reparent) an EXISTING component — same identity, one trace.

        Both ends speak the selector grammar (``#id``, ``.type``, ``+tag``,
        ``dN``); the destination must name exactly one place. ``mode``
        reuses the placement vocabulary: ``insert`` (default; occupants
        shift), ``append`` (container + offset rule), ``replace`` (the
        occupant dies in the same op — casualties land in the trace).
        Message containers are immovable (v1): only elements move.
        """
        self._ensure_pact_file_bound(create=True)
        trace = ContextTreeCore.move_component(
            self, str(target), str(destination), mode, offset
        )
        return TraceRef.from_native(trace)

    def select(
        self,
        selector: str,
        trace_checkpoint: Optional[str] = None,
        *,
        include_archived: bool = False,
    ) -> list[Any]:
        """Run a PACT selector through Rust and return its native projection."""
        return ContextTreeCore.select(self, str(selector), include_archived=include_archived)

    def _message_turn_count(self) -> int:
        return int(ContextTreeCore.message_count(self))

    def model_dump(self, **_: Any) -> dict[str, Any] | list[Any]:
        return self.to_pact()

    def render(self, **_: Any) -> str:


        return ContextTreeCore.project_xml(self)

    def render_to_thread(self, provider_surface: Any = None):
        return self.provider_thread(
            include_partial_tool_calls=True,
            fail_on_orphan_tool_calls=False,
        )

    def to_pact(self) -> dict[str, Any] | list[Any]:
        return ContextTreeCore.preview(self)

    def preview(self, *, as_view: bool = False):
        """Render the current STAGED-INCLUSIVE shape (all uncommitted
        transactions applied) — the primary "what does the tree look like now"
        read. Returns the materialized tree (``{id, componentType, children}``).

        ``as_view=True`` returns the staged-inclusive ``PactView`` — a
        Rust-owned pyclass produced by the engine (no JSON, no Python tree walk).
        """
        if not as_view:
            return ContextTreeCore.preview(self)

        return ContextTreeCore.preview_view(self)

    def view(self, fmt: str = "dataclass"):
        """The current staged-inclusive view as a ``PactView`` (default) or JSON.

        ``tree.view()`` returns the Rust-owned ``PactView`` pyclass of the moving
        staged tree (all uncommitted transactions applied) — the primary "what
        does the tree look like now" read. ``tree.view("json")`` returns its
        JSON string. Rust owns projection; Python never manually projects a
        branch from a root store.
        """
        view = ContextTreeCore.preview_view(self)
        if fmt == "json":
            return view.to_json()
        if fmt not in ("dataclass", "view"):
            raise ValueError(f"unsupported view fmt {fmt!r}; use 'dataclass' or 'json'")
        return view

    def project(self, format: str = "python"):
        """Serialize the current staged-inclusive context.

        - ``"python"``          -> the materialized tree (``{id, componentType, children}``)
        - ``"json"``            -> the ``PactView`` JSON string
        - ``"xml"``             -> the rendered provider/XML thread
        - ``"provider_thread"`` -> the read-only provider-thread projection

        Projection is owned by Rust; Python only selects the output shape.
        ``project("provider_thread")`` is the canonical read-only route; the
        standalone :meth:`provider_thread` stays as the parameterized diagnostic
        (partial tool calls / orphan policy).
        """
        if format == "python":
            return ContextTreeCore.preview(self)
        if format == "json":
            return ContextTreeCore.preview_view(self).to_json()
        if format == "xml":
            return ContextTreeCore.project_xml(self)
        if format == "xml_archive":


            return ContextTreeCore.project_xml(self, "archive")
        if format == "provider_thread":
            return self.provider_thread()
        raise ValueError(
            f"unsupported project format {format!r}; use 'python', 'json', "
            "'xml', 'xml_archive', or 'provider_thread'"
        )

    def traces(self) -> list[dict[str, Any]]:
        """Return native staged trace handles for inspection and discard."""

        return ContextTreeCore.staged_traces(self)

    def discard_trace(self, trace_id: str) -> bool:
        """Discard one staged trace by id through native resolver semantics."""

        return ContextTreeCore.discard_staged_trace(self, str(trace_id))

    def discard(self) -> bool:
        """Drop all staged-but-unsealed work for this branch."""

        return ContextTreeCore.discard_staged(self)

    def seal_staged(self, boundary: str = "adhoc") -> dict[str, Any] | None:
        """Seal staged work at a native Pact boundary without provider send."""

        self._ensure_pact_file_bound(create=True)
        return ContextTreeCore.seal_staged(self, boundary)





    def fork(
        self,
        path: str | Path | None = None,
        *,
        mode: str = "hard",
        at: int | None = None,
    ) -> "ContextTree":
        """Fork this context.

        ``mode``:
        - ``"hard"``    — full, independent copy (no shared history)
        - ``"soft"``    — lightweight branch sharing prior history
        - ``"overlay"`` — in-memory *reference* fork: a ``fork_ref`` branch in a
          clone of this ledger (same root, no minted root, no ``.pact`` written).
          SEES this context's content (composed base + local on read AND send)
          while writing only to its own branch — this context is never touched.
          ``path``/``at`` do not apply; the overlay is ephemeral and unbound.

        ``at``: fork FROM HISTORY — the branch revision to fork at
        (defaults to head). Rewind composes on this: fork-at + restore.

        With ``path`` the fork is written to a new ``.pact`` file; without the
        fork is a new branch in the current file. Rust owns fork semantics and
        branch ids; callers never supply a branch id.
        """
        if mode not in ("hard", "soft", "overlay"):
            raise ValueError(
                f"unsupported fork mode {mode!r}; use 'hard', 'soft', or 'overlay'"
            )
        if mode == "overlay":





            forked = ContextTree()
            forked.fork_branch_id = ContextTreeCore._adopt_reference_fork(
                forked, self
            )
            return forked
        source = self._pact_file_path or self._ensure_pact_file_bound(create=True)
        if source is None:
            raise RuntimeError("fork() requires a saved context; call save(path) first")
        target = Path(path).expanduser() if path is not None else None



        report_json = ContextTreeCore.fork(
            self,
            str(source),
            str(target) if target is not None else None,
            mode,
            int(at) if at is not None else None,
        )


        forked_path = target if (target is not None and target.exists()) else source
        forked = ContextTree.from_file(forked_path)


        try:
            report = json.loads(report_json) if isinstance(report_json, str) else report_json
        except Exception:
            report = None
        if isinstance(report, dict):
            forked.fork_branch_id = (
                report.get("branch_id")
                or report.get("new_branch_id")
                or report.get("target_branch_id")
            )
            forked.fork_report = report
        return forked

    @property
    def file(self) -> "_FileFacade":
        """File-admin operations bound to this tree's ``.pact`` path.

        File admin hangs off the bound tree (``ctx.file.info()`` etc.), not a
        free path-taking module. Rust owns the file ledger; this facade only
        supplies the tree's current path.
        """
        return _FileFacade(self)

    def search(self, query: Any, *, mode: str = "text") -> Any:
        """Search this tree's persisted context.

        ``mode="text"`` (default) runs the native text/regex search. A bare
        string is the common happy path (``ctx.search("needle")``); a mapping
        passes the full native query (``text``/``selector``/``scope``/``regex``/
        ``limit``/``preview_chars``/...). Rust owns selector filtering, historical
        scope, archive hydration, and result shape. Vector/embedding modes are
        reserved until the engine exposes a native tree-level search API.
        """
        if mode == "text":
            path = self.file._require_path()
            native_query = {"text": query} if isinstance(query, str) else dict(query)


            return pactree_engine._pact_file_text_search(str(path), native_query)
        if mode in ("vector", "embedding", "embeddings"):
            raise NotImplementedError(
                f"search mode {mode!r} is not yet exposed by the native engine; "
                "only 'text' is currently supported"
            )
        raise ValueError(
            f"unsupported search mode {mode!r}; use 'text' "
            "(vector/embedding are reserved for native support)"
        )

    def provider_thread(
        self,
        *,
        include_partial_tool_calls: bool = False,
        fail_on_orphan_tool_calls: bool = True,
    ) -> dict[str, Any]:
        """Read-only provider-thread projection (no lifecycle seal).

        Deliberate diagnostic/inspection surface — does NOT advance the
        lifecycle. The strict send path is `send(...)` (seals Preflight); the
        canonical no-arg read is `project("provider_thread")`. This parameterized
        form is kept for callers that need the partial-tool-call / orphan-policy
        knobs (e.g. Egregore's ProviderSurface). (Formerly named
        `provider_thread_readonly`.)
        """
        return ContextTreeCore.build_provider_thread(
            self,
            include_partial_tool_calls=include_partial_tool_calls,
            fail_on_orphan_tool_calls=fail_on_orphan_tool_calls,
        )

    def send(
        self,
        *,
        include_partial_tool_calls: bool = False,
        fail_on_orphan_tool_calls: bool = True,
    ) -> dict[str, Any]:
        self._ensure_pact_file_bound(create=True)
        return ContextTreeCore.send(
            self,
            include_partial_tool_calls=include_partial_tool_calls,
            fail_on_orphan_tool_calls=fail_on_orphan_tool_calls,
        )

class _ContextTreeBuilder:
    """Fluent authoring builder for :meth:`ContextTree.builder`.

    Thin chaining sugar only: each method forwards to the wrapped
    ``ContextTree`` and returns ``self``. It deliberately holds no PACT state and
    performs no classification, id minting, or placement -- those stay in Rust.
    Not part of the public ``__all__``; obtained via ``ContextTree.builder()``.
    """

    def __init__(self, tree: "ContextTree") -> None:
        self._context = tree

    def system(self, content: Any) -> "_ContextTreeBuilder":
        self._context.system(content)
        return self

    def user(self, content: Any) -> "_ContextTreeBuilder":
        self._context.user(content)
        return self

    def assistant(self, content: Any) -> "_ContextTreeBuilder":
        self._context.assistant(content)
        return self

    def add_message(self, role: str, content: Any) -> "_ContextTreeBuilder":
        self._context.add_message(role, content)
        return self

    def append(self, component: Any, placement: Any) -> "_ContextTreeBuilder":
        self._context.append(component, placement)
        return self

    def insert(self, component: Any, placement: Any) -> "_ContextTreeBuilder":
        self._context.insert(component, placement)
        return self

    def build(self) -> "ContextTree":
        """Return the assembled ``ContextTree``."""
        return self._context


class _FileFacade:
    """File-admin operations bound to one :class:`ContextTree`'s ``.pact`` path.

    File operations hang off the bound tree (``ctx.file.*``), not a free
    path-taking module. Each call resolves the tree's current bound path through
    Rust's binding policy; an unbound tree raises a clear error. The methods
    delegate to the narrow ``pactree.file`` engine plumbing.
    """

    __slots__ = ("_context",)

    def __init__(self, tree: "ContextTree") -> None:
        self._context = tree

    def _require_path(self) -> Path:
        path = self._context._ensure_pact_file_bound(create=True)
        if path is None:
            raise RuntimeError(
                "this ContextTree is not bound to a .pact file; call save(path) "
                "or open it with ContextTree.from_file(path) first"
            )
        return path

    def info(self) -> dict[str, Any]:
        from .. import file as _file

        return _file.info(self._require_path())

    def inspect(self) -> dict[str, Any]:
        from .. import file as _file

        return _file.inspect(self._require_path())

    def validate(self) -> dict[str, Any]:
        from .. import file as _file

        return _file.validate(self._require_path())

    def bundle_verify(self) -> dict[str, Any]:
        from .. import file as _file

        return _file.bundle_verify(self._require_path())

    def bundle_export_full(self, output_path: str | Path) -> dict[str, Any]:
        from .. import file as _file

        return _file.bundle_export_full(
            self._require_path(), Path(output_path).expanduser()
        )

    def bundle_prune(self) -> dict[str, Any]:
        from .. import file as _file

        return _file.bundle_prune(self._require_path())

    def slice(self, output_path: str | Path, spec: dict[str, Any] | str) -> dict[str, Any]:
        from .. import file as _file

        return _file.slice(self._require_path(), Path(output_path).expanduser(), spec)

    def import_check(self) -> dict[str, Any]:
        from .. import file as _file

        return _file.import_check(self._require_path())


__all__ = ["ContextTree", "UpdateResult", "TraceRef"]
