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


class ManifestFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        previous = Path.cwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(self.directory.name)
        manifest.git("init", "-q")
        # Commits must not leave detached Git maintenance writing into a
        # temporary fixture while unittest removes it.
        manifest.git("config", "gc.auto", "0")
        manifest.git("config", "maintenance.auto", "false")
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


class ReleaseManifestTests(ManifestFixture):
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


class ReleasePublicationTests(unittest.TestCase):
    """Exercise the workflow's real shell against a draft-aware GitHub fixture."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.tag = "v3.0.0"
        self.source_assets = [
            f"bifrost-{self.tag}-source.tar.gz" + suffix
            for suffix in ("", ".sha256", ".sigstore")
        ]
        self.manifest_assets = [
            "release-manifest.json" + suffix
            for suffix in ("", ".sha256", ".sigstore.json")
        ]
        self.release = {
            "id": 42,
            "tag_name": self.tag,
            "draft": True,
            "immutable": False,
            "assets": [
                {"name": name, "state": "uploaded", "size": 1}
                for name in self.source_assets + self.manifest_assets
            ],
        }
        workflow = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"
        publication = workflow.read_text().split("      - name: Publish release\n", 1)[1]
        block = publication.split("        run: |\n", 1)[1]
        self.script = "\n".join(
            line[10:] for line in block.splitlines() if line.startswith("          ")
        )
        fake_gh = self.root / "gh"
        fake_gh.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ['FIXTURE_ROOT'])
args = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as f:
    f.write(json.dumps(args) + '\\n')
path = root / 'release.json'
release = json.loads(path.read_text())
endpoint = 'repos/Midtown-Technology-Group/bifrost/releases/42'
if args[:3] == ['api', '--method', 'PATCH'] and args[3] == endpoint:
    assert '-F' in args and 'draft=false' in args
    release['draft'] = False
    release['immutable'] = os.environ.get('FIXTURE_IMMUTABLE', 'true') == 'true'
    path.write_text(json.dumps(release))
elif args[:2] == ['api', endpoint]:
    pass
else:
    # GitHub's tag endpoint exposes published releases only. Never let this
    # fixture mask the original 404 by treating a draft as a published tag.
    print('gh: Not Found (HTTP 404)', file=sys.stderr)
    sys.exit(1)
if '--jq' in args:
    print(str(not release['draft'] and release['immutable']).lower())
else:
    print(json.dumps(release))
""")
        fake_gh.chmod(0o755)

    def publish(self, *, release_id="42", prerelease="false", immutable="true"):
        (self.root / "release.json").write_text(json.dumps(self.release))
        calls = self.root / "calls.jsonl"
        calls.unlink(missing_ok=True)
        result = subprocess.run(
            ["bash", "-e", "-c", self.script],
            env={**os.environ, "PATH": str(self.root) + os.pathsep + os.environ["PATH"],
                 "FIXTURE_ROOT": str(self.root), "FIXTURE_IMMUTABLE": immutable,
                 "GITHUB_REPOSITORY": manifest.REPOSITORY, "VERSION": self.tag,
                 "RELEASE_ID": release_id, "PRERELEASE": prerelease},
            text=True, capture_output=True, check=False,
        )
        commands = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
        return result, commands

    def assert_not_published(self, **kwargs):
        result, calls = self.publish(**kwargs)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("PATCH" in call for call in calls), calls)

    def test_draft_is_published_by_returned_id_and_immutable_readback(self):
        result, calls = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        endpoint = f"repos/{manifest.REPOSITORY}/releases/42"
        self.assertEqual(calls, [
            ["api", endpoint],
            ["api", "--method", "PATCH", endpoint, "-F", "draft=false", "-f", "make_latest=true"],
            ["api", endpoint, "--jq", ".draft == false and .immutable == true"],
        ])

    def test_wrong_draft_identity_or_invalid_id_fails_before_publish(self):
        for field, value in (("id", 43), ("tag_name", "v9.0.0"), ("draft", False)):
            with self.subTest(field=field):
                previous = self.release[field]
                self.release[field] = value
                self.assert_not_published()
                self.release[field] = previous
        for release_id in ("", "0", "-1", "42/path", "abc"):
            with self.subTest(release_id=release_id):
                self.assert_not_published(release_id=release_id)

    def test_every_required_asset_must_be_uploaded_and_nonempty(self):
        for index in range(len(self.release["assets"])):
            original = self.release["assets"][index]
            for change in ({"name": "wrong"}, {"size": 0}, {"state": "starter"}):
                with self.subTest(asset=original["name"], change=change):
                    self.release["assets"][index] = {**original, **change}
                    self.assert_not_published()
            self.release["assets"][index] = original

    def test_nonimmutable_publication_readback_fails(self):
        result, calls = self.publish(immutable="false")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sum("PATCH" in call for call in calls), 1)

    def test_prerelease_keeps_source_gate_without_marking_latest(self):
        self.release["assets"] = self.release["assets"][:3]
        result, calls = self.publish(prerelease="true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("prerelease=true", calls[1])
        self.assertNotIn("make_latest=true", calls[1])


class UnpublishedCandidateRecoveryTests(unittest.TestCase):
    """Recovery must not replace tagged releases or transfer stale CI evidence."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "automatic_release_recovery", Path(__file__).with_name("automatic-release.py")
        )
        self.release = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.release)
        self.source, self.previous = "a" * 40, "b" * 40
        self.candidate = {"version": "v3.1.0"}
        self.prs = [{"number": 1078, "merged_at": "2026-10-10T00:00:00Z",
                     "head": {"ref": self.release.BRANCH}, "base": {"ref": "main"},
                     "merge_commit_sha": self.previous}]
        self.good = {
            "head_sha": self.source, "head_branch": "main", "event": "push",
            "conclusion": "success", "status": "completed",
            "path": ".github/workflows/ci.yml",
            "head_repository": {"full_name": self.release.REPOSITORY},
        }
        self.failed = dict(self.good, id=90, head_sha=self.previous, conclusion="failure")
        self.refs, self.releases, self.runs = [], [], [self.failed]
        patches = [
            patch.object(self.release, "api", side_effect=lambda _: self.good),
            patch.object(self.release, "git", side_effect=self.git),
            patch.object(self.release, "blob", return_value=self.candidate),
            patch.object(self.release, "array_pages", side_effect=self.pages),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def git(self, *args):
        if args[0] == "rev-list":
            return self.source + "\n" + self.previous
        if args == ("rev-parse", "HEAD"):
            return self.source
        return ""

    def pages(self, path, *args):
        if path.startswith("git/matching-refs/"):
            return self.refs
        if path.startswith("releases?"):
            return self.releases
        return self.runs

    def recover(self):
        return self.release.supersede_unpublished(self.source, self.prs, 1078, 100)

    def publication_prs(self):
        old = dict(self.prs[0], head={"ref": self.release.BRANCH,
                                   "repo": {"full_name": self.release.REPOSITORY}})
        new = dict(old, number=1200, merge_commit_sha=self.source)
        return [old, new]

    def test_old_successful_ci_rerun_cannot_create_tag_or_dispatch_packaging(self):
        with patch.object(self.release, "checked_release", return_value=self.candidate), \
             patch.object(self.release, "merged_prs", return_value=self.publication_prs()), \
             patch.object(self.release, "api") as api:
            with self.assertRaisesRegex(self.release.ReleaseError, "superseded"):
                self.release.publish(self.previous, 101, self.prs)
            api.assert_not_called()

    def test_current_candidate_rerun_survives_ordinary_main_merges(self):
        # The source mock's first-parent history includes an ordinary newer main
        # merge, but the latest release PR is still the original candidate.
        with patch.object(self.release, "merged_prs", return_value=self.publication_prs()[:1]):
            self.release.reject_superseded_source(self.previous)

    def test_fresh_main_read_failure_cannot_create_tag_or_dispatch(self):
        with patch.object(self.release, "checked_release", return_value=self.candidate), \
             patch.object(self.release, "git", side_effect=self.release.ReleaseError("fetch failed")), \
             patch.object(self.release, "api") as api:
            with self.assertRaisesRegex(self.release.ReleaseError, "fetch failed"):
                self.release.publish(self.previous, 101, self.prs)
            api.assert_not_called()

    def test_replacement_candidate_can_publish_after_supersession(self):
        with patch.object(self.release, "merged_prs", return_value=self.publication_prs()):
            self.release.reject_superseded_source(self.source)

    def test_missing_release_inventory_and_changed_main_candidate_fail_closed(self):
        with patch.object(self.release, "merged_prs", return_value=[]), \
             self.assertRaisesRegex(self.release.ReleaseError, "cannot establish"):
            self.release.reject_superseded_source(self.previous)
        with patch.object(self.release, "merged_prs", return_value=self.publication_prs()), \
             patch.object(self.release, "blob", side_effect=[{"version": "v3.2.0"}, self.candidate]), \
             self.assertRaisesRegex(self.release.ReleaseError, "differs"):
            self.release.reject_superseded_source(self.source)

    def same_tree_fixture(self):
        pr = {"merged": True, "merge_commit_sha": self.source,
              "base": {"ref": "main", "sha": "c" * 40},
              "head": {"sha": self.previous, "ref": "reviewed-fix",
                       "repo": {"full_name": self.release.REPOSITORY}}}
        run = dict(self.good, head_sha=self.previous, head_branch="reviewed-fix",
                   event="pull_request", run_attempt=1,
                   created_at="2026-10-20T10:00:00Z", updated_at="2026-10-20T11:00:00Z")
        names = ("Plan Affected Tests", "Lint & Type Check", "Unit Tests", "Client Unit Tests",
                 "E2E Tests", "MCP Conformance", "Candidate Images",
                 "sonar-coverage / Sonar Coverage Evidence", "sonar-coverage / Sonar CI Analysis")
        jobs = [{"name": name, "conclusion": "success"} for name in names]
        sonar = {"id": 77, "name": "SonarCloud Code Analysis", "app": {"id": 12526},
                 "head_sha": self.previous, "status": "completed", "conclusion": "success",
                 "details_url": "https://sonarcloud.io/dashboard?id=Midtown-Technology-Group_bifrost&pullRequest=1363",
                 "started_at": "2026-10-20T10:30:00Z", "completed_at": "2026-10-20T10:31:00Z"}
        return pr, run, jobs, sonar

    def certify_same_tree(self, pr, run, jobs, sonar):
        def read(path):
            return pr if path == "pulls/1363" else run

        def pages(path, key):
            return jobs if key == "jobs" else [sonar]

        with patch.object(self.release, "api", side_effect=read), \
             patch.object(self.release, "array_pages", side_effect=pages), \
             patch.object(self.release, "git", return_value="d" * 40):
            return self.release.checked_same_tree_pr_ci(self.source, 100, 1363)

    def test_same_tree_evidence_retains_exact_pr_run_attempt_and_sonar(self):
        proof = self.certify_same_tree(*self.same_tree_fixture())
        self.assertEqual(proof, {"pr": 1363, "head": self.previous, "tree": "d" * 40,
                                 "run": 100, "attempt": 1, "sonar_check": 77})

    def test_same_tree_rejects_wrong_pr_merge_repo_or_base(self):
        invalid = {"merged": False, "merge_commit_sha": self.previous,
                   "base": {"ref": "other"},
                   "head": {"sha": self.previous, "repo": {"full_name": "gobifrost/bifrost"}}}
        for key, value in invalid.items():
            pr, run, jobs, sonar = self.same_tree_fixture()
            pr[key] = value
            with self.subTest(field=key), self.assertRaises(self.release.ReleaseError):
                self.certify_same_tree(pr, run, jobs, sonar)

    def test_same_tree_rejects_incomplete_untrusted_or_other_ci(self):
        invalid = {"head_sha": self.source, "event": "workflow_dispatch",
                   "head_branch": "other", "status": "in_progress", "conclusion": "failure",
                   "path": ".github/workflows/other.yml", "run_attempt": None,
                   "head_repository": {"full_name": "gobifrost/bifrost"}}
        for key, value in invalid.items():
            pr, run, jobs, sonar = self.same_tree_fixture()
            run[key] = value
            with self.subTest(field=key), self.assertRaises(self.release.ReleaseError):
                self.certify_same_tree(pr, run, jobs, sonar)

    def test_same_tree_refuses_missing_skipped_failed_or_duplicate_jobs(self):
        for mode in ("missing", "skipped", "failure", "duplicate"):
            pr, run, jobs, sonar = self.same_tree_fixture()
            if mode == "missing":
                jobs.pop()
            elif mode == "duplicate":
                jobs.append(dict(jobs[0]))
            else:
                jobs[0]["conclusion"] = mode
            with self.subTest(mode=mode), self.assertRaises(self.release.ReleaseError):
                self.certify_same_tree(pr, run, jobs, sonar)

    def test_same_tree_rejects_stale_untrusted_pending_or_wrong_project_sonar(self):
        invalid = {"head_sha": self.source, "app": {"id": 1}, "status": "in_progress",
                   "conclusion": "failure", "details_url": "https://sonarcloud.io/dashboard?id=other",
                   "started_at": "2026-10-19T10:00:00Z", "completed_at": None}
        for key, value in invalid.items():
            pr, run, jobs, sonar = self.same_tree_fixture()
            sonar[key] = value
            with self.subTest(field=key), self.assertRaises(self.release.ReleaseError):
                self.certify_same_tree(pr, run, jobs, sonar)

    def test_changed_tree_cannot_reuse_pr_evidence(self):
        pr, run, _, _ = self.same_tree_fixture()
        with patch.object(self.release, "api", side_effect=[pr, run]), \
             patch.object(self.release, "git", side_effect=["", "", "d" * 40, "e" * 40]), \
             self.assertRaisesRegex(self.release.ReleaseError, "tree differs"):
            self.release.checked_same_tree_pr_ci(self.source, 100, 1363)

    def test_same_tree_mode_requires_explicit_supersession(self):
        with self.assertRaisesRegex(self.release.ReleaseError, "explicit supersession"):
            self.release.prepare(self.source, self.prs, same_tree_pr=1363)

    def test_publication_still_requires_exact_main_push_even_with_green_pr_ci(self):
        with patch.object(self.release, "api", return_value=dict(self.good, event="pull_request")), \
             self.assertRaisesRegex(self.release.ReleaseError, "exact trusted main push"):
            self.release.checked_release(self.source, 100, self.prs)

    def test_explicit_recovery_retains_same_tree_receipt_without_using_main_ci(self):
        receipt = {"pr": 1363, "head": self.previous, "tree": "d" * 40,
                   "run": 100, "attempt": 1, "sonar_check": 77}
        with patch.object(self.release, "checked_same_tree_pr_ci", return_value=receipt), \
             patch.object(self.release, "checked_main_ci") as main_ci:
            proof = self.release.supersede_unpublished(
                self.source, self.prs, 1078, 100, same_tree_pr=1363
            )
        main_ci.assert_not_called()
        self.assertEqual(proof["same_tree_ci"], receipt)
        self.assertIn('"sonar_check": 77', "\n".join(self.release.same_tree_notes(proof)))
        self.assertEqual(self.release.preparation_ci_note(proof), "Replacement same-tree PR CI: 100.")

    def test_retains_old_failure_and_new_exact_ci_receipts(self):
        proof = self.recover()
        self.assertEqual(proof, {"pr": 1078, "source": self.previous,
                                "version": "v3.1.0", "failed_ci": 90,
                                "replacement_ci": 100})

    def test_automatic_preparation_still_preserves_pending_candidate(self):
        with patch.object(self.release, "pending_release", return_value=True), \
             patch.object(self.release, "calculate") as calculate:
            self.assertIsNone(self.release.prepare(self.source, self.prs))
            calculate.assert_not_called()

    def test_supersession_inputs_must_be_paired_before_preparation(self):
        for inputs in ({"supersede_pr": 1078}, {"source_ci_run": 100}):
            with self.subTest(inputs=inputs), self.assertRaises(self.release.ReleaseError):
                self.release.prepare(self.source, self.prs, **inputs)

    def test_malformed_retained_candidate_refuses_before_publication_lookup(self):
        for candidate in (None, [], "v3.1.0", {}, {"version": 31}, {"version": "dev.819"}):
            with self.subTest(candidate=candidate), \
                 patch.object(self.release, "blob", return_value=candidate), \
                 self.assertRaisesRegex(self.release.ReleaseError, "invalid retained"):
                self.recover()

    def test_existing_exact_tag_refuses_supersession(self):
        self.refs = [{"ref": "refs/tags/v3.1.0"}]
        with self.assertRaisesRegex(self.release.ReleaseError, "tagged"):
            self.recover()

    def test_existing_release_including_draft_refuses_supersession(self):
        for draft in (False, True):
            self.releases = [{"tag_name": "v3.1.0", "draft": draft}]
            with self.subTest(draft=draft), self.assertRaises(self.release.ReleaseError):
                self.recover()

    def test_wrong_release_pr_cannot_replace_latest_candidate(self):
        self.prs[0]["number"] = 1079
        with self.assertRaisesRegex(self.release.ReleaseError, "latest"):
            self.recover()

    def test_changed_retained_candidate_is_not_silently_replaced(self):
        with patch.object(self.release, "blob", side_effect=[self.candidate, {"version": "v3.0.1"}]), \
             self.assertRaisesRegex(self.release.ReleaseError, "differs"):
            self.recover()

    def test_inflight_cancelled_or_successful_original_ci_requires_existing_recovery(self):
        for status, conclusion in (("in_progress", None), ("completed", "cancelled"),
                                   ("completed", "success")):
            self.failed.update(status=status, conclusion=conclusion)
            with self.subTest(status=status, conclusion=conclusion), \
                 self.assertRaises(self.release.ReleaseError):
                self.recover()

    def test_latest_original_run_is_authoritative(self):
        self.runs.append(dict(self.failed, id=91, status="in_progress", conclusion=None))
        with self.assertRaises(self.release.ReleaseError):
            self.recover()

    def test_replacement_ci_cannot_transfer_from_other_source_or_event(self):
        invalid = {"head_sha": self.previous, "head_branch": "feature",
                   "event": "workflow_dispatch", "conclusion": "failure",
                   "status": "in_progress", "path": ".github/workflows/other.yml",
                   "head_repository": {"full_name": "gobifrost/bifrost"}}
        for key, value in invalid.items():
            with self.subTest(field=key), patch.object(
                self.release, "api", return_value=dict(self.good, **{key: value})
            ), self.assertRaises(self.release.ReleaseError):
                self.recover()

    def test_untrusted_or_absent_original_ci_is_not_failure_evidence(self):
        self.failed["head_repository"] = {"full_name": "gobifrost/bifrost"}
        with self.assertRaisesRegex(self.release.ReleaseError, "no exact main CI"):
            self.recover()

    def test_replacement_preparation_retains_full_interval_and_failure_history(self):
        replacement = {"version": "v3.1.0", "pull_requests": [],
                       "base_tag": "v3.0.0", "contract_floor": None,
                       "source_commit": self.source}
        previous_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                for name in self.release.MANIFESTS:
                    target = Path(name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(json.dumps({"version": "3.0.0", "name": "preserved"}))
                with patch.object(self.release, "pending_release", return_value=True), \
                     patch.object(self.release, "calculate", return_value=replacement), \
                     patch.object(self.release.release_manifest, "upgrade_notes", return_value=[]):
                    actual = self.release.prepare(
                        self.source, self.prs, supersede_pr=1078, source_ci_run=100
                    )
                self.assertEqual(actual, replacement)
                self.assertEqual(json.loads(Path(self.release.CANDIDATE).read_text()), replacement)
                for name in self.release.MANIFESTS:
                    self.assertEqual(json.loads(Path(name).read_text()),
                                     {"version": "3.1.0", "name": "preserved"})
                notes = Path(self.release.NOTES).read_text()
                self.assertIn("Replaces #1078", notes)
                self.assertIn("Original failed main CI: 90", notes)
                self.assertIn("Replacement source main CI: 100", notes)
            finally:
                os.chdir(previous_cwd)

    def test_explicit_recovery_refuses_when_no_candidate_is_pending(self):
        with patch.object(self.release, "pending_release", return_value=False), \
             self.assertRaisesRegex(self.release.ReleaseError, "unpublished pending"):
            self.release.prepare(self.source, self.prs, supersede_pr=1078, source_ci_run=100)


if __name__ == "__main__":
    unittest.main(verbosity=2)

