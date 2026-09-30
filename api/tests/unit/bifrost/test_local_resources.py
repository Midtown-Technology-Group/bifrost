"""Local resource execution cannot request production bytes or choose arbitrary files."""

import asyncio
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bifrost import cli, resources
from bifrost._local_resources import (
    LocalResourceError, current_local_resources, local_resource_context,
)
from bifrost.commands.solution import solution_group
from bifrost.solution_dev.function_host import FunctionHost


def checkout(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir()
    source = root / "functions/run.py"
    source.parent.mkdir()
    source.write_text('from bifrost import workflow, resources\n@workflow\nasync def run():\n return await resources.read("rates.json")\n')
    data = root / "data/rates.json"
    data.parent.mkdir()
    data.write_text('{"rate":1}')
    value = {"schema_version": "bifrost.solution-workflow-delivery/v1", "solution_id": str(uuid4()),
        "files": {"run.py": "functions/run.py"}, "resources": {"rates.json": "data/rates.json"},
        "workflows": [{"id": str(uuid4()), "path": "run.py", "function_name": "run",
            "organization_id": None, "controls": {}, "runtime_bounds": {
                "max_duration_seconds": 20, "max_external_calls": 10,
                "max_records_read": 100, "max_output_bytes": 4096}}]}
    recipe = root / "delivery.json"
    recipe.write_text(json.dumps(value))
    return source, recipe, data


@pytest.fixture(autouse=True)
def no_resource_http(monkeypatch):
    def forbidden():
        raise AssertionError("Local resource reads must never obtain an HTTP client")
    monkeypatch.setattr(importlib.import_module("bifrost.resources"), "get_client", forbidden)


@pytest.mark.asyncio
async def test_declared_dirty_bytes_and_nested_context_restore(tmp_path):
    source, recipe, data = checkout(tmp_path)
    assert current_local_resources() is None
    with local_resource_context(source, recipe):
        assert await resources.read("rates.json") == '{"rate":1}'
        data.write_text('{"rate":2}')
        assert await resources.read_bytes("rates.json") == b'{"rate":2}'
        with local_resource_context(source):
            with pytest.raises(LocalResourceError, match="undeclared"):
                await resources.read("rates.json")
        assert await resources.read("rates.json") == '{"rate":2}'
    assert current_local_resources() is None


@pytest.mark.asyncio
async def test_parallel_local_runs_cannot_share_resource_maps(tmp_path):
    left, right = checkout(tmp_path / "left"), checkout(tmp_path / "right")
    right[2].write_text('{"rate":2}')

    async def read(fixture):
        with local_resource_context(fixture[0], fixture[1]):
            await asyncio.sleep(0)
            return await resources.read("rates.json")
    assert await asyncio.gather(read(left), read(right)) == ['{"rate":1}', '{"rate":2}']
    assert current_local_resources() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing", "symlink", "parent_symlink", "directory", "empty", "oversized", "total_oversized"])
async def test_local_damage_never_falls_back_to_remote_bytes(tmp_path, damage):
    source, recipe, data = checkout(tmp_path)
    if damage == "empty":
        data.write_bytes(b"")
    elif damage == "oversized":
        with data.open("wb") as stream:
            stream.truncate(2 * 1024 * 1024 + 1)
    elif damage == "total_oversized":
        value = json.loads(recipe.read_text())
        value["resources"] = {f"r{i}.json": f"data/r{i}.json" for i in range(6)}
        for path in value["resources"].values():
            with (tmp_path / path).open("wb") as stream:
                stream.truncate(2 * 1024 * 1024)
        recipe.write_text(json.dumps(value))
    else:
        data.unlink()
        if damage == "symlink":
            outside = tmp_path.parent / (tmp_path.name + "-outside.json")
            outside.write_text("private")
            data.symlink_to(outside)
        elif damage == "parent_symlink":
            data.parent.rmdir()
            outside = tmp_path.parent / (tmp_path.name + "-outside")
            outside.mkdir()
            (outside / "rates.json").write_text("private")
            data.parent.symlink_to(outside, target_is_directory=True)
        elif damage == "directory":
            data.mkdir()
    with local_resource_context(source, recipe):
        with pytest.raises(LocalResourceError):
            await resources.read("r0.json" if damage == "total_oversized" else "rates.json")
    assert current_local_resources() is None


@pytest.mark.asyncio
async def test_fifo_is_rejected_without_open_or_blocking(tmp_path):
    source, recipe, data = checkout(tmp_path)
    data.unlink()
    os.mkfifo(data)
    with local_resource_context(source, recipe):
        with pytest.raises(LocalResourceError, match="regular"):
            await resources.read("rates.json")


@pytest.mark.parametrize("damage", ["escape", "auth_state", "duplicate", "recipe_symlink", "different_source", "no_git", "oversized_recipe"])
def test_invalid_recipe_cannot_bind_a_local_context(tmp_path, damage):
    source, recipe, _ = checkout(tmp_path)
    value = json.loads(recipe.read_text())
    if damage == "escape":
        value["resources"]["rates.json"] = "../private.json"
        recipe.write_text(json.dumps(value))
    elif damage == "auth_state":
        value["resources"]["rates.json"] = "data/storage-state.json"
        recipe.write_text(json.dumps(value))
    elif damage == "duplicate":
        recipe.write_text('{"resources":{},"resources":{}}')
    elif damage == "recipe_symlink":
        original = recipe.with_suffix(".original.json")
        recipe.rename(original)
        recipe.symlink_to(original)
    elif damage == "different_source":
        source = tmp_path / "other.py"
        source.write_text("pass")
    elif damage == "no_git":
        (tmp_path / ".git").rmdir()
    else:
        recipe.write_bytes(b" " * (128 * 1024 + 1))
    with pytest.raises(LocalResourceError):
        with local_resource_context(source, recipe):
            pytest.fail("Invalid recipe reached the workflow body")
    assert current_local_resources() is None


def offline_cli(monkeypatch):
    class OfflineClient:
        @staticmethod
        def get_instance(**_kwargs):
            raise RuntimeError("offline")
    monkeypatch.setattr(cli, "BifrostClient", OfflineClient)


def test_actual_direct_cli_reads_checkout_without_auth_or_release_ceremony(tmp_path, monkeypatch, capsys):
    source, recipe, data = checkout(tmp_path)
    offline_cli(monkeypatch)
    data.write_text('{"rate":99}')
    assert cli.handle_run([str(source), "-w", "run", "--resource-recipe", str(recipe)]) == 0
    assert json.loads(capsys.readouterr().out) == '{"rate":99}'
    assert current_local_resources() is None
    assert cli.handle_run([str(source), "-w", "run"]) == 1
    assert "undeclared" in capsys.readouterr().err


def test_local_module_import_uses_same_explicit_map_and_cannot_fall_back(tmp_path, monkeypatch, capsys):
    source, recipe, _ = checkout(tmp_path)
    source.write_text('import asyncio\nfrom bifrost import workflow, resources\nIMPORTED = asyncio.run(resources.read("rates.json"))\n@workflow\nasync def run():\n return IMPORTED\n')
    offline_cli(monkeypatch)
    assert cli.handle_run([str(source), "-w", "run", "--resource-recipe", str(recipe)]) == 0
    assert json.loads(capsys.readouterr().out) == '{"rate":1}'
    assert cli.handle_run([str(source), "-w", "run"]) == 1
    assert "undeclared" in capsys.readouterr().err
    host = FunctionHost(tmp_path, resource_recipe=recipe)
    host.reload()
    assert asyncio.run(host.run("functions/run.py::run", {})) == '{"rate":1}'
    unbound = FunctionHost(tmp_path)
    unbound.reload()
    assert "undeclared" in unbound.failures()["functions/run.py"]
    assert current_local_resources() is None


def test_resource_run_cannot_write_incomplete_loose_promotion_evidence(tmp_path, monkeypatch):
    source, recipe, _ = checkout(tmp_path)
    offline_cli(monkeypatch)
    evidence = tmp_path / "evidence.json"
    assert cli.handle_run([str(source), "-w", "run", "--resource-recipe", str(recipe),
        "--promotion-evidence", str(evidence)]) == 1
    assert not evidence.exists()


@pytest.mark.asyncio
async def test_interactive_cli_binds_resources_only_for_local_body(tmp_path, monkeypatch):
    source, recipe, _ = checkout(tmp_path)
    posted = []

    class Client:
        api_url = "https://example.invalid"

        async def post(self, path, **kwargs):
            posted.append((path, kwargs))
            return SimpleNamespace(status_code=200)

        async def get(self, _path):
            return SimpleNamespace(status_code=200, json=lambda: {
                "execution_id": "local-session", "workflow_name": "run", "params": {}})

    async def run():
        return await resources.read("rates.json")

    monkeypatch.setattr(cli.asyncio, "sleep", AsyncMock())
    assert await cli._run_session_flow(Client(), "session", str(source), [], {"run": run},
        "run", True, resource_recipe=recipe) == 0
    assert posted[-1][1]["json"]["result"] == '{"rate":1}'
    assert current_local_resources() is None


@pytest.mark.asyncio
async def test_function_host_reloads_dirty_resources_and_fails_without_map(tmp_path):
    source, recipe, data = checkout(tmp_path)
    host = FunctionHost(tmp_path, resource_recipe=recipe)
    host.reload()
    assert await host.run("functions/run.py::run", {}) == '{"rate":1}'
    data.write_text('{"rate":2}')
    assert await host.run("functions/run.py::run", {}) == '{"rate":2}'
    unbound = FunctionHost(tmp_path)
    unbound.reload()
    with pytest.raises(LocalResourceError, match="undeclared"):
        await unbound.run("functions/run.py::run", {})
    assert current_local_resources() is None


def test_start_exposes_explicit_recipe_option():
    from click.testing import CliRunner

    result = CliRunner().invoke(solution_group, ["start", "--help"])
    assert result.exit_code == 0
    assert "--resource-recipe" in result.output
