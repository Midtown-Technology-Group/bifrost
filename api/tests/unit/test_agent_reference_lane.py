"""Closed-egress harness checks; no Docker daemon or tenant code is used here."""

from __future__ import annotations

import configparser
import fnmatch
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPTS = (
    Path("/repo/scripts")
    if Path("/repo/scripts").is_dir()
    else Path(__file__).resolve().parents[3] / "scripts"
)
SPEC = importlib.util.spec_from_file_location(
    "agent_reference_compose", SCRIPTS / "render-agent-reference-compose.py"
)
assert SPEC and SPEC.loader
lane = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lane)
ROOT = Path("/owned/reference")
PROJECT = (
    f"bifrost-agent-reference-{hashlib.sha256(str(ROOT).encode()).hexdigest()[:8]}"
)
LOG = Path("/tmp") / f"bifrost-{PROJECT}"
SOURCE = "a" * 40
LANE_ID = "b" * 32
CONTEXT = Path("/owned/private-context")
RUNNER_ID = "f" * 64
RUNNER_PATH = (
    Path("/app/scripts/agent_reference_runner.py")
    if Path("/app/scripts/agent_reference_runner.py").is_file()
    else SCRIPTS.parent / "api/scripts/agent_reference_runner.py"
)
RUNNER_SPEC = importlib.util.spec_from_file_location(
    "agent_reference_runner", RUNNER_PATH
)
assert RUNNER_SPEC and RUNNER_SPEC.loader
runner = importlib.util.module_from_spec(RUNNER_SPEC)
RUNNER_SPEC.loader.exec_module(runner)


@pytest.fixture
def base():
    services = {}
    for name in lane.SERVICES:
        app = name in lane.PROCESSES or name == "scheduler-fixtures"
        services[name] = {
            "image": "bifrost-test-api-dev:latest" if app else "infrastructure-fixture",
            "environment": {},
            "volumes": [],
            "networks": {"default": None},
        }
        if app:
            services[name]["build"] = {
                "context": str(ROOT),
                "dockerfile": "api/Dockerfile.dev",
            }
    services["test-runner"]["volumes"] = [
        {
            "type": "bind",
            "source": str(ROOT / "test-fixtures/agent-reference"),
            "target": "/app/reference-assets",
            "read_only": True,
        },
        {
            "type": "bind",
            "source": str(ROOT / "api/tests"),
            "target": "/app/tests",
            "read_only": True,
        },
        {
            "type": "bind",
            "source": str(ROOT / ".env.debug"),
            "target": "/app/.env.debug",
            "read_only": True,
        },
    ]
    return {
        "name": PROJECT,
        "services": services,
        "networks": {"default": {"name": f"{PROJECT}_default"}},
        "volumes": {
            name: {"name": f"{PROJECT}_{name}"}
            for name in ("test-mounts", "test-coverage", "playwright-results")
        },
    }


def rendered(base):
    return lane.render(base, ROOT, PROJECT, LOG, SOURCE, CONTEXT, LANE_ID)


def test_renderer_closes_services_network_consumers_and_assets(base):
    config = rendered(base)
    assert set(config["services"]) == set(lane.SERVICES)
    assert config["networks"] == {
        "default": {"name": f"{PROJECT}_default", "internal": True}
    }
    for service in lane.PROCESSES:
        env = config["services"][service]["environment"]
        assert env["BIFROST_WORK_DELIVERY_BACKEND"] == "postgres"
        assert all(env[key] == "" for key in lane.OPTIONAL)
    assert (
        config["services"]["worker"]["environment"]["BIFROST_WORKER_CONSUMERS"]
        == "workflow,agent-run,summarize"
    )
    runner = config["services"]["test-runner"]
    assert runner["command"] == ["python", "/app/scripts/agent_reference_runner.py"]
    assert runner["entrypoint"] == [] and runner["user"] == "1000:1000"
    custody_mount = next(
        m for m in runner["volumes"] if m["target"] == "/app/reference-custody"
    )
    assert (
        custody_mount["source"] == str(CONTEXT / "custody")
        and custody_mount["read_only"] is True
    )
    assert all(
        not any(
            m["target"] == "/app/reference-custody"
            for m in config["services"][name]["volumes"]
        )
        for name in lane.SERVICES
        if name != "test-runner"
    )
    assets = [m for m in runner["volumes"] if m["target"] == "/app/reference-assets"]
    assert len(assets) == 1
    assert assets[0]["read_only"] is True
    assert assets[0]["source"] == str(ROOT / "test-fixtures/agent-reference")
    assert not any(m["target"] == "/app/.env.debug" for m in runner["volumes"])
    for name in ("api", "api-replica", "test-runner"):
        mount = next(
            m
            for m in config["services"][name]["volumes"]
            if m["target"]
            == (
                "/app/reference-observer-status"
                if name == "test-runner"
                else f"/app/reference-observer-status/{name}"
            )
        )
        assert mount["read_only"] is (name == "test-runner")
        assert mount["volume"]["nocopy"] is True
        assert mount["volume"].get("subpath") == (
            None if name == "test-runner" else name
        )


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "writable", "foreign-source", "other-reader"]
)
def test_reference_assets_are_exactly_once_readonly_runner_only(base, mutation):
    mounts = base["services"]["test-runner"]["volumes"]
    assets = next(m for m in mounts if m["target"] == "/app/reference-assets")
    if mutation == "missing":
        mounts.remove(assets)
    elif mutation == "duplicate":
        mounts.append(assets.copy())
    elif mutation == "writable":
        assets["read_only"] = False
    elif mutation == "foreign-source":
        assets["source"] = "/unapproved/source"
    else:
        base["services"]["api"]["volumes"].append(assets.copy())
    with pytest.raises(ValueError):
        rendered(base)


@pytest.mark.parametrize(
    "field,value",
    [
        ("ports", ["8000:8000"]),
        ("env_file", [".env.test"]),
        ("secrets", ["live"]),
        ("network_mode", "host"),
        ("extra_hosts", ["host:host-gateway"]),
        ("privileged", True),
        ("use_api_socket", True),
    ],
)
def test_renderer_rejects_unsafe_service_fields(base, field, value):
    base["services"]["api"][field] = value
    with pytest.raises(ValueError):
        rendered(base)


@pytest.mark.parametrize(
    "mutation", ["network", "mount", "env", "default-backend", "build"]
)
def test_renderer_rejects_changed_custody_inputs(base, mutation):
    if mutation == "network":
        base["networks"]["default"]["external"] = True
    elif mutation == "mount":
        next(
            m
            for m in base["services"]["test-runner"]["volumes"]
            if m["target"] == "/app/tests"
        )["source"] = "/root/.aws"
    elif mutation == "env":
        base["services"]["api"]["environment"]["HTTP_PROXY"] = "http://proxy"
    elif mutation == "default-backend":
        base["services"]["api"]["environment"]["BIFROST_WORK_DELIVERY_BACKEND"] = (
            "rabbitmq"
        )
    else:
        base["services"]["api"]["build"]["args"] = {"TOKEN": "unapproved"}
    with pytest.raises(ValueError):
        rendered(base)


def image_witnesses(config):
    images = []
    for index, tag in enumerate(
        sorted({s["image"] for s in config["services"].values()}), 1
    ):
        images.append(
            {
                "Id": f"sha256:{index:064x}",
                "RepoTags": [tag],
                "Config": {
                    "Cmd": ["fixture-default"],
                    "Entrypoint": ["/entrypoint.sh"],
                    "User": "",
                    "WorkingDir": "/app",
                },
            }
        )
    return images


def binding(pins):
    return {
        "version": 1,
        "source": SOURCE,
        "project": PROJECT,
        "container_name": f"{PROJECT}-pytest-runner",
        "nonce": "b" * 32,
        "argv": ["pytest", lane.CASE, "-v", *runner.SUFFIX],
        "image_id": pins["test-runner"]["id"],
    }


def inspections(config, pins):
    containers = []
    for name in lane.SERVICES:
        planned = config["services"][name]
        mounts = []
        for mount in planned["volumes"]:
            actual = {
                "Type": mount["type"],
                "Destination": mount["target"],
                "RW": not mount.get("read_only", False),
            }
            if mount["type"] == "bind":
                actual["Source"] = mount["source"]
            elif mount.get("source"):
                actual["Name"] = config["volumes"][mount["source"]]["name"]
            mounts.append(actual)
        containers.append(
            {
                "Config": {
                    "Image": planned["image"],
                    "Labels": {
                        "com.docker.compose.project": PROJECT,
                        "com.docker.compose.service": name,
                    },
                    "Env": [
                        f"{key}={value}"
                        for key, value in planned["environment"].items()
                    ],
                    "Cmd": lane.argv(planned.get("command", pins[name]["cmd"])),
                    "Entrypoint": lane.argv(
                        planned.get("entrypoint", pins[name]["entrypoint"])
                    ),
                    "Hostname": RUNNER_ID[:12] if name == "test-runner" else "fixture",
                    "User": str(planned.get("user", pins[name]["user"])),
                    "WorkingDir": planned.get("working_dir", pins[name]["working_dir"]),
                },
                "Id": RUNNER_ID if name == "test-runner" else "c" * 64,
                "Name": f"/{PROJECT}-pytest-runner"
                if name == "test-runner"
                else f"/{name}",
                "Image": pins[name]["id"],
                "State": {"Running": True, "Status": "running", "ExitCode": 0},
                "HostConfig": {
                    "Mounts": [
                        {
                            "Type": "volume",
                            "Source": config["volumes"][m["source"]]["name"],
                            "Target": m["target"],
                            "ReadOnly": m.get("read_only", False),
                            "VolumeOptions": {
                                "NoCopy": m["volume"]["nocopy"],
                                **(
                                    {"Subpath": m["volume"]["subpath"]}
                                    if "subpath" in m["volume"]
                                    else {}
                                ),
                            },
                        }
                        for m in planned["volumes"]
                        if m.get("source") == "observer-status"
                    ]
                },
                "Mounts": mounts,
                "NetworkSettings": {"Networks": {f"{PROJECT}_default": {}}},
            }
        )
    networks = [
        {
            "Name": f"{PROJECT}_default",
            "Internal": True,
            "Labels": {"com.docker.compose.project": PROJECT},
        }
    ]
    volumes = [
        {"Name": value["name"], "Labels": {"com.docker.compose.project": PROJECT}}
        for value in config["volumes"].values()
    ]
    return containers, networks, volumes


def test_effective_custody_is_sanitized_and_observer_stays_pending(base):
    config = rendered(base)
    pins = lane.pin_images(config, image_witnesses(config))
    report = lane.verify(
        config,
        *inspections(config, pins),
        PROJECT,
        pins,
        binding(pins),
        RUNNER_ID,
        source=SOURCE,
    )
    assert set(report["processes"]) == set(lane.PROCESSES)
    assert "not supplied" in report["http_token_event_observer"]
    assert "Config" not in json.dumps(report) and "Env" not in json.dumps(report)


@pytest.mark.parametrize(
    "mutation",
    [
        "backend",
        "consumers",
        "proxy",
        "ambient-openai",
        "missing-runner",
        "network",
        "volume",
        "assets-rw",
        "uniform-image-substitution",
        "fixture-image-substitution",
        "command",
        "entrypoint",
        "runner-identity",
        "binding-source",
        "user",
        "workdir",
    ],
)
def test_effective_custody_mutants_fail(base, mutation):
    config = rendered(base)
    pins = lane.pin_images(config, image_witnesses(config))
    bound = binding(pins)
    containers, networks, volumes = inspections(config, pins)
    worker = next(
        c
        for c in containers
        if c["Config"]["Labels"]["com.docker.compose.service"] == "worker"
    )
    if mutation == "backend":
        worker["Config"]["Env"].remove("BIFROST_WORK_DELIVERY_BACKEND=postgres")
        worker["Config"]["Env"].append("BIFROST_WORK_DELIVERY_BACKEND=rabbitmq")
    elif mutation == "consumers":
        worker["Config"]["Env"].remove(
            "BIFROST_WORKER_CONSUMERS=workflow,agent-run,summarize"
        )
    elif mutation == "proxy":
        worker["Config"]["Env"].append("HTTPS_PROXY=http://unapproved")
    elif mutation == "ambient-openai":
        worker["Config"]["Env"].remove("OPENAI_CUSTOM_HEADERS=")
        worker["Config"]["Env"].append(
            'OPENAI_CUSTOM_HEADERS={"organization":"unapproved"}'
        )
    elif mutation == "missing-runner":
        containers.pop()
    elif mutation == "network":
        networks[0]["Internal"] = False
    elif mutation == "volume":
        volumes[-1]["Labels"] = {}
    elif mutation == "uniform-image-substitution":
        for container in containers:
            container["Image"] = "sha256:" + "3" * 64
    elif mutation == "fixture-image-substitution":
        containers[-2]["Image"] = "sha256:" + "3" * 64
    elif mutation == "command":
        containers[-1]["Config"]["Cmd"] = ["pytest", lane.CASE, "-v"]
    elif mutation == "entrypoint":
        containers[-1]["Config"]["Entrypoint"] = ["sh", "-c"]
    elif mutation == "runner-identity":
        containers[-1]["Id"] = "d" * 64
    elif mutation == "binding-source":
        bound["source"] = "d" * 40
    elif mutation == "user":
        containers[-1]["Config"]["User"] = "root"
    elif mutation == "workdir":
        containers[-1]["Config"]["WorkingDir"] = "/unapproved"
    else:
        next(
            m
            for m in containers[-1]["Mounts"]
            if m["Destination"] == "/app/reference-assets"
        )["RW"] = True
    with pytest.raises(ValueError):
        lane.verify(
            config,
            containers,
            networks,
            volumes,
            PROJECT,
            pins,
            bound,
            RUNNER_ID,
            source=SOURCE,
        )


def test_prebuild_witness_requires_every_actual_image(base):
    config = rendered(base)
    images = image_witnesses(config)
    with pytest.raises(ValueError):
        lane.pin_images(config, images[:-1])
    images[0]["RepoTags"] = ["different-image:latest"]
    with pytest.raises(ValueError):
        lane.pin_images(config, images)


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "missing-phase",
        "wrong-source",
        "raw-env",
        "nonzero-exit",
        "cleanup-unverified",
    ],
)
def test_only_finite_source_matched_success_evidence_can_be_published(base, mutation):
    config = rendered(base)
    pins = lane.pin_images(config, image_witnesses(config))
    report = lane.verify(
        config,
        *inspections(config, pins),
        PROJECT,
        pins,
        binding(pins),
        RUNNER_ID,
        source=SOURCE,
    )
    records = []
    for invocation in ("fixture-units", "capacity-reference"):
        for phase in ("before_pytest", "after_pytest"):
            record = {
                **report,
                "source": SOURCE,
                "invocation": invocation,
                "phase": phase,
            }
            if phase == "after_pytest":
                record["runner_exit_code"] = 0
            records.append(record)
    source = {
        "source": SOURCE,
        "project": PROJECT,
        "prebuild_image_ids": {name: pin["id"] for name, pin in pins.items()},
    }
    cleanup = {
        "source": SOURCE,
        "project": PROJECT,
        "verified_empty_resources": True,
        "domain_settlement": "case-owned; not established by infrastructure teardown",
    }
    if mutation == "missing-phase":
        records.pop()
    elif mutation == "wrong-source":
        records[0]["source"] = "d" * 40
    elif mutation == "raw-env":
        records[0]["Config.Env"] = ["PRIVATE_KEY=unapproved"]
    elif mutation == "nonzero-exit":
        records[-1]["runner_exit_code"] = 7
    elif mutation == "cleanup-unverified":
        cleanup["verified_empty_resources"] = False
    if mutation:
        with pytest.raises(ValueError):
            lane.validate_evidence(source, records, cleanup, SOURCE, PROJECT)
    else:
        lane.validate_evidence(source, records, cleanup, SOURCE, PROJECT)


def test_publication_path_is_unmounted_and_writes_are_exclusive():
    coordinator = (SCRIPTS / "agent-reference-lane.sh").read_text()
    renderer = (SCRIPTS / "render-agent-reference-compose.py").read_text()
    assert (
        'lane_evidence_dir="/tmp/bifrost-agent-reference-evidence-${COMPOSE_PROJECT_NAME}"'
        in coordinator
    )
    assert 'mkdir -m 700 "$lane_evidence_dir"' in coordinator
    assert "os.O_EXCL | os.O_NOFOLLOW" in renderer
    assert (
        "lane_evidence_dir"
        not in coordinator.split("docker compose --env-file", 1)[1].split(
            "lane_started=1", 1
        )[0]
    )


@pytest.mark.parametrize("private", [False, True])
def test_caller_cannot_override_reference_namespace_or_private_environment(
    tmp_path, private
):
    command = [str(SCRIPTS / "agent-reference-lane.sh")]
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "BIFROST_PROJECT_PREFIX": "bifrost-test",
    }
    if private:
        command.append("--run-owned")
        env["BIFROST_AGENT_REFERENCE_CLEAN"] = "1"
    result = subprocess.run(
        command, env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 2
    assert (
        "refuses ambient" in result.stderr
        if not private
        else "not sanitized" in result.stderr
    )


@pytest.mark.parametrize(
    "mutation", [None, "receipt-missing", "receipt-mismatch", "exit-mismatch"]
)
def test_actual_runner_post_exit_requires_exact_release_and_wait_code(base, mutation):
    config = rendered(base)
    pins = lane.pin_images(config, image_witnesses(config))
    bound = binding(pins)
    containers, networks, volumes = inspections(config, pins)
    containers[-1]["State"] = {"Running": False, "Status": "exited", "ExitCode": 7}
    receipt = {**bound, "container_id": RUNNER_ID}
    if mutation == "receipt-missing":
        receipt = None
    elif mutation == "receipt-mismatch":
        receipt["nonce"] = "d" * 32
    elif mutation == "exit-mismatch":
        containers[-1]["State"]["ExitCode"] = 0
    if mutation:
        with pytest.raises(ValueError):
            lane.verify(
                config,
                containers,
                networks,
                volumes,
                PROJECT,
                pins,
                bound,
                RUNNER_ID,
                receipt,
                7,
                source=SOURCE,
            )
    else:
        assert lane.verify(
            config,
            containers,
            networks,
            volumes,
            PROJECT,
            pins,
            bound,
            RUNNER_ID,
            receipt,
            7,
            source=SOURCE,
        )["startup_commands_verified"]


@pytest.mark.parametrize("invocation", ["nominal", "units"])
def test_runner_execs_closed_argv_only_after_exact_host_receipt(
    base, tmp_path, monkeypatch, invocation
):
    pins = lane.pin_images(rendered(base), image_witnesses(rendered(base)))
    bound = binding(pins)
    if invocation == "units":
        assert runner.UNITS == lane.UNITS
        bound["argv"] = ["pytest", *lane.UNITS, "-v", *runner.SUFFIX]
    (tmp_path / "binding.json").write_text(json.dumps(bound))
    (tmp_path / "release.json").write_text(
        json.dumps({**bound, "container_id": RUNNER_ID})
    )
    monkeypatch.setattr(runner, "CUSTODY", tmp_path)
    monkeypatch.setattr(runner.sys, "argv", ["agent_reference_runner.py"])
    monkeypatch.setattr(
        runner.os, "uname", lambda: type("Uname", (), {"nodename": RUNNER_ID[:12]})()
    )
    executed = []
    monkeypatch.setattr(
        runner.os, "execvp", lambda program, args: executed.append((program, args))
    )
    runner.main()
    assert executed == [("pytest", bound["argv"])]


@pytest.mark.parametrize(
    "mutation", ["legacy", "extra", "reordered", "missing", "duplicate"]
)
def test_unit_phase_accepts_only_same_complete_closed_selector(mutation):
    bound = binding({"test-runner": {"id": "sha256:" + "1" * 64}})
    assert runner.UNITS == lane.UNITS
    selected = list(lane.UNITS)
    if mutation == "legacy":
        selected = selected[:2]
    elif mutation == "extra":
        selected.append("tests/unit/other.py")
    elif mutation == "reordered":
        selected.reverse()
    elif mutation == "missing":
        selected.pop()
    else:
        selected.append(selected[0])
    bound["argv"] = ["pytest", *selected, "-v", *runner.SUFFIX]
    with pytest.raises(ValueError, match="invalid binding"):
        runner.validate_binding(bound)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-release",
        "wrong-nonce",
        "wrong-container",
        "wrong-image",
        "broad-argv",
        "stopped-oracle",
        "extra-argv",
    ],
)
def test_runner_never_execs_on_missing_or_unbound_release(
    base, tmp_path, monkeypatch, mutation
):
    pins = lane.pin_images(rendered(base), image_witnesses(rendered(base)))
    bound = binding(pins)
    receipt = {**bound, "container_id": RUNNER_ID}
    if mutation == "wrong-nonce":
        receipt["nonce"] = "d" * 32
    elif mutation == "wrong-container":
        receipt["container_id"] = "d" * 64
    elif mutation == "wrong-image":
        receipt["image_id"] = "sha256:" + "d" * 64
    elif mutation in {"broad-argv", "stopped-oracle"}:
        bound["argv"] = [
            "pytest",
            "tests/"
            if mutation == "broad-argv"
            else "tests/e2e/platform/stopped_reference.py",
            "-v",
            *runner.SUFFIX,
        ]
        receipt["argv"] = bound["argv"]
    (tmp_path / "binding.json").write_text(json.dumps(bound))
    if mutation != "missing-release":
        (tmp_path / "release.json").write_text(json.dumps(receipt))
    clock = iter([0, runner.WAIT_SECONDS + 1])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(runner, "CUSTODY", tmp_path)
    monkeypatch.setattr(
        runner.sys,
        "argv",
        ["agent_reference_runner.py", "override"]
        if mutation == "extra-argv"
        else ["agent_reference_runner.py"],
    )
    monkeypatch.setattr(
        runner.os, "uname", lambda: type("Uname", (), {"nodename": RUNNER_ID[:12]})()
    )
    executed = []
    monkeypatch.setattr(runner.os, "execvp", lambda *args: executed.append(args))
    with pytest.raises(SystemExit, match="pytest did not start"):
        runner.main()
    assert executed == []


@pytest.mark.parametrize(
    "kind", ["stopped-container", "network", "volume", "query-failure"]
)
def test_preexisting_project_or_failed_query_never_mutates(tmp_path, kind):
    root = tmp_path / "worktree"
    scripts = root / "scripts"
    (scripts / "lib").mkdir(parents=True)
    (scripts / "agent-reference-lane.sh").write_text(
        (SCRIPTS / "agent-reference-lane.sh").read_text()
    )
    (scripts / "agent-reference-lane.sh").chmod(0o755)
    (scripts / "lib/test_helpers.sh").write_text(
        (SCRIPTS / "lib/test_helpers.sh").read_text()
    )
    for path in (
        f"api/{lane.CASE}",
        *(f"api/{p}" for p in lane.UNITS),
        "test-fixtures/agent-reference/provenance.json",
        "api/scripts/agent_reference_runner.py",
        "api/scripts/agent_reference_fixture.py",
        "api/tests/e2e/platform/agent_reference_observer.py",
        "api/tests/e2e/platform/agent_reference_server.py",
    ):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git = bin_dir / "git"
    git.write_text(
        f'#!/bin/sh\ncase "$*" in *--git-path*) echo "{root}/.locks";; *--show-toplevel*) echo "{root}";; *HEAD*) echo "{SOURCE}";; esac\n'
    )
    git.chmod(0o755)
    calls = tmp_path / "docker-calls"
    docker = bin_dir / "docker"
    docker.write_text(f"""#!/usr/bin/env python3
import json
import sys
from pathlib import Path
with Path({str(calls)!r}).open('a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')
kind = {kind!r}
if kind == 'query-failure': sys.exit(1)
if kind == 'stopped-container' and sys.argv[1] == 'ps': print('stopped-owned-id')
if kind == 'network' and sys.argv[1:3] == ['network', 'ls']: print('owned-network')
if kind == 'volume' and sys.argv[1:3] == ['volume', 'ls']: print('owned-volume')
""")
    docker.chmod(0o755)
    result = subprocess.run(
        [str(scripts / "agent-reference-lane.sh")],
        env={"PATH": f"{bin_dir}:/usr/local/bin:/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert b"required agent-reference input missing" not in result.stderr
    assert calls.exists()
    for call in calls.read_text().splitlines():
        arguments = json.loads(call)
        assert arguments[0] == "ps" or arguments[:2] in [
            ["network", "ls"],
            ["volume", "ls"],
        ]
        expected_hash = hashlib.sha256(str(root).encode()).hexdigest()[:8]
        assert (
            arguments[-1]
            == f"label=com.docker.compose.project=bifrost-agent-reference-{expected_hash}"
        )
        assert (
            arguments[-1]
            != f"label=com.docker.compose.project=bifrost-test-{expected_hash}"
        )


def test_private_case_cannot_join_default_collection_and_has_explicit_job():
    api = Path(__file__).resolve().parents[2]
    parser = configparser.ConfigParser()
    parser.read(api / "pytest.ini")
    patterns = parser["pytest"]["python_files"].split()
    assert not any(
        fnmatch.fnmatch(Path(lane.CASE).name, pattern) for pattern in patterns
    )
    repo = api.parent
    workflow = (
        api / ".github/workflows/agent-reference.yml"
        if (api / ".github").exists()
        else repo / ".github/workflows/agent-reference.yml"
    ).read_text()
    assert (
        "workflow_dispatch:" in workflow
        and "run: ./test.sh agent-reference" in workflow
    )
    assert "pull_request:" in workflow and "push:" not in workflow
    assert "api/tests/e2e/platform/agent_reference_cases.py" in workflow
    assert "if: always()" in workflow and "if-no-files-found: error" in workflow


def inner_context(tmp_path, monkeypatch):
    (tmp_path / "owner.json").write_text(
        json.dumps({"root": str(ROOT), "project": PROJECT, "source": SOURCE})
    )
    tmp_path.chmod(0o700)
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", PROJECT)
    monkeypatch.setenv("COMPOSE_FILE", str(tmp_path / "compose.json"))
    monkeypatch.setenv("BIFROST_SKIP_BUILD", "1")
    monkeypatch.setenv("BIFROST_PROJECT_PREFIX", "bifrost-agent-reference")
    monkeypatch.setattr(
        lane.subprocess,
        "check_output",
        lambda args, **kwargs: (
            SOURCE + "\n"
            if args[-1] == "HEAD"
            else str(ROOT) + "\n"
            if args[-1] == "--show-toplevel"
            else ""
        ),
    )


def test_inner_command_allowlist_rejects_broad_pytest(tmp_path, monkeypatch):
    inner_context(tmp_path, monkeypatch)
    for args in (
        ["all"],
        ["e2e"],
        [lane.CASE, "--no-reset"],
        [lane.CASE, "-v", "--reruns", "1"],
    ):
        with pytest.raises(ValueError):
            lane.check_inner(tmp_path, ROOT, args)
    lane.check_inner(tmp_path, ROOT, [lane.CASE, "-v"])


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-prefix",
        "ordinary-prefix",
        "ordinary-project",
        "wrong-hash",
        "wrong-root",
    ],
)
def test_inner_refuses_redirected_project_ownership(tmp_path, monkeypatch, mutation):
    inner_context(tmp_path, monkeypatch)
    if mutation == "missing-prefix":
        monkeypatch.delenv("BIFROST_PROJECT_PREFIX")
    elif mutation == "ordinary-prefix":
        monkeypatch.setenv("BIFROST_PROJECT_PREFIX", "bifrost-test")
    elif mutation in {"ordinary-project", "wrong-hash"}:
        wrong = (
            PROJECT.replace("bifrost-agent-reference", "bifrost-test")
            if mutation == "ordinary-project"
            else "bifrost-agent-reference-00000000"
        )
        monkeypatch.setenv("COMPOSE_PROJECT_NAME", wrong)
        owner = json.loads((tmp_path / "owner.json").read_text())
        owner["project"] = wrong
        (tmp_path / "owner.json").write_text(json.dumps(owner))
    else:
        monkeypatch.setattr(
            lane.subprocess, "check_output", lambda *a, **kw: "/different/root\n"
        )
    with pytest.raises(ValueError):
        lane.check_inner(tmp_path, ROOT, [lane.CASE, "-v"])


def test_reference_namespace_uses_shared_hash_and_preserves_default(tmp_path):
    helper = SCRIPTS / "lib/test_helpers.sh"
    command = f"source \"{helper}\"\ngit() {{ printf '%s\\n' \"$1\" >/dev/null; printf '%s\\n' {str(ROOT)!r}; }}\nunset BIFROST_PROJECT_PREFIX\ncompute_project_name .\nprintf '\\n'\nexport BIFROST_PROJECT_PREFIX=bifrost-agent-reference\ncompute_project_name .\n"
    result = subprocess.run(
        ["bash", "-c", command], capture_output=True, text=True, check=True
    )
    assert result.stdout.splitlines() == [
        PROJECT.replace("bifrost-agent-reference", "bifrost-test"),
        PROJECT,
    ]
    assert lane.project_name(ROOT) == PROJECT
    coordinator = (SCRIPTS / "agent-reference-lane.sh").read_text()
    assert (
        coordinator.index("exec env -i")
        < coordinator.index("export BIFROST_PROJECT_PREFIX=bifrost-agent-reference")
        < coordinator.index('COMPOSE_PROJECT_NAME="$(compute_project_name .)"')
    )


@pytest.mark.parametrize("original_status,expected_status", [(7, 7), (0, 1)])
def test_cleanup_failure_cannot_mask_runner_failure(
    tmp_path, original_status, expected_status
):
    source = (SCRIPTS / "agent-reference-lane.sh").read_text()
    body = source.split("lane_cleanup() {", 1)[1].split("\n}\ntrap lane_cleanup", 1)[0]
    context = tmp_path / "private-context"
    context.mkdir()
    stack = tmp_path / "test.sh"
    stack.write_text("#!/bin/sh\nexit 0\n")
    stack.chmod(0o755)
    script = f"""set -euo pipefail
lane_started=1
lane_context={str(context)!r}
lane_root={str(tmp_path)!r}
COMPOSE_FILE={str(context / "compose.json")!r}
LOG_DIR={str(tmp_path)!r}
COMPOSE_PROJECT_NAME={PROJECT!r}
lane_source={SOURCE!r}
docker() {{ return 1; }}
lane_require_empty() {{ return 0; }}
lane_cleanup() {{{body}
}}
trap lane_cleanup EXIT
exit {original_status}
"""
    result = subprocess.run(["bash", "-c", script], capture_output=True, check=False)
    assert result.returncode == expected_status
    assert context.exists()  # failed custody is retained, never called a pass


@pytest.mark.parametrize("lane_id", ["", "b" * 31, "b" * 33, "B" * 32, "g" * 32])
def test_renderer_rejects_invalid_lane_identity(base, lane_id):
    with pytest.raises(ValueError, match="invalid lane identity"):
        lane.render(base, ROOT, PROJECT, LOG, SOURCE, CONTEXT, lane_id)


def test_lane_identity_and_role_are_closed_per_service(base):
    services = rendered(base)["services"]
    for name, service in services.items():
        env = service["environment"]
        if name in {"api", "api-replica", "scheduler-fixtures", "test-runner"}:
            assert env["BIFROST_AGENT_REFERENCE_LANE_ID"] == LANE_ID
        else:
            assert "BIFROST_AGENT_REFERENCE_LANE_ID" not in env
        if name in {"api", "api-replica"}:
            assert env["BIFROST_AGENT_REFERENCE_ROLE"] == name
        else:
            assert "BIFROST_AGENT_REFERENCE_ROLE" not in env


def test_renderer_caps_private_mounts_and_preserves_service_identity(base):
    config = rendered(base)
    for name, service in config["services"].items():
        mounts = service["volumes"]
        private = {
            m["target"]: m
            for m in mounts
            if m["target"].startswith("/run/agent-reference/")
        }
        filenames = set()
        if name in {"api", "api-replica", "scheduler-fixtures"}:
            filenames.add("observer-ingest-key")
        if name in {"scheduler-fixtures", "test-runner"}:
            filenames.add("observer-control-key")
        if name in {"api", "api-replica", "scheduler-fixtures", "test-runner"}:
            filenames.add("observer-ca.pem")
        if name == "scheduler-fixtures":
            filenames.update({"observer-server.pem", "observer-server-key.pem"})
        assert set(private) == {f"/run/agent-reference/{f}" for f in filenames}
        for target, mount in private.items():
            assert mount["source"] == str(CONTEXT / target.rsplit("/", 1)[1])
            assert mount["read_only"] is True
            assert mount["bind"] == {"create_host_path": False}
        model_input = [
            m for m in mounts if m["target"] == "/app/reference-model-oracle/input.json"
        ]
        assert len(model_input) == (
            1 if name in {"scheduler-fixtures", "test-runner"} else 0
        )
        if model_input:
            assert model_input[0] == {
                "type": "bind",
                "source": str(CONTEXT / "model-oracle-input.json"),
                "target": "/app/reference-model-oracle/input.json",
                "read_only": True,
                "bind": {"create_host_path": False},
            }
        if name in {"api", "api-replica"}:
            assert service["command"] == lane.API_COMMAND
            assert "user" not in service and "entrypoint" not in service
            for target in ("/app/scripts", "/app/tests"):
                assert (
                    next(m for m in mounts if m["target"] == target)["read_only"]
                    is True
                )
        if name == "scheduler-fixtures":
            assert service["command"] == lane.FIXTURE_COMMAND
            assert "user" not in service and "entrypoint" not in service


def test_renderer_retains_actual_configured_users_and_entrypoints(base):
    for name in ("api", "api-replica", "scheduler-fixtures"):
        base["services"][name].update(user="root", entrypoint=["/entrypoint.sh"])
    config = rendered(base)
    for name in ("api", "api-replica", "scheduler-fixtures"):
        assert config["services"][name]["user"] == "root"
        assert config["services"][name]["entrypoint"] == ["/entrypoint.sh"]


@pytest.mark.parametrize(
    "service,slot",
    [
        ("worker", "BIFROST_AGENT_REFERENCE_LANE_ID"),
        ("scheduler", "BIFROST_AGENT_REFERENCE_ROLE"),
        ("scheduler-fixtures", "BIFROST_AGENT_REFERENCE_ROLE"),
        ("test-runner", "BIFROST_AGENT_REFERENCE_ROLE"),
    ],
)
def test_actual_excluded_service_cannot_gain_a_runtime_construction_slot(
    base, service, slot
):
    config = rendered(base)
    pins = lane.pin_images(config, image_witnesses(config))
    containers, networks, volumes = inspections(config, pins)
    selected = next(
        c
        for c in containers
        if c["Config"]["Labels"]["com.docker.compose.service"] == service
    )
    selected["Config"]["Env"].append(f"{slot}=")
    with pytest.raises(
        ValueError, match="unexpected runtime construction identity slot"
    ):
        lane.verify(
            config,
            containers,
            networks,
            volumes,
            PROJECT,
            pins,
            binding(pins),
            RUNNER_ID,
            source=SOURCE,
        )


@pytest.mark.parametrize("subpath", ["", "api-replica", "../api", "/api"])
def test_observer_actual_mount_subpath_drift_is_rejected(base, subpath):
    config = rendered(base)
    pins = lane.pin_images(config, image_witnesses(config))
    containers, networks, volumes = inspections(config, pins)
    api = next(
        c
        for c in containers
        if c["Config"]["Labels"]["com.docker.compose.service"] == "api"
    )
    api["HostConfig"]["Mounts"][0]["VolumeOptions"]["Subpath"] = subpath
    with pytest.raises(ValueError, match="observer volume subpath differs"):
        lane.verify(
            config,
            containers,
            networks,
            volumes,
            PROJECT,
            pins,
            binding(pins),
            RUNNER_ID,
            source=SOURCE,
        )
