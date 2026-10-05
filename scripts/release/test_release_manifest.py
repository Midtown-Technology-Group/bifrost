#!/usr/bin/env python3
"""Offline stdlib tests for release metadata and digest-only acceptance orchestration."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import accept_release_images as acceptance
import release_manifest as manifest


class ReleaseManifestTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        previous = Path.cwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(self.directory.name)
        manifest.git("init", "-q")
        manifest.git("config", "user.name", "Release fixture")
        manifest.git("config", "user.email", "release@example.invalid")
        manifest.git("config", "commit.gpgsign", "false")
        for path in manifest.CONTRACT_PATHS:
            self.write(path, "CONTRACT_VERSION: int = 11\n")
        self.write(manifest.MIGRATIONS + "baseline.py", "revision = 'baseline'\ndown_revision = None\n")
        self.commit()
        manifest.git("tag", "v2.0.0")
        for path in manifest.CONTRACT_PATHS:
            self.write(path, "CONTRACT_VERSION: int = 13\n")
        self.write(manifest.MIGRATIONS + "20261003_workflow_registration_retirement.py", f"revision = '{manifest.RETIREMENT}'\ndown_revision = 'baseline'\n")
        source = self.commit()
        self.write("release/candidate.json", json.dumps({"schema": 1, "repository": manifest.REPOSITORY, "version": "v3.0.0", "base_tag": "v2.0.0", "source_commit": source, "source_tree": manifest.git("rev-parse", f"{source}^{{tree}}")}))
        self.source = self.commit()
        manifest.git("tag", "v3.0.0")
        self.digests = {name: "sha256:" + str(index) * 64 for index, name in enumerate(manifest.COMPONENTS, 1)}

    def write(self, path, text):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self):
        manifest.git("add", ".")
        manifest.git("commit", "-qm", "fixture")
        return manifest.git("rev-parse", "HEAD")

    def build(self, **changes):
        args = dict(tag="v3.0.0", source=self.source, run_id=12, run_attempt=1, digests=self.digests)
        args.update(changes)
        return manifest.build(**args)

    def test_exact_tag_commit_not_pre_release_source_is_recorded(self):
        result = self.build()
        self.assertEqual(result["source"]["commit"], self.source)
        self.assertNotEqual(result["source"]["commit"], json.loads(Path("release/candidate.json").read_text())["source_commit"])
        self.assertEqual(result["tag"], "v3.0.0")
        self.assertEqual(result["version"], "3.0.0")
        self.assertEqual(result["compatibility"]["cli_contract"], {"current": 13, "previous": 11})
        self.assertEqual(result["compatibility"]["database"], {"migration_heads": [manifest.RETIREMENT], "upgrade_required": True})
        self.assertEqual(result["compatibility"]["rollback"]["supported"], False)
        self.assertIn("refuses once retirement evidence", result["compatibility"]["rollback"]["reason"])
        self.assertNotIn("acceptance", result)
        self.assertEqual(result, self.build())

    def test_repository_and_digests_are_immutable_identities(self):
        for name, image in self.build()["images"].items():
            self.assertEqual(image, {"repository": f"ghcr.io/midtown-technology-group/bifrost-{name}", "digest": self.digests[name]})

    def test_rejects_non_stable_or_malformed_tag(self):
        for tag in ("3.0.0", "v03.0.0", "v3.0.0-rc.1", "v3.0.0+meta", "dev", "v3.0.0\n", "--help"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                self.build(tag=tag)

    def test_rejects_bad_source_and_run_identity(self):
        for source in ("HEAD", "a" * 39, "A" * 40, "a" * 40):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.build(source=source)
        for field in ("run_id", "run_attempt"):
            for value in (0, -1, True, "12"):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.build(**{field: value})

    def test_rejects_missing_extra_or_tag_based_digest(self):
        invalid = [dict(self.digests, api=bad) for bad in ("", "3.0.0", "sha256:" + "A" * 64, "sha256:" + "a" * 63, "sha256:" + "a" * 64 + "\n")]
        invalid += [{key: val for key, val in self.digests.items() if key != "worker"}, dict(self.digests, extra="sha256:" + "a" * 64)]
        for digests in invalid:
            with self.subTest(digests=digests), self.assertRaises(ValueError):
                self.build(digests=digests)

    def test_moved_tag_fails_closed(self):
        manifest.git("tag", "-f", "v3.0.0", "v2.0.0")
        with self.assertRaisesRegex(ValueError, "differ"):
            self.build()

    def test_candidate_version_repository_and_base_are_validated(self):
        for field, value in (("version", "v9.0.0"), ("repository", "other/repo"), ("schema", 2), ("base_tag", "--help"), ("base_tag", "v3.0.0")):
            with self.subTest(field=field):
                candidate = json.loads(manifest.git("show", f"{self.source}:release/candidate.json"))
                candidate[field] = value
                self.write("release/candidate.json", json.dumps(candidate))
                sha = self.commit()
                manifest.git("tag", "-f", "v3.0.0", sha)
                with self.assertRaises(ValueError):
                    self.build(source=sha)

    def test_source_tree_and_extra_source_changes_fail_closed(self):
        candidate = json.loads(Path("release/candidate.json").read_text())
        candidate["source_tree"] = "0" * 40
        self.write("release/candidate.json", json.dumps(candidate))
        sha = self.commit()
        manifest.git("tag", "-f", "v3.0.0", sha)
        with self.assertRaisesRegex(ValueError, "source tree"):
            self.build(source=sha)
        candidate["source_tree"] = manifest.git("rev-parse", f"{candidate['source_commit']}^{{tree}}")
        self.write("release/candidate.json", json.dumps(candidate))
        self.write("unreviewed-source.py", "value = 1")
        sha = self.commit()
        manifest.git("tag", "-f", "v3.0.0", sha)
        with self.assertRaisesRegex(ValueError, "outside"):
            self.build(source=sha)

    def test_contract_literals_fail_closed(self):
        for text in ("CONTRACT_VERSION = True", "CONTRACT_VERSION = 0", "CONTRACT_VERSION = int('13')", "OTHER = 13", "CONTRACT_VERSION = 12", "CONTRACT_VERSION = 13\nCONTRACT_VERSION = 13"):
            with self.subTest(text=text):
                self.write(manifest.CONTRACT_PATHS[1], text)
                sha = self.commit()
                with self.assertRaises(ValueError):
                    manifest.contract(sha)

    def test_merge_heads_are_derived_from_graph(self):
        self.write(manifest.MIGRATIONS + "branch.py", "revision = 'branch'\ndown_revision = 'baseline'\n")
        branch = self.commit()
        self.assertEqual(manifest.heads(manifest.migration_graph(branch)), [manifest.RETIREMENT, "branch"])
        self.write(manifest.MIGRATIONS + "merge.py", f"revision = 'merge'\ndown_revision = ('{manifest.RETIREMENT}', 'branch')\n")
        merge = self.commit()
        self.assertEqual(manifest.heads(manifest.migration_graph(merge)), ["merge"])

    def test_missing_duplicate_and_cyclic_migrations_fail(self):
        for text in ("revision = 'orphan'\ndown_revision = 'missing'", "revision = 'baseline'\ndown_revision = None", "revision = 'cycle'\ndown_revision = 'cycle'", "revision = 'bad'\ndown_revision = 12"):
            with self.subTest(text=text):
                self.write(manifest.MIGRATIONS + "bad.py", text)
                sha = self.commit()
                with self.assertRaises(ValueError):
                    manifest.migration_graph(sha)

    def test_unchanged_schema_does_not_require_upgrade(self):
        result = manifest.compatibility(self.source, self.source)
        self.assertFalse(result["database"]["upgrade_required"])
        self.assertEqual(result["cli_contract"], {"current": 13, "previous": 13})

    def test_initial_release_has_no_invented_prior_contract(self):
        result = manifest.compatibility(self.source, None)
        self.assertIsNone(result["cli_contract"]["previous"])
        self.assertTrue(result["database"]["upgrade_required"])

    def test_upgrade_notes_are_regenerated_from_source(self):
        notes = "\n".join(manifest.upgrade_notes(self.source, "v2.0.0"))
        self.assertIn("11 -> 13", notes)
        self.assertIn("mismatched contracts are rejected", notes)
        self.assertIn("refuses once retirement evidence", notes)
        self.assertIn("Preserve terminal", notes)
        self.assertIn("production caller/byte evidence", notes)
        self.assertIn("uncertain outcome requires readback before any exact retry", notes)
        self.assertIn(f"/blob/{self.source}/docs/architecture/rapid-workspace-promotion.md", notes)

    def test_acceptance_uses_only_exact_digests_and_declared_architecture(self):
        document = self.build()
        def fake(*args):
            return "linux/amd64" if args[:3] == ("docker", "image", "inspect") else "ok"
        with patch.object(acceptance, "command", side_effect=fake) as commands:
            result = acceptance.accept(document)
        self.assertEqual(result["acceptance"], manifest.ACCEPTANCE)
        self.assertNotIn("acceptance", document)
        self.assertEqual(commands.call_count, 9)
        for call in commands.call_args_list:
            args = call.args
            self.assertEqual(args[0], "docker")
            self.assertNotIn("build", args)
            self.assertNotIn("push", args)
            self.assertNotIn("tag", args)
            self.assertTrue(any("@sha256:" in arg for arg in args))
            if args[1] == "run":
                self.assertIn("none", args)
                self.assertIn("--read-only", args)
                self.assertIn("no-new-privileges", args)

    def test_altered_metadata_cannot_be_accepted(self):
        for field in ("version", "compatibility", "source", "images", "acceptance"):
            document = self.build()
            if field == "acceptance":
                document[field] = manifest.ACCEPTANCE
            elif field == "version":
                document[field] = "9.0.0"
            elif field == "compatibility":
                document[field]["cli_contract"]["current"] = 12
            elif field == "source":
                document[field]["repository"] = "other/repo"
            else:
                document[field]["api"]["repository"] = "other/image"
            with self.subTest(field=field), patch.object(acceptance, "command") as commands:
                with self.assertRaises(ValueError):
                    acceptance.accept(document)
                commands.assert_not_called()

    def test_wrong_architecture_and_failed_content_do_not_attest(self):
        document = self.build()
        with patch.object(acceptance, "command", return_value="linux/arm64"):
            with self.assertRaisesRegex(ValueError, "unsupported"):
                acceptance.accept(document)
        with patch.object(acceptance, "command", side_effect=subprocess.CalledProcessError(1, ["docker"])):
            with self.assertRaises(subprocess.CalledProcessError):
                acceptance.accept(document)
        self.assertNotIn("acceptance", document)

    def test_server_probe_reads_real_cli_archive_format_and_rejects_wrong_contract(self):
        builder_path = Path(__file__).resolve().parents[2] / "api/shared/cli_artifact.py"
        spec = importlib.util.spec_from_file_location("cli_artifact_fixture", builder_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        root = Path(self.directory.name) / "image"
        root.mkdir()
        def image_file(path, text):
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        image_file("shared/__init__.py", "")
        image_file("shared/version.py", "import os\nget_version = lambda: os.environ['BIFROST_VERSION']\n")
        image_file("shared/contract_version.py", "CONTRACT_VERSION = 13\n")
        image_file("bifrost/__init__.py", "def _compute_version(): return 'source'\n__version__ = _compute_version()\n")
        image_file("bifrost/contract_version.py", "CONTRACT_VERSION = 13\n")
        image_file("bifrost/pyproject.toml", '[project]\nname = "bifrost"\nversion = "0.0.0"\n')
        image_file("_bifrost_workspace_effects.py", "")
        image_file("alembic/__init__.py", "")
        image_file("alembic/script.py", "class ScriptDirectory:\n    def __init__(self, path): pass\n    def get_heads(self): return ['20261003_workflow_retirement']\n")
        artifact = builder.build_cli_artifact(root / "bifrost", root / "artifacts", "3.0.0")
        self.assertEqual(artifact.name, "bifrost-cli-3.0.0.tar.gz")
        probe = acceptance.SERVER_PROBE.replace('"/app/', '"' + str(root) + '/')
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(root), "BIFROST_VERSION": "3.0.0", "EXPECTED_RELEASE": json.dumps({"version": "3.0.0", "contract": 13, "heads": [manifest.RETIREMENT]})}
        subprocess.run([sys.executable, "-c", probe], cwd=root, env=env, check=True, capture_output=True, text=True)
        # Build a stale CLI archive while leaving the runtime contract current.
        image_file("bifrost/contract_version.py", "CONTRACT_VERSION = 12\n")
        builder.build_cli_artifact(root / "bifrost", root / "artifacts", "3.0.0")
        image_file("bifrost/contract_version.py", "CONTRACT_VERSION = 13\n")
        result = subprocess.run([sys.executable, "-c", probe], cwd=root, env=env, check=False, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bundled CLI contract differs", result.stderr)

    def test_client_probe_checks_build_stamp_not_dependency_version(self):
        root = Path(self.directory.name) / "client"
        root.mkdir()
        (root / "index.html").write_text("<html></html>")
        (root / "assets").mkdir()
        (root / "assets/dependency.js").write_text('version = "3.0.0";')
        stamp = root / "bifrost-version.txt"
        stamp.write_text("3.0.0\n")
        probe = acceptance.CLIENT_PROBE.replace("/usr/share/nginx/html", str(root))
        env = {**os.environ, "EXPECTED_VERSION": "3.0.0"}
        subprocess.run(["sh", "-c", probe], env=env, check=True, capture_output=True)
        stamp.write_text("3.0.0-dev.945\n")
        result = subprocess.run(["sh", "-c", probe], env=env, check=False, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        stamp.unlink()
        result = subprocess.run(["sh", "-c", probe], env=env, check=False, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        dockerfile = Path(__file__).resolve().parents[2] / "client/Dockerfile"
        self.assertIn('"$VITE_BIFROST_VERSION" > dist/bifrost-version.txt', dockerfile.read_text())

    def test_publication_retry_preserves_final_digests_and_changes_attempt_identity(self):
        first = self.build()
        second = self.build(run_attempt=2)
        self.assertEqual(first["images"], second["images"])
        self.assertEqual(first["source"], second["source"])
        self.assertEqual(first["compatibility"], second["compatibility"])
        self.assertEqual(first["build"]["run_id"], second["build"]["run_id"])
        self.assertEqual(second["build"]["run_attempt"], 2)

    def test_invalid_probe_input_cannot_execute_code(self):
        compile(acceptance.SERVER_PROBE, "server_probe", "exec")
        self.assertIn('tarfile.open(artifact, "r:gz")', acceptance.SERVER_PROBE)
        self.assertNotIn("extractall", acceptance.SERVER_PROBE)
        subprocess.run(["bash", "-n"], input=acceptance.CLIENT_PROBE, text=True, check=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
