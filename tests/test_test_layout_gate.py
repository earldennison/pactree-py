from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_production_sources_do_not_embed_tests():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_test_layout.py")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
