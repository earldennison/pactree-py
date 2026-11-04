#!/usr/bin/env python3
"""Fail when executable tests are embedded in production source files."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
INLINE_RUST_TEST = re.compile(r"\bmod\s+tests\s*\{")
RUST_TEST_ATTRIBUTE = re.compile(r"#\s*\[\s*(?:tokio::)?test(?:\([^]]*\))?\s*\]")


def python_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: test function")
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: test class")
    return violations


def rust_violations(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    violations: list[str] = []
    for matcher, label in (
        (INLINE_RUST_TEST, "inline test module"),
        (RUST_TEST_ATTRIBUTE, "test attribute"),
    ):
        for match in matcher.finditer(source):
            line = source.count("\n", 0, match.start()) + 1
            violations.append(f"{path.relative_to(ROOT)}:{line}: {label}")
    return violations


def main() -> int:
    violations: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        if "tests" not in path.parts:
            violations.extend(python_violations(path))
    for path in sorted(SOURCE.rglob("*.rs")):
        if "tests" not in path.parts and path.name != "tests.rs":
            violations.extend(rust_violations(path))
    if violations:
        print("\n".join(violations), file=sys.stderr)
        return 1
    print("test layout: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
