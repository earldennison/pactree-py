# Bounded append writer

`pact.append.v1` appends message batches to a PACTBIN2 file through the Rust
engine. Each committed batch has a durable receipt stored in the file.

```python
import pactree

writer = pactree.open_append_writer("session.pact", branch_id="main")
assert writer.capability == "pact.append.v1"
batch = {
    "source": "example",
    "batch_id": "batch-1",
    "expected": writer.version(),
    "previous_checkpoint": None,
    "checkpoint": "checkpoint-1",
    "messages": [{
        "row_id": "message-1",
        "role": "user",
        "parts": [{"componentType": "text_content", "content": "Hello"}],
        "aux": {},
    }],
}
outcome = writer.append_batch(batch)
receipt = outcome["receipt"]
assert writer.lookup("example", "batch-1") == receipt
writer.close()
```

`outcome["status"]` is `committed` or `already_committed`. Repeating the same
batch returns its original receipt. Reusing a batch ID with different content
is an idempotency conflict. Row IDs must be unique within a source across
batches. Empty batches are invalid; a batch cannot exceed 16,384 messages.
System messages go under the system header; other messages become root turns
in batch order.

The receipt contains `version`, `transaction_id`, `source`, `batch_id`,
`request_digest`, `checkpoint`, and `row_ids`. The version contains
`file_identity`, `generation`, `branch_id`, and `revision`.

`writer.checkpoint(source)` returns the last committed checkpoint, which may
be `None`. `writer.latest_receipt(source)` distinguishes no prior batch from a
committed batch with a null checkpoint. `close()` is idempotent. File reads and
writes through a closed writer raise `PactInvalidInput`; its `closed` and
`capability` properties remain readable.

## Failure handling

`PactAppendError.category` identifies the failure independently of its message:

| Category | Meaning |
| --- | --- |
| `invalid_input` | Malformed batch or duplicate row identity |
| `unsupported_semantics` | A part uses semantics the bounded writer does not support |
| `idempotency_conflict` | Existing batch ID with a different request digest |
| `conflict` | Stale expected version or previous checkpoint |
| `corrupt` | File integrity failure |
| `format_limit` | Structural format limit exceeded |
| `resource_unavailable` | Required resource or writer ownership unavailable |
| `rejected_io` | Write rejected before root publication |
| `outcome_unknown` | Root publication outcome could not be determined |

For `conflict`, `error.actual` carries the current version. For
`outcome_unknown`, close and reopen the writer, then inspect
`lookup(source, batch_id)` before deciding whether to retry. Do not infer
commit success or failure from a transport error alone. Applications own
their retry and queue policies.

## Options

When supplied, `memory_options` requires all five integer fields: `page_cache_bytes`,
`component_cache_bytes`, `scratch_cache_bytes`, `io_chunk_bytes`, and
`in_flight_bytes`. Booleans are rejected; `io_chunk_bytes` must be positive.
Omit this argument for native defaults.

`derived_depth=True` opts a branch into deriving depth from turn order instead
of storing a depth update for every existing component on each append. This
setting is sticky in the file. Enable it only when all readers support derived
depth; older readers may display incorrect depths for marked files.
