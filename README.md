# Pactree

Pactree is a deterministic context-tree substrate for AI agents and
applications. It is designed to make context explicit: ordered, queryable,
branchable, compact on disk, and stable across Python and Rust runtimes.

> **`pactree-py` is a compatibility / marshalling layer over the Rust engine —
> not a second PACT implementation.** The pact object (Rust resolver) is the
> single source of truth. See [`docs/api-contract.md`](docs/api-contract.md) for
> the ownership split, canonical API surface, selector vocabulary, and the
> patterns forbidden in the Python facade.

The project centers on the `.pact` file format and a Rust engine for
context trees, operations, selectors, projections, traces, and rewind/fork
workflows. The Python package provides the ergonomic host API that applications
use to build, inspect, and persist those trees.

## Quick start

You learn one object: `ContextTree`.

```python
from pactree import ContextTree
from pactree.components import TextContent, ToolCall, ToolResult

tree = ContextTree()

tree.user("hello")
tree.user({"componentType": "text_content", "content": "hello"})
tree.user(TextContent("hello", ttl=3))

provider_thread = tree.send()
tree.receive("the answer")

tree.view()
tree.project("json")

tree.save("session.pact")
reopened = ContextTree.from_file("session.pact")
child = tree.fork("child.pact", mode="hard")
```

Components (`TextContent`, `ReasoningContent`, `ToolCall`, `ToolResult`,
`XMLComponent`) are advanced authoring objects — strings and dicts work
everywhere a component does. Everything below `ContextTree` (the
resolver, branch ids, root store, materialization) is Rust engine machinery and
is not part of the public Python surface.

Core goals:

- deterministic tree layout for agent context
- compact content-addressed storage
- branch, rewind, and what-if workflows
- provider-facing projections without losing substrate structure
- a clean Python API backed by a canonical Rust implementation

## Package status

Version `0.1.0` includes native Python bindings to the Rust-owned PACTBIN2
substrate. Rust owns `.pact` persistence, indexed reads, append transactions,
and tree operations; Python provides the host API and marshals data across
that boundary.

Source builds require Python 3.11 or newer, a Rust toolchain, and the matching
Rust `pactree` checkout beside this repository at `../pactree`, as declared in
`Cargo.toml`. Maturin builds the native `pactree.pactree_engine` extension with
the Python package.

## Build from source

Install Python 3.11 or newer and Rust 1.93 or newer, then clone both repositories
beside each other:

```sh
git clone https://github.com/earldennison/pactree.git
git clone https://github.com/earldennison/pactree-py.git
cd pactree-py
python3 -m venv .venv
. .venv/bin/activate
python -m pip install 'maturin>=1.8,<2' 'pytest>=8,<9'
maturin develop --release --locked
python -m pytest tests
```

The paired `main` branches provide PACTBIN2 version 0.1.0. Rust dependencies
are pinned by `Cargo.lock`. A source checkout of this Python repository alone
does not include its sibling Rust dependency. To build a distributable wheel,
use `maturin build --release --locked`; the wheel is specific to the Python
interpreter and target platform used for the build.

See [append writer](docs/append-writer.md) for transactional message batches.

## License

MIT; see [LICENSE](LICENSE). Bundled legacy lexical data retains separate
attribution terms described in [third-party notices](THIRD_PARTY_NOTICES.md).
