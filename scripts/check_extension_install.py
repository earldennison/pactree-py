#!/usr/bin/env python3
"""Prove a pure-Python extension installs without rebuilding Pactree."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "pactree_fixture_extension"


def fingerprint(path: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    stat = path.stat()
    return digest, stat.st_mtime_ns, stat.st_size


def main() -> int:
    import pactree
    from pactree import pactree_engine

    native_path = Path(pactree_engine.__file__).resolve()
    before = fingerprint(native_path)

    with tempfile.TemporaryDirectory(prefix="pactree-extension-gate-") as root:
        root_path = Path(root)
        fixture_source = root_path / "fixture"
        shutil.copytree(FIXTURE, fixture_source)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-deps",
                str(fixture_source),
            ],
            check=True,
        )
        importlib.invalidate_caches()

        installed = {
            item.name
            for item in importlib.metadata.entry_points().select(
                group="pactree.extensions"
            )
        }
        if "fixture" not in installed:
            raise AssertionError("fixture entry point was not discovered")

        path = root_path / "session.pact"
        pactree.ContextTree.with_id("extension-install-gate").save(path)
        with pactree.open(path, extensions=["fixture"]) as pact:
            if not pact.fixture.active:
                raise AssertionError("installed fixture did not activate")
            if pact.fixture.context.read_handle is not pact.read_handle:
                raise AssertionError("fixture did not receive the pinned PACT handle")

    after = fingerprint(native_path)
    if after != before:
        raise AssertionError(
            "installing a pure-Python extension changed the Pactree native module"
        )
    print(
        "extension install: PASS "
        f"native_sha256={before[0]} native_mtime_ns={before[1]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
