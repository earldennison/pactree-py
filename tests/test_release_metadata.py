"""Release metadata stays aligned without importing the native engine."""

from pathlib import Path
import runpy
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReleaseMetadataTests(unittest.TestCase):
    def test_release_versions_are_aligned(self) -> None:
        def read_toml(name: str) -> dict:
            return tomllib.loads((ROOT / name).read_text(encoding="utf-8"))

        cargo_lock = read_toml("Cargo.lock")
        uv_lock = read_toml("uv.lock")
        versions = {
            "pyproject.toml": read_toml("pyproject.toml")["project"]["version"],
            "Cargo.toml": read_toml("Cargo.toml")["package"]["version"],
            "Cargo.lock": next(
                package["version"]
                for package in cargo_lock["package"]
                if package["name"] == "pactree-py"
            ),
            "uv.lock": next(
                package["version"]
                for package in uv_lock["package"]
                if package["name"] == "pactree"
                and package["source"] == {"editable": "."}
            ),
            "_version.py": runpy.run_path(ROOT / "src/pactree/_version.py")[
                "__version__"
            ],
        }

        self.assertEqual(versions, dict.fromkeys(versions, "0.1.0"))
