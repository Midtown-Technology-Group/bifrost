"""Repository tools must preserve reproducible evidence and unresolved consumers."""

import gzip
import io
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.check_dependency_metadata import check_metadata
from scripts.dependency_inventory import inventory


ROOT = Path(__file__).resolve().parents[1]


def write(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class DependencyInventoryTests(unittest.TestCase):
    def test_consumers_plugins_dynamic_imports_locks_and_cli_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root, "pyproject.toml", '''[project]
dependencies = ["PyYAML==1", "parent==1", "plugin==2"]
[project.optional-dependencies]
dev = ["pytest==3"]
[project.scripts]
example = "example:main"
''')
            write(root, "requirements.lock", '''parent==1
    # via -r pyproject.toml
child==2
    # via
    #   parent
parent-cycle==3
    # via child
child-extra==4
    # via parent-cycle
PyYAML==1
    # via parent
''')
            write(root, "api/src/example.py", '''import yaml as y
import importlib
from importlib.metadata import entry_points
y.safe_load("data")
importlib.import_module("plugin")
entry_points(group="plugins")
''')
            write(root, "api/bifrost/cli.py", "from yaml import safe_load\nsafe_load('data')\n")
            write(root, "api/tests/test_example.py", "import pytest\n")
            write(root, "scripts/tool.py", "import pathlib\n")
            write(root, "broken.py", "invalid (\n")
            (root / "binary.py").write_bytes(b"\xff")
            write(root, "requirements.txt", "# fixture\n-r ignored.txt\nPyYAML==1 # input\n===\n")
            write(root, "extras.in", "parent>=1\n")
            write(root, "Dockerfile", "RUN pip install --require-hashes -r requirements.lock\n")
            write(root, "ignored.txt", "no Python consumer\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run([
                "git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "-c", "commit.gpgsign=false", "commit", "-qm", "fixture",
            ], cwd=root, check=True)
            # A tracked missing file is retained as an uncertainty, not imported.
            (root / "ignored.txt").unlink()

            result = inventory(root)
            declarations = result["dependencies"]
            yaml = next(d for d in declarations if d["name"] == "pyyaml" and d["group"] == "dependencies")
            self.assertEqual(yaml["resolved"], {"requirements.lock": "1"})
            consumers = [result["imports"][i] for i in yaml["consumer_sites"]]
            self.assertEqual({c["scope"] for c in consumers}, {"runtime", "sdk-cli"})
            self.assertTrue(any(c.get("symbol") == "yaml.safe_load" for c in consumers))
            plugin = next(d for d in declarations if d["name"] == "plugin")
            self.assertEqual(plugin["consumer_sites"], [])
            self.assertTrue(plugin["assessment"].startswith("unknown:"))
            self.assertEqual(len(result["dynamic_loading"]), 2)
            self.assertEqual({e["path"] for e in result["parse_errors"]}, {"broken.py", "binary.py"})
            self.assertEqual(result["entrypoints"][0]["target"], "example:main")
            self.assertTrue(any(c["path"] == "Dockerfile" for c in result["commands"]))
            parent = next(d for d in declarations if d["name"] == "parent")
            self.assertEqual(parent["lock_provenance_descendants"], ["child", "child-extra", "parent-cycle", "pyyaml"])
            self.assertTrue(any(d["group"] == "dev" for d in declarations))
            self.assertTrue(any(d["group"] == "requirements-source" for d in declarations))
            self.assertTrue(any(i["scope"] == "tooling-or-other-runtime" for i in result["imports"]))

            outputs = [root / "first.gz", root / "second.gz", root / "plain.json"]
            for output in outputs:
                with patch.object(sys, "argv", ["inventory", "--root", str(root), "--output", str(output)]):
                    runpy.run_path(str(ROOT / "scripts/dependency_inventory.py"), run_name="__main__")
            self.assertEqual(outputs[0].read_bytes(), outputs[1].read_bytes())
            self.assertEqual(gzip.decompress(outputs[0].read_bytes()), outputs[2].read_bytes())
            self.assertEqual(json.loads(outputs[2].read_bytes()), result)


class DependencyMetadataTests(unittest.TestCase):
    def test_markers_extras_transitive_closure_and_unresolved_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root, "pyproject.toml", '[project]\ndependencies = ["Parent[Feature]==1", "setuptools>=78"]\n')
            metadata = {
                "parent": {"version": "1", "requires_dist": [
                    "Child[extra]>=2; extra == 'Feature'", "absent; sys_platform == 'win32'",
                ]},
                "child": {"version": "2", "requires_dist": ["Leaf==3; extra == 'extra'", "parent==1"]},
                "leaf": {"version": "3", "requires_dist": None},
            }
            path = root / "docs/architecture/dependency-evidence/locked-metadata.json.gz"
            path.parent.mkdir(parents=True)
            path.write_bytes(gzip.compress(json.dumps(metadata).encode(), mtime=0))
            result = check_metadata(root)
            self.assertEqual(result["violations"], [])
            self.assertEqual(result["unresolved"], ["setuptools"])
            self.assertEqual(result["active_packages"], 4)
            self.assertEqual(result["environment"]["python_full_version"], "3.14.0")

            metadata["child"]["version"] = "1"
            metadata["leaf"]["requires_dist"] = ["missing>=1"]
            path.write_bytes(gzip.compress(json.dumps(metadata).encode(), mtime=0))
            failures = check_metadata(root)
            self.assertEqual({v["requirement"] for v in failures["violations"]}, {
                "Child[extra]>=2; extra == 'Feature'", "missing>=1",
            })
            self.assertEqual(failures["unresolved"], ["missing", "setuptools"])

    def test_cli_reports_the_recorded_tree_without_installing_packages(self):
        output = io.StringIO()
        with patch.object(sys, "stdout", output):
            runpy.run_path(str(ROOT / "scripts/check_dependency_metadata.py"), run_name="__main__")
        result = json.loads(output.getvalue())
        self.assertEqual(result["kind"], "modeled-metadata-only")
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["unresolved"], ["setuptools"])


if __name__ == "__main__":
    unittest.main()
