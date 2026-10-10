"""Complete package review preserves mixed App/workflow/resource source."""

import copy
import hashlib
import io
import json
import subprocess
import zipfile

import pytest
import yaml
from click.testing import CliRunner

from bifrost.commands.solution import solution_group
from bifrost.solution_package_delivery import (
    PACKAGE_RECIPE_SCHEMA,
    build_solution_package_archive,
    load_solution_package_recipe,
    review_solution_package_source,
)

SID = "00000000-0000-0000-0000-000000000010"
APP = "00000000-0000-0000-0000-000000000011"
WORKFLOW = "00000000-0000-0000-0000-000000000012"
TABLE = "00000000-0000-0000-0000-000000000013"


def source():
    return {
        "bifrost.solution.yaml": b"slug: example\nname: Example\n",
        ".bifrost/apps.yaml": yaml.safe_dump(
            {
                "apps": {
                    APP: {
                        "id": APP,
                        "app_model": "standalone_v2",
                        "path": "apps/example",
                        "access_level": "authenticated",
                    }
                }
            }
        ).encode(),
        ".bifrost/workflows.yaml": yaml.safe_dump(
            {
                "workflows": {
                    WORKFLOW: {
                        "id": WORKFLOW,
                        "path": "functions/main.py",
                        "function_name": "main",
                        "access_level": "role_based",
                        "endpoint_enabled": False,
                    }
                }
            }
        ).encode(),
        ".bifrost/tables.yaml": yaml.safe_dump(
            {
                "tables": {
                    TABLE: {
                        "id": TABLE,
                        "name": "evidence",
                        "policies": [{"$ref": "admin_bypass"}],
                    }
                }
            }
        ).encode(),
        ".bifrost/files.yaml": b"locations:\n  - audit-evidence\n",
        "functions/main.py": b"def main():\n    return {'ok': True}\n",
        "modules/__init__.py": b"",
        "apps/example/src/main.tsx": b"export const version = 1;",
        "apps/example/public/icon.bin": b"\x00\xff\x01",
    }


def recipe(files):
    return {
        "schema_version": PACKAGE_RECIPE_SCHEMA,
        "solution_id": SID,
        "organization_id": None,
        "repo_subpath": "solutions/example",
        "manifest_hashes": {
            path: "sha256:" + hashlib.sha256(raw).hexdigest()
            for path, raw in files.items()
            if path == "bifrost.solution.yaml" or path.startswith(".bifrost/")
        },
    }


def review(files, value=None, modes=None):
    return review_solution_package_source(
        value or recipe(files),
        files,
        modes or dict.fromkeys(files, "100644"),
        source_commit_sha="a" * 40,
        source_tree_sha="b" * 40,
    )


def test_complete_mixed_package_keeps_resources_and_empty_binary_source():
    files = source()
    proof = review(files)
    assert proof["entities"] == {
        "apps": [APP],
        "workflows": [WORKFLOW],
        "tables": [TABLE],
        "file_locations": ["audit-evidence"],
    }
    assert {x["path"]: x["size"] for x in proof["source_files"]}[
        "modules/__init__.py"
    ] == 0
    assert len(proof["source_files"]) == len(files)
    assert proof["organization_id"] is None
    assert proof["source_ci_verified"] is False
    assert proof["installed_controls_verified"] is False
    assert proof["runtime_verified"] is False
    modes = dict.fromkeys(files, "100644")
    archive = build_solution_package_archive(files, modes)
    assert proof["source_archive_sha256"] == hashlib.sha256(archive).hexdigest()
    assert archive == build_solution_package_archive(
        dict(reversed(list(files.items()))), modes
    )
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        assert {name: package.read(name) for name in package.namelist()} == files


def test_workflow_package_keeps_complete_resource_source_without_an_app():
    files = {path: raw for path, raw in source().items()
             if path != ".bifrost/apps.yaml" and not path.startswith("apps/")}
    proof = review(files)
    assert "apps" not in proof["entities"]
    assert proof["entities"]["workflows"] == [WORKFLOW]
    assert proof["entities"]["tables"] == [TABLE]
    assert proof["entities"]["file_locations"] == ["audit-evidence"]
    assert {item["path"] for item in proof["source_files"]} == set(files)
    assert proof["runtime_verified"] is False


@pytest.mark.parametrize(
    "path", [".bifrost/tables.yaml", ".bifrost/workflows.yaml", ".bifrost/files.yaml"]
)
def test_app_only_filtering_cannot_certify_a_complete_package(path):
    files = source()
    expected = recipe(files)
    del files[path]
    with pytest.raises(ValueError, match="contracts differ"):
        review(files, expected)


def test_unreviewed_resource_policy_change_is_rejected():
    files = source()
    expected = recipe(files)
    files[".bifrost/tables.yaml"] = files[".bifrost/tables.yaml"].replace(
        b"admin_bypass", b"public_access"
    )
    with pytest.raises(ValueError, match="contracts differ"):
        review(files, expected)


@pytest.mark.parametrize("key", ["dist_files", "bin_dist_files"])
def test_reviewed_source_cannot_supply_local_prebuilt_outputs(key):
    files = source()
    document = yaml.safe_load(files[".bifrost/apps.yaml"])
    document["apps"][APP][key] = {"index.html": "unproven"}
    files[".bifrost/apps.yaml"] = yaml.safe_dump(document).encode()
    with pytest.raises(ValueError, match="compile from reviewed source"):
        review(files)


@pytest.mark.parametrize("mode", ["120000", "160000"])
def test_symlink_and_submodule_source_are_rejected(mode):
    files = source()
    modes = dict.fromkeys(files, "100644")
    modes["modules/__init__.py"] = mode
    with pytest.raises(ValueError, match="regular Git files"):
        review(files, modes=modes)


def test_global_scope_must_be_explicit_and_json_keys_unique():
    value = recipe(source())
    del value["organization_id"]
    with pytest.raises(ValueError):
        load_solution_package_recipe(json.dumps(value).encode())
    raw = json.dumps(recipe(source())).replace(
        '"organization_id": null', '"organization_id": null, "organization_id": "other"'
    )
    with pytest.raises(ValueError, match="duplicate JSON"):
        load_solution_package_recipe(raw.encode())


def test_ambiguous_yaml_and_entity_id_changes_fail_closed():
    files = source()
    files[".bifrost/apps.yaml"] += b"apps: {}\n"
    with pytest.raises(ValueError, match="duplicate mapping"):
        review(files)
    files = source()
    document = yaml.safe_load(files[".bifrost/workflows.yaml"])
    document["workflows"][WORKFLOW]["id"] = APP
    files[".bifrost/workflows.yaml"] = yaml.safe_dump(document).encode()
    with pytest.raises(ValueError, match="identity differs"):
        review(files)


def test_mode_changes_and_application_code_changes_have_distinct_artifacts():
    files = source()
    original = review(files)
    modified = copy.copy(files)
    modified["apps/example/src/main.tsx"] += b"\nexport const next = 2;"
    assert review(modified)["artifact_digest"] != original["artifact_digest"]
    modes = dict.fromkeys(files, "100644")
    modes["functions/main.py"] = "100755"
    assert review(files, modes=modes)["artifact_digest"] != original["artifact_digest"]


def test_cli_reads_exact_git_source_despite_dirty_worktree(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True
        ).stdout

    files = source()
    root = tmp_path / "solutions/example"
    for path, raw in files.items():
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    git("init", "-q")
    git("add", ".")
    git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "Fixture",
    )
    commit = git("rev-parse", "HEAD").decode().strip()
    (root / "apps/example/src/main.tsx").write_text("dirty source must not be reviewed")
    contract = tmp_path / "recipe.json"
    contract.write_text(json.dumps(recipe(files)))
    monkeypatch.setattr(
        "bifrost.commands.solution.BifrostClient.get_instance",
        lambda **_: pytest.fail("Offline review requested an API client"),
    )
    result = CliRunner().invoke(
        solution_group,
        [
            "review-package",
            str(contract),
            "--source-commit",
            commit,
            "--repository-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    proof = json.loads(result.output)
    original = next(
        x for x in proof["source_files"] if x["path"] == "apps/example/src/main.tsx"
    )
    assert original["sha256"] == hashlib.sha256(files[original["path"]]).hexdigest()
    assert proof["entities"]["workflows"] == [WORKFLOW]
    assert proof["source_commit_sha"] == commit
