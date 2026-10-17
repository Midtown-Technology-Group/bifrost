"""Independent App identity/scope and unknown-outcome lifecycle regressions."""
from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import click
import pytest
from bifrost.app_binding import AppBinding, read_app_binding, write_app_binding
from bifrost.commands import app
from click.testing import CliRunner

APP_ID = "11111111-1111-1111-1111-111111111111"
ORG_ID = "22222222-2222-2222-2222-222222222222"
URL = "https://bifrost.example.test"


def response(payload):
    result = MagicMock()
    result.json.return_value = payload
    result.raise_for_status.return_value = None
    return result


def client():
    value = MagicMock(api_url=URL)
    value.post = AsyncMock(return_value=response({"id": APP_ID}))
    value.get = AsyncMock(return_value=response({"organization": {"id": ORG_ID}}))
    value.refresh_access_token = AsyncMock()
    value._access_token = "synthetic-fixture-session"
    return value


@pytest.mark.parametrize("options,organization", [(["--global"], None), (["--org", ORG_ID], ORG_ID), ([], "omitted")])
def test_create_preserves_explicit_global_org_or_selected_scope(tmp_path, options, organization):
    api = client()
    root = tmp_path / "reviewed-app"
    with patch.object(app, "_client", return_value=api), patch.object(app, "_resolve_org", new=AsyncMock(return_value=ORG_ID)):
        result = CliRunner().invoke(app.app_group, ["create", str(root), "--name", "Reviewed App", *options])
    assert result.exit_code == 0, result.output
    api.post.assert_awaited_once()
    args = api.post.await_args
    assert args.args == ("/api/applications",)
    assert args.kwargs["json"]["app_model"] == "standalone_v2"
    assert args.kwargs["json"]["slug"] == "reviewed-app"
    if organization == "omitted":
        assert "organization_id" not in args.kwargs["json"]
    else:
        assert args.kwargs["json"]["organization_id"] == organization
    assert read_app_binding(root) == AppBinding(URL, APP_ID)
    assert (root / "package.json").is_file()


@pytest.mark.parametrize("case", ["nonempty", "mixed-scope", "invalid-derived-slug"])
def test_create_rejects_ambiguous_scope_and_existing_files_before_remote_effect(tmp_path, case):
    root = tmp_path / "reviewed-app"
    root.mkdir()
    options = []
    if case == "nonempty":
        (root / "retain.txt").write_text("owned")
    elif case == "mixed-scope":
        options = ["--org", ORG_ID, "--global"]
    else:
        options = ["--name", "123-invalid"]
    with patch.object(app, "_client") as selected:
        result = CliRunner().invoke(app.app_group, ["create", str(root), *options])
    assert result.exit_code != 0
    selected.assert_not_called()
    if case == "nonempty":
        assert (root / "retain.txt").read_text() == "owned"


def test_scaffold_failure_preserves_remote_identity_without_destructive_rollback(tmp_path):
    api = client()
    with patch.object(app, "_client", return_value=api), patch.object(app, "_scaffold", side_effect=OSError("local storage unavailable")):
        result = CliRunner().invoke(app.app_group, ["create", str(tmp_path / "project")])
    assert result.exit_code != 0
    api.post.assert_awaited_once()
    api.delete.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [None, {"app_model": "legacy"}, {"app_model": "standalone_v2", "is_solution_managed": True}])
async def test_binding_cannot_reclaim_invisible_legacy_or_solution_owned_app(row):
    api = client()
    apps = [] if row is None else [{"id": APP_ID, **row}]
    api.get.return_value = response({"applications": apps})
    resolver = MagicMock()
    resolver.resolve = AsyncMock(return_value=APP_ID)
    with patch.object(app, "RefResolver", return_value=resolver), pytest.raises(click.ClickException):
        await app._find_app(api, "reviewed-app")
    api.post.assert_not_called()


def test_bind_requires_existing_vite_root_then_retains_selected_remote_identity(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    with patch.object(app, "_client") as selected:
        result = CliRunner().invoke(app.app_group, ["bind", APP_ID, str(root)])
    assert result.exit_code != 0
    selected.assert_not_called()
    (root / "package.json").write_text("{}")
    api = client()
    api.get.return_value = response({"applications": [{"id": APP_ID, "app_model": "standalone_v2", "is_solution_managed": False}]})
    resolver = MagicMock()
    resolver.resolve = AsyncMock(return_value=APP_ID)
    with patch.object(app, "_client", return_value=api), patch.object(app, "RefResolver", return_value=resolver):
        result = CliRunner().invoke(app.app_group, ["bind", APP_ID, str(root)])
    assert result.exit_code == 0, result.output
    assert read_app_binding(root) == AppBinding(URL, APP_ID)
    api.post.assert_not_called()


@pytest.mark.parametrize("nested", [False, True])
def test_migration_cannot_write_into_original_source_before_creating_remote_app(tmp_path, nested):
    source = tmp_path / "source"
    source.mkdir()
    (source / "retain.tsx").write_text("original")
    destination = source / "child" if nested else source
    with patch.object(app, "_client") as selected:
        result = CliRunner().invoke(app.app_group, ["migrate", str(source), str(destination)])
    assert result.exit_code != 0
    selected.assert_not_called()
    assert (source / "retain.tsx").read_text() == "original"


def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "package.json").write_text("{}")
    (root / "src").mkdir()
    (root / "src/main.tsx").write_text("export {}")
    (root / ".env").write_text("FIXTURE_VALUE=must-stay-local\n")
    write_app_binding(root, AppBinding(URL, APP_ID))
    return root


@pytest.mark.parametrize("uncertain", [False, True])
def test_deploy_uploads_once_and_does_not_replay_an_unknown_job_outcome(tmp_path, uncertain):
    root = project(tmp_path)
    api = client()
    captured = {}
    async def post(path, **kwargs):
        captured["path"] = path
        stream = kwargs["files"]["source"][1]
        captured["stream"] = stream
        captured["archive"] = Path(stream.name)
        with zipfile.ZipFile(stream) as archive:
            captured["paths"] = archive.namelist()
        return response({"job_id": "original-job"})
    api.post.side_effect = post
    poll = AsyncMock(side_effect=click.ClickException("unknown original-job outcome")) if uncertain else AsyncMock(return_value={"result": {"application_id": APP_ID}})
    with patch.object(app, "_client", return_value=api), patch.object(app, "_wait_for_deploy", new=poll):
        result = CliRunner().invoke(app.app_group, ["deploy", str(root)])
    assert result.exit_code == (1 if uncertain else 0), result.output
    api.post.assert_awaited_once()
    poll.assert_awaited_once_with(api, "original-job")
    assert captured["path"] == f"/api/applications/{APP_ID}/deploy"
    assert "src/main.tsx" in captured["paths"]
    assert ".env" not in captured["paths"]
    assert captured["stream"].closed
    assert not captured["archive"].exists()
    assert "original-job" in result.output


def test_empty_app_archive_stops_before_any_dispatch(tmp_path):
    root = project(tmp_path)
    api = client()
    with patch.object(app, "_client", return_value=api), patch("bifrost.cli._build_file_filter") as filter_factory:
        filter_factory.return_value.match_file.return_value = True
        result = CliRunner().invoke(app.app_group, ["deploy", str(root)])
    assert result.exit_code != 0
    api.post.assert_not_called()


@pytest.mark.parametrize("vite_failure", [False, True])
def test_start_preserves_app_and_org_identity_with_no_local_workflow_registration(tmp_path, vite_failure):
    root = project(tmp_path)
    (root / "node_modules/bifrost").mkdir(parents=True)
    api = client()
    runner = MagicMock()
    runner.setup = AsyncMock()
    runner.cleanup = AsyncMock()
    site = MagicMock()
    site.start = AsyncMock()
    vite = MagicMock()
    with (
        patch.object(app, "_client", return_value=api),
        patch("bifrost.commands.solution._ensure_port_free") as ports,
        patch("bifrost.commands.solution._wait_for_vite", side_effect=RuntimeError("vite failed") if vite_failure else None),
        patch("bifrost.commands.solution._terminate_process_group") as terminate,
        patch.object(app.shutil, "which", return_value="/fixture/npm"),
        patch.object(app.subprocess, "Popen", return_value=vite),
        patch("bifrost.solution_dev.proxy.build_dev_app", return_value=MagicMock()) as build,
        patch("aiohttp.web.AppRunner", return_value=runner),
        patch("aiohttp.web.TCPSite", return_value=site),
        patch.object(app.asyncio, "sleep", new=AsyncMock(side_effect=asyncio.CancelledError)),
    ):
        result = CliRunner().invoke(app.app_group, ["start", str(root), "--port", "3300"])
    assert result.exit_code == (1 if vite_failure else 0), result.output
    assert ports.call_args_list == [call(3300), call(3301)]
    terminate.assert_called_once_with(vite)
    if vite_failure:
        build.assert_not_called()
        runner.setup.assert_not_called()
    else:
        config = build.call_args.args[0]
        assert config.app_id == APP_ID
        assert config.org_id == ORG_ID
        assert config.solution_id is None
        assert config.local_workflows is False
        runner.cleanup.assert_awaited_once()
        site.start.assert_awaited_once()
    api.post.assert_not_called()


def test_unbound_project_cannot_start_or_deploy_live_resources(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    with patch.object(app, "_client") as selected:
        for command in ("start", "deploy"):
            result = CliRunner().invoke(app.app_group, [command, str(tmp_path)])
            assert result.exit_code != 0
            assert "No App binding found" in result.output
    selected.assert_not_called()
