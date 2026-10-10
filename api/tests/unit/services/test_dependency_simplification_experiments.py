"""Bounded replacement experiments for #1104; differences are NO-GO evidence.

Run through ./test.sh tests/unit/services/test_dependency_simplification_experiments.py -v.
These characterize alternatives; they do not authorize changing their contracts.
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from importlib.metadata import distributions, requires

import httpx2
import pytest
import regex
from apscheduler.triggers.cron import CronTrigger
from croniter import croniter
from pydantic_ai.retries import AsyncHTTPX2TenacityTransport
from pydantic_settings import BaseSettings, SettingsConfigDict
from tenacity import stop_after_attempt

from bifrost.credentials import load_allowed_dotenv
from src.core import telemetry
from src.services.agent_runtime.retry_transport import _create_retry_transport
from src.services.cron_parser import cron_to_human_readable


def test_regex_timeout_replaces_syntax_heuristic_for_execution_safety():
    # Nested quantifiers are legal; the execution engine must bound nonmatches.
    pattern = regex.compile("(a+)+$")
    assert pattern.fullmatch("aaaa") is not None
    with pytest.raises(TimeoutError):
        list(pattern.finditer("a" * 100_000 + "!", timeout=0.001))


def test_cron_trigger_is_not_a_drop_in_description_or_calendar_replacement():
    expression = "0 9 * * 0"
    trigger = CronTrigger.from_crontab(expression, timezone="UTC")
    assert str(trigger) != cron_to_human_readable(expression)
    now = datetime(2026, 10, 3, tzinfo=timezone.utc)
    # croniter's 0 is Sunday; APScheduler's 0 is Monday. Bifrost uses these
    # libraries for different purposes, so an apparent overlap is not parity.
    assert croniter(expression, now).get_next(datetime).weekday() == 6
    assert trigger.get_next_fire_time(None, now).weekday() == 0


def test_process_metric_does_not_supply_container_child_cpu(monkeypatch, tmp_path):
    import psutil

    parent_seconds = sum(psutil.Process().cpu_times()[:2])
    # A cgroup includes active execution children, unlike a process CPU counter.
    stat = tmp_path / "cpu.stat"
    stat.write_text(f"usage_usec {int((parent_seconds + 30) * 1_000_000)}\n")
    monkeypatch.setattr(telemetry, "_CGROUP_CPU_STAT", stat)
    container_seconds = telemetry._cgroup_cpu_seconds()
    assert container_seconds is not None
    assert container_seconds - parent_seconds == pytest.approx(30, abs=0.001)


def test_settings_default_precedence_differs_from_cli_dotenv(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("BIFROST_API_URL=https://project.example\nBIFROST_API_KEY=untrusted\n")
    monkeypatch.setenv("BIFROST_API_URL", "https://process.example")
    monkeypatch.delenv("BIFROST_API_KEY", raising=False)

    class Settings(BaseSettings):
        api_url: str
        model_config = SettingsConfigDict(env_prefix="BIFROST_", extra="ignore")

    assert Settings(_env_file=env).api_url == "https://process.example"
    load_allowed_dotenv(env)
    import os

    assert os.environ["BIFROST_API_URL"] == "https://project.example"
    assert "BIFROST_API_KEY" not in os.environ
    assert any(requirement.startswith("python-dotenv") for requirement in requires("pydantic-settings") or [])


@pytest.mark.asyncio
async def test_native_retry_exhaustion_does_not_preserve_final_http_response():
    async def handler(request):
        return httpx2.Response(429, request=request)

    native = AsyncHTTPX2TenacityTransport(
        config={"stop": stop_after_attempt(1), "reraise": True},
        wrapped=httpx2.MockTransport(handler),
        validate_response=lambda response: response.raise_for_status(),
    )
    async with httpx2.AsyncClient(transport=native) as client:
        with pytest.raises(httpx2.HTTPStatusError):
            await client.get("https://provider.example/model")

    # Bifrost leaves the exhausted response available for provider classification.
    async def no_sleep(_delay):
        pass

    async with httpx2.AsyncClient(transport=_create_retry_transport(httpx2.MockTransport(handler), sleep=no_sleep)) as client:
        assert (await client.get("https://provider.example/model")).status_code == 429


def test_distribution_metadata_matches_locked_container_pip_inventory():
    # Compare real metadata to pip, not a mocked list. This also characterizes
    # the first-distribution-wins rule and display-name ordering.
    inventory = {}
    import re

    for distribution in distributions():
        name = distribution.metadata.get("Name")
        if name:
            key = re.sub(r"[-_.]+", "-", name).lower()
            inventory.setdefault(key, {"name": name, "version": distribution.version})
    actual = sorted(inventory.values(), key=lambda package: re.sub(r"[-_.]+", "-", package["name"]).lower())
    completed = subprocess.run(
        [sys.executable, "-m", "pip", "list", "--format=json"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    expected = [{"name": item["name"], "version": item["version"]} for item in json.loads(completed.stdout)]
    assert actual == expected


def test_in_process_metadata_misses_user_site_created_after_startup(tmp_path):
    import os

    # Non-root workers create their user site on the first pip install. A fresh
    # pip process sees it, but the running worker's sys.path still omits it.
    probe = '''
import json, site, subprocess, sys
from importlib.metadata import distributions
from pathlib import Path
assert site.ENABLE_USER_SITE is True
user_site = Path(site.getusersitepackages())
assert not user_site.exists()
assert str(user_site) not in sys.path
metadata = user_site / "dependency_inventory_probe-1.0.dist-info"
metadata.mkdir(parents=True)
(metadata / "METADATA").write_text("Name: DependencyInventoryProbe\\nVersion: 1.0\\n")
assert not any(d.metadata.get("Name") == "DependencyInventoryProbe" for d in distributions())
fresh = subprocess.run([sys.executable, "-m", "pip", "list", "--format=json"],
                       capture_output=True, text=True, check=True, timeout=30)
assert any(p["name"] == "DependencyInventoryProbe" for p in json.loads(fresh.stdout))
'''
    env = {**os.environ, "PYTHONUSERBASE": str(tmp_path / "new-user-base")}
    env.pop("PYTHONNOUSERSITE", None)
    subprocess.run([sys.executable, "-c", probe], env=env, check=True,
                   capture_output=True, text=True, timeout=40)
