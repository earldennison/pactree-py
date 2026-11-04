"""Boundary audit guard for the pactree-py binding.

This is an intentionally dumb regression catch: it scans `src/rust_lib.rs` for
`PyValueError` raises that look like PACT-*semantic* failures (a missing branch,
bad input, a mismatch) which should instead raise a pact-specific exception
(`PactNotFound` / `PactInvalidInput` / `pact_err_to_py`). Generic `PyValueError`
is only allowed for true binding plumbing — encoding an already-valid output
object, decoding/parsing the engine's own output, or raw `.pact` filesystem IO.

If this test fails, you probably added a `PyValueError::new_err(...)` for a PACT
semantic error. Route it through `py_not_found` / `py_invalid_input` /
`pact_err_to_py` instead, or — if it is genuinely plumbing — add its prefix to
`ALLOWED_PLUMBING_PREFIXES` with a note.
"""

from __future__ import annotations

import pathlib
import re

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"

RUST_SOURCES = [_SRC / "rust_lib.rs", *sorted((_SRC / "bindings").glob("*.rs"))]


def _all_rust_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in RUST_SOURCES)




ALLOWED_PLUMBING_PREFIXES = (
    "failed to encode",
    "failed to serialize",
    "failed to decode",
    "failed to parse materialized tree",
    "failed to read .pact file",
    "failed to write .pact file",
)


def _pyvalueerror_string_messages(text: str) -> list[str]:


    pattern = re.compile(r'PyValueError::new_err\(\s*(?:format!\(\s*)?"((?:[^"\\]|\\.)*)"')
    return [m.group(1) for m in pattern.finditer(text)]


def test_no_semantic_pyvalueerror_string_leaks():
    text = _all_rust_text()
    leaks = [
        msg
        for msg in _pyvalueerror_string_messages(text)
        if not any(msg.startswith(prefix) for prefix in ALLOWED_PLUMBING_PREFIXES)
    ]
    assert not leaks, (
        "semantic PyValueError leak(s) found in rust_lib.rs — route through "
        "py_not_found / py_invalid_input / pact_err_to_py instead:\n  "
        + "\n  ".join(leaks)
    )


def test_no_stringified_typed_error_in_pyvalueerror():



    text = _all_rust_text()
    assert "PyValueError::new_err(err.to_string())" not in text, (
        "found PyValueError::new_err(err.to_string()) — a typed error is being "
        "stringified at the boundary; route it through pact_err_to_py instead."
    )


def test_no_branch_not_found_via_pyvalueerror():


    text = _all_rust_text()
    pattern = re.compile(r'PyValueError::new_err\([^)]*branch not found')
    assert not pattern.search(text), (
        "found 'branch not found' raised as PyValueError — use py_not_found."
    )
