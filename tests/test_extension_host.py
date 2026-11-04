from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import pactree
from pactree.extensions import (
    ExtensionActivationError,
    ExtensionCapabilityError,
    ExtensionCompatibilityError,
    ExtensionConfigurationError,
    ExtensionDescriptor,
    ExtensionForkError,
    ExtensionNotFoundError,
    ExtensionOrderError,
    PreparedFork,
)
from pactree.extensions import host as extension_host
from pactree.extensions.services import CommitStream


class _EntryPoint:
    def __init__(self, descriptor: ExtensionDescriptor):
        self.name = descriptor.name
        self._descriptor = descriptor

    def load(self):
        return lambda: self._descriptor


class _EntryPoints(tuple):
    def select(self, *, group: str):
        assert group == "pactree.extensions"
        return self


def _install(monkeypatch, *descriptors: ExtensionDescriptor) -> None:
    entry_points = _EntryPoints(_EntryPoint(item) for item in descriptors)
    monkeypatch.setattr(
        extension_host.importlib.metadata,
        "entry_points",
        lambda: entry_points,
    )


def _state_roots(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PACTREE_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("PACTREE_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))


def _pact(path: Path) -> None:
    tree = pactree.ContextTree.with_id(f"tree-{path.stem}")
    tree.save(path)


@dataclass
class _Lifecycle:
    name: str
    context: Any
    dependency: Any
    events: list[str]

    def activate(self):
        self.events.append(f"activate:{self.name}")
        return self

    def close(self) -> None:
        self.events.append(f"close:{self.name}")


def _derived(
    name: str,
    events: list[str],
    *,
    factory=None,
) -> ExtensionDescriptor:
    return ExtensionDescriptor(
        name=name,
        authority="derived",
        fork_strategy="follow",
        record_prefixes=(f"{name}.",),
        factory=factory
        or (lambda context, dependency: _Lifecycle(name, context, dependency, events)),
    )


def test_top_level_open_discovers_in_order_preserves_injection_and_closes_reverse(
    monkeypatch, tmp_path
):
    _state_roots(monkeypatch, tmp_path)
    path = tmp_path / "session.pact"
    _pact(path)
    events: list[str] = []
    embeddings = object()
    first = _derived("first", events)
    semantic = _derived("semantic_search", events)
    _install(monkeypatch, first, semantic)

    with pactree.open(
        path,
        extensions=["first", {"semantic_search": embeddings}],
    ) as pact:
        assert pact.first is pact.extensions["first"]
        assert pact.semantic_search is pact.extensions["semantic_search"]
        assert pact.semantic_search.dependency is embeddings
        assert pactree.file.open(path)["revision"] == pact.snapshot.context.revision
        assert pact.first.context.pact_path == path.resolve()
        assert pact.first.context.shared_data_dir == tmp_path / "data" / "extensions" / "first"
        assert pact.first.context.data_dir.is_relative_to(
            pact.first.context.shared_data_dir
        )
        assert pact.first.context.data_dir.is_relative_to(tmp_path / "data")
        assert pact.first.context.cache_dir.is_relative_to(tmp_path / "cache")
        assert pact.first.context.runtime_dir.is_relative_to(tmp_path / "runtime")

    assert events == [
        "activate:first",
        "activate:semantic_search",
        "close:semantic_search",
        "close:first",
    ]


@pytest.mark.parametrize(
    "extensions",
    [
        "fs",
        ["fs", "fs"],
        [{"fs": None, "semantic_search": None}],
        [42],
        ["not-valid-name"],
    ],
)
def test_malformed_and_duplicate_extension_specs_are_typed(
    monkeypatch, tmp_path, extensions
):
    _state_roots(monkeypatch, tmp_path)
    path = tmp_path / "invalid.pact"
    _pact(path)
    _install(monkeypatch)

    with pytest.raises(ExtensionConfigurationError):
        pactree.open(path, extensions=extensions)


def test_unknown_extension_is_typed(monkeypatch, tmp_path):
    _state_roots(monkeypatch, tmp_path)
    path = tmp_path / "unknown.pact"
    _pact(path)
    _install(monkeypatch)

    with pytest.raises(ExtensionNotFoundError, match="not installed"):
        pactree.open(path, extensions=["missing"])


def test_incompatible_protocol_and_authority_order_fail_before_activation(
    monkeypatch, tmp_path
):
    _state_roots(monkeypatch, tmp_path)
    path = tmp_path / "compat.pact"
    _pact(path)
    events: list[str] = []
    incompatible = ExtensionDescriptor(
        name="future",
        api_version="99",
        record_prefixes=("future.",),
        factory=lambda context, dependency: _Lifecycle(
            "future", context, dependency, events
        ),
    )
    _install(monkeypatch, incompatible)
    with pytest.raises(ExtensionCompatibilityError, match="unsupported API"):
        pactree.open(path, extensions=["future"])
    assert events == []

    derived = _derived("derived", events)
    authoritative = ExtensionDescriptor(
        name="authority",
        authority="authoritative",
        fork_strategy="authoritative",
        record_prefixes=("authority.",),
        factory=lambda context, dependency: _Lifecycle(
            "authority", context, dependency, events
        ),
    )
    _install(monkeypatch, derived, authoritative)
    with pytest.raises(ExtensionOrderError, match="authoritative extensions"):
        pactree.open(path, extensions=["derived", "authority"])
    assert events == []


def test_partial_activation_closes_each_opened_extension_exactly_once(
    monkeypatch, tmp_path
):
    _state_roots(monkeypatch, tmp_path)
    path = tmp_path / "partial.pact"
    _pact(path)
    events: list[str] = []
    first = _derived("first", events)

    def fail(_context, _dependency):
        raise RuntimeError("boom")

    second = _derived("second", events, factory=fail)
    _install(monkeypatch, first, second)

    with pytest.raises(ExtensionActivationError):
        pactree.open(path, extensions=["first", "second"])

    assert events == ["activate:first", "close:first"]


def test_refresh_rebinds_to_a_new_head_without_mutating_the_held_handle(
    monkeypatch, tmp_path
):
    _state_roots(monkeypatch, tmp_path)
    path = tmp_path / "refresh.pact"
    _pact(path)
    _install(monkeypatch)
    pact = pactree.open(path)
    held_handle = pact.read_handle
    held_snapshot = pact.snapshot

    writer = pactree.open_extension_writer(path)
    writer.append(
        [
            {
                "schema": "fixture.event.v1",
                "kind": "fixture.event",
                "record_id": "refresh-event",
                "payload": {},
                "index_keys": [],
            }
        ]
    )
    refreshed = pact.refresh()

    assert held_handle.info()["revision"] == held_snapshot.context.revision
    assert refreshed.context.revision == held_snapshot.context.revision + 1
    pact.close()


def test_refresh_retires_previous_extension_generation_before_final_close(
    monkeypatch, tmp_path
):
    _state_roots(monkeypatch, tmp_path)
    path = tmp_path / "refresh-lifecycle.pact"
    _pact(path)
    events: list[str] = []
    generations: list[Any] = []

    class Generation:
        def __init__(self, generation: int):
            self.generation = generation

        def activate(self):
            events.append(f"activate:{self.generation}")
            return self

        def retire(self):
            events.append(f"retire:{self.generation}")

        def close(self):
            events.append(f"close:{self.generation}")

    def factory(_context, _dependency):
        instance = Generation(len(generations))
        generations.append(instance)
        return instance

    descriptor = _derived("maintainer", events, factory=factory)
    _install(monkeypatch, descriptor)

    with pactree.open(path, extensions=["maintainer"]) as pact:
        pact.refresh()
        assert events == ["activate:0", "activate:1", "retire:0"]

    assert events == [
        "activate:0",
        "activate:1",
        "retire:0",
        "retire:1",
        "close:1",
        "close:0",
    ]


def test_read_only_open_never_hydrates_contexttree_or_opens_a_writer(
    monkeypatch, tmp_path
):
    _state_roots(monkeypatch, tmp_path)
    path = tmp_path / "bounded.pact"
    _pact(path)
    observed: dict[str, Any] = {}

    def factory(context, dependency):
        observed["context"] = context
        return _Lifecycle("bounded", context, dependency, [])

    descriptor = _derived("bounded", [], factory=factory)
    _install(monkeypatch, descriptor)

    def fail(*_args, **_kwargs):
        raise AssertionError("generic extension host must not hydrate ContextTree")

    monkeypatch.setattr(pactree.ContextTree, "from_file", fail)
    with pactree.open(path, extensions=["bounded"]):
        context = observed["context"]
        assert context.transaction._coordinator.writer_opened is False


def test_coordinated_write_publishes_a_commit_and_generic_records_remain_readable(
    monkeypatch, tmp_path
):
    _state_roots(monkeypatch, tmp_path)
    path = tmp_path / "writer.pact"
    _pact(path)
    observed: dict[str, Any] = {}

    def factory(context, dependency):
        observed["context"] = context
        return _Lifecycle("writer", context, dependency, [])

    descriptor = _derived("writer", [], factory=factory)
    _install(monkeypatch, descriptor)
    with pactree.open(path, extensions=["writer"]) as pact:
        context = observed["context"]
        commit = context.transaction.commit(
            [
                {
                    "schema": "writer.event.v1",
                    "kind": "writer.event",
                    "record_id": "writer-event",
                    "payload": {"ok": True},
                    "index_keys": [],
                }
            ]
        )
        event = context.commit_stream.after(0)
        assert commit["revision"] == 1
        assert len(event) == 1
        assert event[0].transaction_id == commit["transaction_id"]

    _install(monkeypatch)
    with pactree.open(path) as pact:
        assert pact.extension_record("writer-event")["record"]["payload"] == {
            "ok": True
        }
        assert pact.extension_records(kind="writer.event", limit=10)["rows"]


def test_commit_stream_lifecycle_wake_is_level_triggered() -> None:
    stream = CommitStream()
    wake_sequence = stream.wake_sequence
    stream.wake()
    started_at = time.monotonic()

    events, next_wake_sequence = stream.wait_after_or_wake(
        0,
        wake_sequence,
        timeout=1,
    )

    assert events == ()
    assert next_wake_sequence == wake_sequence + 1
    assert time.monotonic() - started_at < 0.2


def test_verified_external_reference_registry_is_shared_in_activation_order(
    monkeypatch, tmp_path
):
    _state_roots(monkeypatch, tmp_path)
    path = tmp_path / "external.pact"
    _pact(path)
    observed: dict[str, bytes] = {}

    def source_factory(context, dependency):
        context.external_refs.register(
            kind="fixture.cas",
            resolver=lambda *, reference, max_bytes: reference["value"].encode()[:max_bytes],
        )
        return _Lifecycle("source", context, dependency, [])

    def consumer_factory(context, dependency):
        observed["payload"] = context.external_refs.resolve(
            kind="fixture.cas",
            reference={"value": "verified"},
            max_bytes=8,
        )
        return _Lifecycle("consumer", context, dependency, [])

    source = _derived("source", [], factory=source_factory)
    consumer = _derived("consumer", [], factory=consumer_factory)
    _install(monkeypatch, source, consumer)

    with pactree.open(path, extensions=["source", "consumer"]):
        assert observed["payload"] == b"verified"


def test_refresh_uses_a_new_external_reference_registry_generation(
    monkeypatch, tmp_path
):
    _state_roots(monkeypatch, tmp_path)
    path = tmp_path / "external-refresh.pact"
    _pact(path)
    registries: list[Any] = []

    def source_factory(context, dependency):
        registries.append(context.external_refs)
        context.external_refs.register(
            kind="fixture.cas",
            resolver=lambda *, reference, max_bytes: b"stable"[:max_bytes],
        )
        return _Lifecycle("source", context, dependency, [])

    source = _derived("source", [], factory=source_factory)
    _install(monkeypatch, source)

    with pactree.open(path, extensions=["source"]) as pact:
        pact.refresh()
        assert len(registries) == 2
        assert registries[0] is not registries[1]
        for registry in registries:
            assert registry.resolve(
                kind="fixture.cas",
                reference={},
                max_bytes=6,
            ) == b"stable"


def test_capability_and_record_prefix_collisions_fail_before_factory(
    monkeypatch, tmp_path
):
    _state_roots(monkeypatch, tmp_path)
    path = tmp_path / "policy.pact"
    _pact(path)
    called: list[str] = []
    privileged = ExtensionDescriptor(
        name="privileged",
        record_prefixes=("privileged.",),
        required_capabilities=frozenset({"process.spawn"}),
        factory=lambda context, dependency: called.append("privileged"),
    )
    _install(monkeypatch, privileged)
    with pytest.raises(ExtensionCapabilityError, match="process.spawn"):
        pactree.open(path, extensions=["privileged"])
    assert called == []

    first = _derived("first", called)
    second = ExtensionDescriptor(
        name="second",
        record_prefixes=("first.child.",),
        factory=lambda context, dependency: called.append("second"),
    )
    _install(monkeypatch, first, second)
    with pytest.raises(ExtensionCompatibilityError, match="record prefix collision"):
        pactree.open(path, extensions=["first", "second"])
    assert called == []


def test_state_schema_downgrade_is_rejected_without_touching_runtime(
    monkeypatch, tmp_path
):
    _state_roots(monkeypatch, tmp_path)
    path = tmp_path / "schema.pact"
    _pact(path)
    events: list[str] = []
    version_two = ExtensionDescriptor(
        name="schema",
        state_schema_version=2,
        record_prefixes=("schema.",),
        factory=lambda context, dependency: _Lifecycle(
            "schema", context, dependency, events
        ),
    )
    _install(monkeypatch, version_two)
    with pactree.open(path, extensions=["schema"]):
        pass

    version_one = ExtensionDescriptor(
        name="schema",
        state_schema_version=1,
        record_prefixes=("schema.",),
        factory=lambda context, dependency: _Lifecycle(
            "schema", context, dependency, events
        ),
    )
    _install(monkeypatch, version_one)
    with pytest.raises(ExtensionCompatibilityError, match="cannot downgrade"):
        pactree.open(path, extensions=["schema"])


class _Environment:
    def __init__(self, context, events: list[str]):
        self.context = context
        self.events = events

    def activate(self):
        return self

    def close(self):
        self.events.append(f"close:{self.context.pact_file_id}")

    def snapshot_view(self):
        page = self.context.read_handle.extensions(
            kind="fixture.checkpoint",
            direction="before",
            limit=1,
            at_revision=self.context.held_revision,
        )
        row = page["rows"][-1]
        return row["record"]["payload"]

    def view_at_revision(self, revision: int):
        page = self.context.read_handle.extensions(
            kind="fixture.checkpoint",
            direction="before",
            limit=1,
            at_revision=revision,
        )
        return page["rows"][-1]["record"]["payload"]

    def prepare_fork(
        self,
        *,
        source_view,
        context,
        child_scope_id,
        child_path,
    ):
        self.events.append(f"prepare:{child_scope_id}")

        def bind_child(child_context):
            checkpoint = f"child-{child_context.context_tree_id}"
            self.events.append(f"bind:{checkpoint}")
            view = {
                "namespace_id": source_view["namespace_id"],
                "branch_id": checkpoint,
                "checkpoint_id": checkpoint,
            }
            records = [
                {
                    "schema": "fixture.checkpoint.v1",
                    "kind": "fixture.checkpoint",
                    "record_id": checkpoint,
                    "payload": view,
                    "index_keys": [f"checkpoint:{checkpoint}"],
                }
            ]
            return view, records

        return PreparedFork(
            extension="fixture",
            bind_child=bind_child,
            finalize=lambda: self.events.append(f"commit:{child_scope_id}"),
            abort=lambda: self.events.append(f"abort:{child_scope_id}"),
        )


class _FailingForkEnvironment(_Environment):
    def __init__(self, context, events: list[str], stage: str):
        super().__init__(context, events)
        self.stage = stage

    def prepare_fork(self, **kwargs):
        plan = super().prepare_fork(**kwargs)
        if self.stage == "bind":
            plan.bind_child = lambda _context: (_ for _ in ()).throw(
                RuntimeError("bind failed")
            )
        elif self.stage == "finalize":
            plan.finalize = lambda: (_ for _ in ()).throw(
                RuntimeError("finalize failed")
            )
        return plan


def _fork_failure_parent(monkeypatch, tmp_path, stage):
    path = tmp_path / f"{stage}-parent.pact"
    _pact(path)
    pactree.open_extension_writer(path).append(
        [
            {
                "schema": "fixture.checkpoint.v1",
                "kind": "fixture.checkpoint",
                "record_id": "parent-checkpoint",
                "payload": {
                    "namespace_id": "fixture-namespace",
                    "branch_id": "parent",
                    "checkpoint_id": "parent-checkpoint",
                },
                "index_keys": ["checkpoint:parent-checkpoint"],
            }
        ]
    )
    events = []
    descriptor = ExtensionDescriptor(
        name="fixture",
        authority="authoritative",
        fork_strategy="authoritative",
        record_prefixes=("fixture.",),
        factory=lambda context, _dependency: _FailingForkEnvironment(
            context,
            events,
            stage,
        ),
    )
    _install(monkeypatch, descriptor)
    return path, events


def test_fork_failure_before_canonical_seal_removes_child_and_aborts_once(
    monkeypatch, tmp_path
):
    _state_roots(monkeypatch, tmp_path)
    path, events = _fork_failure_parent(monkeypatch, tmp_path, "bind")
    child_path = tmp_path / "unsealed-child.pact"

    with pactree.open(path, extensions=["fixture"]) as parent:
        with pytest.raises(ExtensionForkError, match="before canonical seal"):
            parent.fork(path=child_path)

    assert not child_path.exists()
    assert sum(event.startswith("abort:") for event in events) == 1
    assert not any(event.startswith("commit:") for event in events)


def test_fork_failure_after_canonical_seal_retains_readable_repair_target(
    monkeypatch, tmp_path
):
    _state_roots(monkeypatch, tmp_path)
    path, events = _fork_failure_parent(monkeypatch, tmp_path, "finalize")
    child_path = tmp_path / "sealed-child.pact"

    with pactree.open(path, extensions=["fixture"]) as parent:
        with pytest.raises(ExtensionForkError, match="retained for repair"):
            parent.fork(path=child_path)

    assert child_path.exists()
    with pactree.open(child_path, extensions=["fixture"]) as repaired:
        assert repaired.fixture.snapshot_view()["checkpoint_id"].startswith("child-")
    assert sum(event.startswith("abort:") for event in events) == 1


def test_context_only_fork_still_creates_a_distinct_authoritative_child(
    monkeypatch, tmp_path
):
    _state_roots(monkeypatch, tmp_path)
    path = tmp_path / "parent.pact"
    _pact(path)
    pactree.open_extension_writer(path).append(
        [
            {
                "schema": "fixture.checkpoint.v1",
                "kind": "fixture.checkpoint",
                "record_id": "parent-checkpoint",
                "payload": {
                    "namespace_id": "fixture-namespace",
                    "branch_id": "parent",
                    "checkpoint_id": "parent-checkpoint",
                },
                "index_keys": ["checkpoint:parent-checkpoint"],
            }
        ]
    )
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    events: list[str] = []
    descriptor = ExtensionDescriptor(
        name="fixture",
        authority="authoritative",
        fork_strategy="authoritative",
        record_prefixes=("fixture.",),
        factory=lambda context, _dependency: _Environment(context, events),
    )
    _install(monkeypatch, descriptor)

    with pactree.open(path, extensions=["fixture"]) as parent:
        child = parent.fork(context=parent.context.current)
        try:
            assert child.scope_id != parent.scope_id
            assert child.snapshot.context.context_tree_id != parent.snapshot.context.context_tree_id
            assert child.snapshot.files is None
            assert child.snapshot.extension_views["fixture"] != parent.snapshot.extension_views[
                "fixture"
            ]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == before
        finally:
            child.close()

    assert any(event.startswith("prepare:scope-") for event in events)
    assert any(event.startswith("bind:child-") for event in events)
    assert any(event.startswith("commit:scope-") for event in events)
    assert not any(event.startswith("abort:") for event in events)


def test_synchronized_fork_resolves_the_authoritative_view_visible_at_revision(
    monkeypatch, tmp_path
):
    _state_roots(monkeypatch, tmp_path)
    path = tmp_path / "synchronized.pact"
    _pact(path)
    writer = pactree.open_extension_writer(path)
    for record_id in ("old-checkpoint", "current-checkpoint"):
        writer.append(
            [
                {
                    "schema": "fixture.checkpoint.v1",
                    "kind": "fixture.checkpoint",
                    "record_id": record_id,
                    "payload": {
                        "namespace_id": "fixture-namespace",
                        "branch_id": record_id,
                        "checkpoint_id": record_id,
                    },
                    "index_keys": [f"checkpoint:{record_id}"],
                }
            ]
        )
    events: list[str] = []
    descriptor = ExtensionDescriptor(
        name="fixture",
        authority="authoritative",
        fork_strategy="authoritative",
        record_prefixes=("fixture.",),
        factory=lambda context, _dependency: _Environment(context, events),
    )
    _install(monkeypatch, descriptor)

    with pactree.open(path, extensions=["fixture"]) as parent:
        child = parent.fork(at=parent.at(1))
        try:
            assert parent.fixture.snapshot_view()["checkpoint_id"] == "current-checkpoint"
            assert child.fixture.snapshot_view()["branch_id"].startswith("child-")
            composite = child.extension_records(
                kind="pactree.host.composite_branch",
                limit=1,
            )["rows"][0]["record"]["payload"]
            assert composite["selected_extension_views"]["fixture"][
                "checkpoint_id"
            ] == "old-checkpoint"
        finally:
            child.close()
