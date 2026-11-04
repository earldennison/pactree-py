# pactree-py API Contract

`pactree-py` is a **Python compatibility / marshalling layer** over the Rust
`pactree` engine. It is **not** a second PACT implementation.

The invariant:

```
in:   Python host object -> JSON payload -> Rust validates/normalizes/mutates/projects
out:  Rust typed projection -> native Python pyclass   (NO JSON, NO from_dict)
```

Reads/projections cross the FFI as **Rust-owned pyclasses** (defined in
`pactree._engine`) and are obtained only as the return values of
`ContextTree.view()` / `ContextTree.project()`: `PactView`,
`PactFileProjection`, and the per-kind component views (`ComponentView`,
`TextContentView`, `ToolCallView`, …). There is no public
`pactree.data_models` namespace to import them from. They are returned directly — there
is no JSON string and no Python `from_dict` hydration on the read path. The ref
policy is **structural**: blob/content refs (`content_ref`/`parameters_ref`/
`result_ref`) and ledger internals (branch/head/revision, stats, tool-pairs) have
no getter and never cross the FFI; only node ids, *rendered* content, and the
external `source_ref` are exposed. `to_dict()`/`to_json()` are the explicit
serialization edge. (Operation *inputs* still marshal in as JSON; native-object
inputs are a later phase.)

Never:

```
Python guesses node kind / depth / children / selector / result metadata -> Rust receives half-canonical state
```

## Ownership

**Rust `pactree` owns** (the single source of truth):
- canonical node schema and node-kind normalization
- root/system/active bootstrap shape
- selector parsing and coordinate resolution (`parent_id`, `offset`, `pact_depth`)
- insert / append / replace / update / delete / upsert / prune semantics
- staged traces, working traces, preview, seal, head, branches
- produced/replaced/deleted/affected metadata
- provider-thread projection; Preflight / PostResponse boundaries
- tool-call/tool-result pairing; content refs and external refs

**pactree-py owns** (only):
- PyO3 loading; JSON **marshal-IN** for operations; native **pyclass marshal-OUT**
  for reads (no JSON on the read path)
- the public `ContextTree` facade and its ergonomic input lanes (string / dict /
  component); engine calls are made through a **private** bridge — there is no
  public resolver class
- the host-ergonomic **authoring** DTOs: `pactree.components`
  (`Component`, `TextContent`, `ReasoningContent`, `ToolCall`, `ToolResult`, …)
- typed Python exceptions re-exported from the engine
- path-level `.pact` **inspection** helpers (`info`, `validate`, `inspect`,
  bundle/import utilities) — ordinary tree reads/forks/saves go through
  `ContextTree`, not raw file/store/materialize calls

Legitimate adapter logic is **marshal-in** (`component.model_dump()` → node dict)
and **marshal-out** for `select` (component wrappers). Projection/view reads do
NOT marshal — the engine returns the pyclass directly. Internal pipeline reports
(`SubmitOutcome`/`ResolveOutcome`) and lifecycle indexes (`ToolCallRecord`/
`tool_pairs`) are **not** exposed: an interfacing language carries only the typed
results of user-callable verbs, never engine plumbing.

**Egregore owns:** agents, sessions, providers, tools, scaffolds, retention,
notifications, tasks, and the decision of *which host event becomes which PACT
operation*. Host-ergonomic sugar (e.g. `add_user`) belongs in egregore's
`ContextController`, not in this binding.

## Forbidden in the pactree-py public facade
- recursive tree walking to derive canonical facts
- selector parsing
- node taxonomy maps / a second node model (`canonical_nodes` is deleted)
- a Python node cache shadowing resolver state (`_node_cache` is deleted —
  `select()` always rebuilds from the DTO)
- message-part classification in Python (`_is_tool_call`/`_is_tool_result`/…)
- root/system/active construction in Python
- public raw mutation/trace authoring for normal users

If Python needs a semantic fact, add a Rust API that returns it.

## Canonical API surface

Context operations (Rust-owned placement). Placement is one `Placement(...)`
object; targets are plain component ids. Mutations return a `TraceRef`
(`trace_id`, `op_kind`, produced/replaced/deleted component ids):
```python
from pactree import Placement
from pactree.components import TextContent

ctx.insert(TextContent("system"), Placement(-1, 0))   # depth/offset lane
ctx.append(TextContent("hello"), Placement("d0"))      # depth-string lane
ctx.append(TextContent("child"), Placement(parent_id)) # under a component id
ctx.update(component_id, content="new")                # target is a component id
ctx.replace(TextContent("replacement"), component_id)
ctx.upsert(TextContent("maybe"), component_id=None, placement=Placement("system", 0))
ctx.prune(component_id)                                 # delete by component id
ctx.select(".tool_call")                               # selectors are read-only
```

No public mutation accepts `target_selector` or returns a `*_report`; selectors
are for `select(...)` only (plus selector-shaped placement inside `Placement`).

Lifecycle boundaries:
```python
ctx.send()        # seals Preflight
ctx.receive(...)  # ingests provider response, seals PostResponse
ctx.project("provider_thread")  # canonical read-only inspection, never seals
ctx.provider_thread(...)        # same read, with partial-tool/orphan knobs
ctx.preview() / ctx.has_staged() / ctx.discard_staged()
```

Construction & loading:
```python
ContextTree()                       # fresh context
ContextTree.builder().user("hi").build()   # fluent authoring sugar
tree.save(path)                     # persist to a .pact file
ContextTree.from_file(path)         # load from a .pact file
```
There is no `from_json`: JSON (`view`/`project`) is a one-way projection in this
engine, not an ingest format. The round-trippable persistence format is the
`.pact` file (`save` / `from_file`). A faithful JSON restore would require a
native store-deserialize-with-ledger-replay API that does not exist. Use `.pact`
files when round-trip persistence is required.

Selectors (Rust-parsed):
`.type` (node type, e.g. `.tool_call`) · `+tag` · `(dN)` depth scope ·
`#node_id` · `{k=v}` attribute · combinations like `(d0) .tool_call`.
Note: a `ToolCall` node's *type* is `tool_call` — select it with `.tool_call`,
not `+tool_call` (which is a tag match).

## Bounded append API

See [append writer](append-writer.md) for transactional batch writes and durable
receipts without loading a complete context tree.
