"""Regression tests for CLI-controlled file, JSON, and path inputs."""

from pathlib import Path

import click
import httpx
import pytest
from click.testing import CliRunner

from bifrost import client as bifrost_client_module
from bifrost.commands.agents import _load_str_file
from bifrost.commands.apps import _is_safe_slug_ref, apps_group
from bifrost.commands.integrations import load_schema_file
from bifrost.dto_flags import load_dict_value


@pytest.mark.parametrize(
    "loader,value",
    [
        (load_schema_file, "@/definitely/missing/config-schema.json"),
        (load_schema_file, "not json"),
        (load_dict_value, "@/definitely/missing/form-schema.json"),
        (load_dict_value, "not json"),
        (_load_str_file, "@/definitely/missing/system-prompt.md"),
    ],
)
def test_invalid_local_input_is_a_click_parameter_error(loader, value: str) -> None:
    with pytest.raises(click.BadParameter):
        loader(value)


def test_config_schema_rejects_non_object_entries(tmp_path: Path) -> None:
    schema = tmp_path / "schema.json"
    schema.write_text('[{"key": "ok"}, "not-an-object"]', encoding="utf-8")

    with pytest.raises(click.BadParameter, match="entry must be an object"):
        load_schema_file(f"@{schema}")


@pytest.mark.parametrize("ref", ["../health/detailed", "foo/bar", ".", ".."])
def test_app_slug_fast_path_rejects_endpoint_traversal(ref: str) -> None:
    assert not _is_safe_slug_ref(ref)


@pytest.mark.parametrize("ref", ["portal", "portal-2", "App_3"])
def test_app_slug_fast_path_accepts_single_safe_segment(ref: str) -> None:
    assert _is_safe_slug_ref(ref)


def test_apps_create_validates_deps_before_remote_create(monkeypatch) -> None:
    class Client:
        api_url = "https://bifrost.invalid"
        _access_token = "test"

        def __init__(self) -> None:
            self.posts: list[str] = []

        async def post(self, path: str, **_kwargs) -> httpx.Response:
            self.posts.append(path)
            request = httpx.Request("POST", self.api_url + path)
            return httpx.Response(201, json={"id": "created"}, request=request)

    client = Client()
    monkeypatch.setattr(
        bifrost_client_module.BifrostClient,
        "get_instance",
        classmethod(lambda cls, require_auth=False: client),
    )

    result = CliRunner().invoke(
        apps_group,
        [
            "create",
            "--name",
            "Unsafe partial create",
            "--slug",
            "unsafe-partial-create",
            "--app-model",
            "inline_v1",
            "--deps",
            "@/definitely/missing/package.json",
        ],
    )

    assert result.exit_code != 0
    assert client.posts == []
