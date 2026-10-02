#!/usr/bin/env python3
"""Closed Compose derivative and independent Docker custody checks for C1-R.

Only stdlib is used. This does not observe HTTP credentials, events or domain work.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
from pathlib import Path

SERVICES = (
    "postgres",
    "pgbouncer",
    "rabbitmq",
    "redis",
    "seaweedfs",
    "init",
    "api",
    "api-replica",
    "worker",
    "scheduler",
    "scheduler-fixtures",
    "test-runner",
)
PROCESSES = ("init", "api", "api-replica", "worker", "scheduler", "test-runner")
CONSUMERS = "workflow,agent-run,summarize"
PROJECT_PREFIX = "bifrost-agent-reference"
RUNNER_COMMAND = ["python", "/app/scripts/agent_reference_runner.py"]
API_COMMAND = [
    "coverage",
    "run",
    "--parallel-mode",
    "-m",
    "tests.e2e.platform.agent_reference_server",
    "--host",
    "0.0.0.0",
    "--port",
    "8000",
]
FIXTURE_COMMAND = ["python", "-m", "scripts.agent_reference_fixture"]
CASE = "tests/e2e/platform/agent_reference_cases.py"
UNITS = (
    "tests/unit/test_agent_reference_fixture.py",
    "tests/unit/test_agent_reference_lane.py",
    "tests/unit/test_agent_reference_contract.py",
    "tests/unit/test_agent_reference_model_contract.py",
    "tests/unit/test_agent_reference_observer.py",
    "tests/unit/test_agent_reference_server.py",
)
OPTIONAL = {
    "GITHUB_TEST_PAT",
    "GITHUB_TEST_REPO",
    "ANTHROPIC_API_TEST_KEY",
    "OPENAPI_API_TEST_KEY",
    "GENERIC_AI_TEST_KEY",
    "GENERIC_AI_BASE_URL",
    "EMBEDDINGS_AI_TEST_KEY",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
    "OPENAI_CUSTOM_HEADERS",
}
COMMON_ENV = {
    "BIFROST_DATABASE_URL",
    "BIFROST_DATABASE_URL_SYNC",
    "BIFROST_REDIS_URL",
    "BIFROST_RABBITMQ_URL",
    "BIFROST_SECRET_KEY",
    "BIFROST_ENVIRONMENT",
    "BIFROST_S3_BUCKET",
    "BIFROST_S3_ENDPOINT_URL",
    "BIFROST_S3_ACCESS_KEY",
    "BIFROST_S3_SECRET_KEY",
    "BIFROST_S3_REGION",
    "COVERAGE_FILE",
    "BIFROST_ALLOW_REGISTRATION",
    "BIFROST_PUBLIC_URL",
    "BIFROST_ACCESS_TOKEN_EXPIRE_MINUTES",
    "EMBEDDING_ALLOWED_HOSTS",
    "BIFROST_MAX_CONCURRENCY",
    "BIFROST_DATABASE_POOL_SIZE",
    "BIFROST_DATABASE_MAX_OVERFLOW",
    "PIP_NO_CACHE_DIR",
    "HOME",
    "BIFROST_DEFERRED_EXECUTION_PROMOTER_INTERVAL_SECONDS",
    "TEST_API_URL",
    "TEST_API_REPLICA_URL",
    "PYTHONPATH",
    "BIFROST_POSTURE_HARDENED",
}
INFRA_ENV = {
    "postgres": {
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_INITDB_ARGS",
    },
    "pgbouncer": {
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
        "POOL_MODE",
        "MAX_CLIENT_CONN",
        "DEFAULT_POOL_SIZE",
        "MIN_POOL_SIZE",
        "RESERVE_POOL_SIZE",
        "AUTH_TYPE",
    },
    "rabbitmq": {"RABBITMQ_DEFAULT_USER", "RABBITMQ_DEFAULT_PASS"},
    "redis": set(),
    "seaweedfs": {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "S3_BUCKET"},
    "scheduler-fixtures": set(),
}
BIND_PATHS = {
    "/app/src": "api/src",
    "/app/shared": "api/shared",
    "/app/bifrost": "api/bifrost",
    "/app/tests": "api/tests",
    "/app/scripts": "api/scripts",
    "/repo/scripts": "scripts",
    "/app/alembic": "api/alembic",
    "/app/alembic.ini": "api/alembic.ini",
    "/app/.coveragerc": "api/.coveragerc",
    "/app/pytest.ini": "api/pytest.ini",
    "/app/pyproject.toml": "api/pyproject.toml",
    "/app/pyrightconfig.json": "api/pyrightconfig.json",
    "/app/pyrightconfig.docker.json": "api/pyrightconfig.docker.json",
    "/app/test.sh": "test.sh",
    "/app/docker-compose.test.yml": "docker-compose.test.yml",
    "/app/.github/workflows": ".github/workflows",
}
STRIP_BINDS = {
    "/app/doc_renderer_service": "doc_renderer_service",
    "/app/fuzz": "api/fuzz",
    "/app/benchmarks": "api/benchmarks",
    "/app/docs/architecture/execution-operations.md": "docs/architecture/execution-operations.md",
    "/app/api/Dockerfile.dev": "api/Dockerfile.dev",
    "/app/api/Dockerfile": "api/Dockerfile",
    "/app/docker-compose.yml": "docker-compose.yml",
    "/app/docker-compose.dev.yml": "docker-compose.dev.yml",
    "/app/.github": ".github",
    "/app/.snyk": ".snyk",
    "/app/.claude": ".claude",
    "/app/.env.debug": ".env.debug",
    "/app/debug.sh": "debug.sh",
    "/app/docker-compose.debug.yml": "docker-compose.debug.yml",
    "/app/docker-compose.debug.port.yml": "docker-compose.debug.port.yml",
    "/app/client/playwright.config.ts": "client/playwright.config.ts",
    "/app/client/e2e": "client/e2e",
    "/app/client/nginx.conf": "client/nginx.conf",
    "/app/client/Dockerfile": "client/Dockerfile",
    "/client/src": "client/src",
    "/k8s": "k8s",
    "/deploy": "deploy",
    "/scripts/kubernetes": "scripts/kubernetes",
    "/.claude/skills": ".claude/skills",
}
MASKS = {
    "/app/src/services/app_compiler/node_modules",
    "/app/src/services/app_bundler/node_modules",
    "/app/src/services/sdk_package/node_modules",
    "/app/src/services/sdk_package/sdk_src",
}
FORBIDDEN = {
    "env_file",
    "secrets",
    "configs",
    "ports",
    "network_mode",
    "privileged",
    "devices",
    "extra_hosts",
    "cap_add",
    "pid",
    "ipc",
    "uts",
    "container_name",
    "volumes_from",
    "external_links",
    "links",
    "develop",
    "provider",
    "post_start",
    "pre_stop",
}
SERVICE_FIELDS = {
    "image",
    "build",
    "environment",
    "volumes",
    "depends_on",
    "healthcheck",
    "command",
    "user",
    "security_opt",
    "cap_drop",
    "profiles",
    "mem_limit",
    "working_dir",
    "tmpfs",
    "networks",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(
            message
        )  # Static descriptions only; never print customer input.


def project_name(root: Path) -> str:
    return f"{PROJECT_PREFIX}-{hashlib.sha256(str(root).encode()).hexdigest()[:8]}"


def render(
    base: dict,
    root: Path,
    project: str,
    log: Path,
    source: str,
    context: Path,
    lane_id: str,
) -> dict:
    require(
        root.is_absolute() and project == project_name(root),
        "unexpected project",
    )
    require(
        root.is_absolute() and log == Path("/tmp") / f"bifrost-{project}",
        "unexpected ownership paths",
    )
    require(re.fullmatch(r"[a-f0-9]{40}", source) is not None, "invalid source commit")
    require(re.fullmatch(r"[a-f0-9]{32}", lane_id) is not None, "invalid lane identity")
    require(
        set(base) <= {"name", "services", "networks", "volumes"},
        "unexpected Compose root field",
    )
    require(set(base.get("networks", {})) == {"default"}, "unexpected base network")
    require(
        set(base["networks"]["default"]) <= {"name"},
        "base network configuration changed",
    )
    base_volumes = set(base.get("volumes", {}))
    require(
        {"test-mounts", "test-coverage"}
        <= base_volumes
        <= {"test-mounts", "test-coverage", "playwright-results"},
        "base volumes changed",
    )
    require(
        all(set(v) <= {"name"} for v in base["volumes"].values()),
        "unexpected volume custody",
    )
    require(set(SERVICES) <= set(base["services"]), "required service absent")
    result = {
        "name": project,
        "services": {},
        "networks": {"default": {"name": f"{project}_default", "internal": True}},
        "volumes": {
            name: {"name": f"{project}_{name}"}
            for name in ("test-mounts", "test-coverage", "observer-status")
        },
    }
    for name in SERVICES:
        service = copy.deepcopy(base["services"][name])
        require(not (set(service) & FORBIDDEN), "unsafe service property")
        require(set(service) <= SERVICE_FIELDS, "unreviewed service property")
        if name in PROCESSES or name == "scheduler-fixtures":
            require(
                service["image"] == "bifrost-test-api-dev:latest"
                and service.get("build")
                == {"context": str(root), "dockerfile": "api/Dockerfile.dev"},
                "unexpected image/build custody",
            )
            service["image"] = f"bifrost-agent-reference-{project}:{source}"
        require(
            set(service.get("networks", {"default": None})) == {"default"},
            "foreign service network",
        )
        require(not service.get("labels"), "unexpected service labels")
        require(
            set(service.get("depends_on", {})) <= set(SERVICES),
            "foreign service dependency",
        )
        env = service.get("environment", {})
        require(isinstance(env, dict), "unresolved service environment")
        require(
            set(env) <= COMMON_ENV | OPTIONAL
            if name in PROCESSES
            else set(env) <= INFRA_ENV[name],
            "unexpected environment slot",
        )
        require(
            all(v is not None and "${" not in str(v) for v in env.values()),
            "unresolved environment value",
        )
        env = {key: str(value) for key, value in env.items()}
        # Fixed synthetic custody, independent of optional host interpolation.
        fixed = {
            "BIFROST_SECRET_KEY": "test-secret-key-for-e2e-testing-must-be-32-chars",
            "BIFROST_ENVIRONMENT": "testing",
            "BIFROST_S3_ACCESS_KEY": "bifrost",
            "BIFROST_S3_SECRET_KEY": "bifrost_test_key",
            "AWS_ACCESS_KEY_ID": "bifrost",
            "AWS_SECRET_ACCESS_KEY": "bifrost_test_key",
            "POSTGRES_PASSWORD": "bifrost_test",
            "DB_PASSWORD": "bifrost_test",
            "RABBITMQ_DEFAULT_PASS": "bifrost_test",
            "BIFROST_PUBLIC_URL": "http://api:8000",
            "BIFROST_MAX_CONCURRENCY": "1",
        }
        for key in env.keys() & fixed.keys():
            env[key] = fixed[key]
        for key in env.keys() & OPTIONAL:
            env[key] = ""
        if name in PROCESSES:
            env["BIFROST_WORK_DELIVERY_BACKEND"] = "postgres"
            for key in OPTIONAL:
                env[key] = ""
        if name == "worker":
            env.update(
                BIFROST_WORKER_CONSUMERS=CONSUMERS,
                BIFROST_MAX_WORKERS="1",
                BIFROST_MAX_CONCURRENCY="1",
            )
        if name == "test-runner":
            env["BIFROST_AGENT_REFERENCE_ASSETS_DIR"] = "/app/reference-assets"
        if name in {"api", "api-replica", "scheduler-fixtures", "test-runner"}:
            env["BIFROST_AGENT_REFERENCE_LANE_ID"] = lane_id
        if name in {"api", "api-replica"}:
            env["BIFROST_AGENT_REFERENCE_ROLE"] = name
        service["environment"] = env
        mounts = []
        for mount in service.get("volumes", []):
            require(isinstance(mount, dict), "unresolved mount")
            target, kind = mount.get("target"), mount.get("type")
            if kind == "bind":
                expected = BIND_PATHS.get(target) or STRIP_BINDS.get(target)
                if expected:
                    require(
                        Path(mount.get("source", "")) == root / expected,
                        "unexpected bind source",
                    )
                    if target in STRIP_BINDS:
                        continue
                    mount["read_only"] = True
                else:
                    expected_source = (
                        log / "solution-repo-fixtures"
                        if target == "/tmp/bifrost/solution-repo-fixtures"
                        else log
                    )
                    require(
                        target
                        in {
                            "/tmp/bifrost",
                            "/bifrost-results",
                            "/tmp/bifrost/solution-repo-fixtures",
                        }
                        and Path(mount.get("source", "")) == expected_source,
                        "unexpected host mount",
                    )
            else:
                require(
                    kind == "volume"
                    and (
                        target in MASKS
                        and not mount.get("source")
                        or (target, mount.get("source"))
                        in {("/mounts", "test-mounts"), ("/coverage", "test-coverage")}
                    ),
                    "unexpected volume mount",
                )
            mounts.append(mount)
        if name == "test-runner":
            mounts.append(
                {
                    "type": "bind",
                    "source": str(root / "test-fixtures/agent-reference"),
                    "target": "/app/reference-assets",
                    "read_only": True,
                }
            )
            require(context.is_absolute(), "invalid custody context")
            mounts.append(
                {
                    "type": "bind",
                    "source": str(context / "custody"),
                    "target": "/app/reference-custody",
                    "read_only": True,
                }
            )
        if name in {"api", "api-replica"}:
            for target in ("/app/scripts", "/app/tests"):
                if not any(m["target"] == target for m in mounts):
                    mounts.append(
                        {
                            "type": "bind",
                            "source": str(root / BIND_PATHS[target]),
                            "target": target,
                            "read_only": True,
                            "bind": {"create_host_path": False},
                        }
                    )
            service["command"] = API_COMMAND.copy()
        if name == "scheduler-fixtures":
            service["command"] = FIXTURE_COMMAND.copy()
        if name in {"api", "api-replica", "test-runner"}:
            status = {
                "type": "volume",
                "source": "observer-status",
                "target": "/app/reference-observer-status",
                "read_only": name == "test-runner",
                "volume": {"nocopy": True},
            }
            if name != "test-runner":
                status["target"] += f"/{name}"
                status["volume"]["subpath"] = name
            mounts.append(status)
        if name == "test-runner":
            mounts.append(
                {
                    "type": "bind",
                    "source": str(context / "host-status"),
                    "target": "/app/reference-host-status",
                    "read_only": True,
                    "bind": {"create_host_path": False},
                }
            )
        private_files = []
        if name in {"api", "api-replica", "scheduler-fixtures"}:
            private_files.append("observer-ingest-key")
        if name in {"scheduler-fixtures", "test-runner"}:
            private_files.append("observer-control-key")
        if name in {"api", "api-replica", "scheduler-fixtures", "test-runner"}:
            private_files.append("observer-ca.pem")
        if name == "scheduler-fixtures":
            private_files.extend(["observer-server.pem", "observer-server-key.pem"])
        if name in {"scheduler-fixtures", "test-runner"}:
            mounts.append(
                {
                    "type": "bind",
                    "source": str(context / "model-oracle-input.json"),
                    "target": "/app/reference-model-oracle/input.json",
                    "read_only": True,
                    "bind": {"create_host_path": False},
                }
            )
        for filename in private_files:
            mounts.append(
                {
                    "type": "bind",
                    "source": str(context / filename),
                    "target": f"/run/agent-reference/{filename}",
                    "read_only": True,
                    "bind": {"create_host_path": False},
                }
            )
        service["volumes"] = mounts
        service["networks"] = {"default": None}
        if name == "test-runner":
            service["entrypoint"] = []
            service["user"] = "1000:1000"
            service["command"] = RUNNER_COMMAND
        result["services"][name] = service
    return result


def check_inner(context: Path, root: Path, args: list[str]) -> None:
    record = json.loads((context / "owner.json").read_text())
    require(
        context.stat().st_uid == os.getuid()
        and context.stat().st_mode & 0o777 == 0o700,
        "invalid private context",
    )
    git_root = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"], text=True
    ).strip()
    require(
        git_root == str(root)
        and os.environ.get("BIFROST_PROJECT_PREFIX") == PROJECT_PREFIX
        and record["root"] == str(root)
        and record["project"] == project_name(Path(git_root))
        and os.environ.get("COMPOSE_PROJECT_NAME") == record["project"],
        "inner ownership mismatch",
    )
    source = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    require(
        source == record["source"]
        and not subprocess.check_output(
            ["git", "-C", str(root), "status", "--porcelain"], text=True
        ),
        "inner source changed",
    )
    require(
        os.environ.get("COMPOSE_FILE") == str(context / "compose.json")
        and os.environ.get("BIFROST_SKIP_BUILD") == "1",
        "invalid inner Compose/build mode",
    )
    require(
        args in [["stack", "up"], ["stack", "down"], [*UNITS, "-v"], [CASE, "-v"]],
        "unapproved inner command",
    )


def argv(value: list[str] | str | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return shlex.split(value)
    require(
        isinstance(value, list) and all(isinstance(v, str) for v in value),
        "invalid command",
    )
    return value


def pin_images(config: dict, images: list[dict]) -> dict:
    """Freeze actual local IDs/default commands before any project resources exist."""
    pins = {}
    tags = {service["image"] for service in config["services"].values()}
    require(len(images) == len(tags), "unexpected prebuild image witnesses")
    for service, planned in config["services"].items():
        matched = [
            image for image in images if planned["image"] in image.get("RepoTags", [])
        ]
        require(len(matched) == 1, "missing or ambiguous prebuild image witness")
        image = matched[0]
        require(
            re.fullmatch(r"sha256:[a-f0-9]{64}", image["Id"]) is not None,
            "invalid prebuild image ID",
        )
        pins[service] = {
            "id": image["Id"],
            "cmd": argv(image["Config"].get("Cmd")),
            "entrypoint": argv(image["Config"].get("Entrypoint")),
            "user": image["Config"].get("User") or "",
            "working_dir": image["Config"].get("WorkingDir") or "",
        }
        require(
            isinstance(pins[service]["user"], str)
            and isinstance(pins[service]["working_dir"], str),
            "invalid image execution defaults",
        )
    return pins


def bind_run(context: Path, root: Path, args: list[str]) -> None:
    check_inner(context, root, args)
    require(args in [[*UNITS, "-v"], [CASE, "-v"]], "unapproved pytest command")
    owner = json.loads((context / "owner.json").read_text())
    pins = json.loads((context / "images.json").read_text())
    custody = context / "custody"
    require(
        custody.is_dir()
        and not (custody / "binding.json").exists()
        and not (custody / "release.json").exists(),
        "stale runner custody",
    )
    binding = {
        "version": 1,
        "source": owner["source"],
        "project": owner["project"],
        "container_name": f"{owner['project']}-pytest-runner",
        "nonce": secrets.token_hex(16),
        "argv": [
            "pytest",
            *args,
            "--durations=25",
            "--junitxml=/tmp/bifrost/test-results.xml",
        ],
        "image_id": pins["test-runner"]["id"],
    }
    path = custody / "binding.json"
    path.write_text(json.dumps(binding, sort_keys=True) + "\n")
    path.chmod(0o444)


def release_run(context: Path, runner_id: str) -> None:
    """Called only after the host verifier has independently accepted custody."""
    require(re.fullmatch(r"[a-f0-9]{64}", runner_id) is not None, "invalid runner ID")
    custody = context / "custody"
    require(not (custody / "release.json").exists(), "release already exists")
    binding = json.loads((custody / "binding.json").read_text())
    receipt = {**binding, "container_id": runner_id}
    staging = custody / "release.pending"
    staging.write_text(json.dumps(receipt, sort_keys=True) + "\n")
    staging.chmod(0o444)
    staging.replace(custody / "release.json")


def validate_evidence(
    source_record: dict, records: list[dict], cleanup: dict, source: str, project: str
) -> None:
    require(
        re.fullmatch(r"[a-f0-9]{40}", source) is not None
        and re.fullmatch(r"bifrost-agent-reference-[a-f0-9]{8}", project) is not None,
        "invalid evidence identity",
    )
    require(
        set(source_record) == {"source", "project", "prebuild_image_ids"}
        and source_record["source"] == source
        and source_record["project"] == project,
        "source evidence mismatch",
    )
    ids = source_record["prebuild_image_ids"]
    require(
        set(ids) == set(SERVICES)
        and all(re.fullmatch(r"sha256:[a-f0-9]{64}", value) for value in ids.values()),
        "invalid evidence image witnesses",
    )
    require(len(records) == 4, "missing pre/post invocation evidence")
    fields = {
        "project",
        "internal_network",
        "processes",
        "worker_consumers",
        "optional_credentials_absent",
        "prebuild_image_ids_verified",
        "startup_commands_verified",
        "http_token_event_observer",
        "source",
        "phase",
        "invocation",
    }
    for record, (invocation, phase) in zip(
        records,
        [
            ("fixture-units", "before_pytest"),
            ("fixture-units", "after_pytest"),
            ("capacity-reference", "before_pytest"),
            ("capacity-reference", "after_pytest"),
        ],
        strict=True,
    ):
        require(
            set(record)
            == fields | ({"runner_exit_code"} if phase == "after_pytest" else set())
            and record["source"] == source
            and record["project"] == project
            and record["phase"] == phase
            and record["invocation"] == invocation
            and (phase != "after_pytest" or record["runner_exit_code"] == 0),
            "invalid invocation evidence",
        )
        require(
            all(
                record[key] is True
                for key in (
                    "internal_network",
                    "optional_credentials_absent",
                    "prebuild_image_ids_verified",
                    "startup_commands_verified",
                )
            )
            and record["worker_consumers"] == CONSUMERS
            and record["http_token_event_observer"]
            == "not supplied by this infrastructure lane",
            "unsafe custody evidence",
        )
        require(
            set(record["processes"]) == set(PROCESSES)
            and all(
                set(value) == {"backend", "image"}
                and value["backend"] == "postgres"
                and value["image"] == ids[service]
                for service, value in record["processes"].items()
            ),
            "invalid process evidence",
        )
    require(
        cleanup
        == {
            "source": source,
            "project": project,
            "verified_empty_resources": True,
            "domain_settlement": "case-owned; not established by infrastructure teardown",
        },
        "invalid cleanup evidence",
    )


def publish(context: Path, destination: Path, source: str, project: str) -> None:
    require(
        destination == Path(f"/tmp/bifrost-agent-reference-evidence-{project}")
        and not destination.is_symlink()
        and destination.stat().st_uid == os.getuid()
        and destination.stat().st_mode & 0o777 == 0o700
        and not list(destination.iterdir()),
        "invalid evidence destination custody",
    )
    inputs = ("source-evidence.json", "custody-records.json", "cleanup-evidence.json")
    values = [json.loads((context / name).read_text()) for name in inputs]
    validate_evidence(*values, source, project)
    for name, value in zip(
        (
            "agent-reference-source.json",
            "agent-reference-custody.json",
            "agent-reference-cleanup.json",
        ),
        values,
        strict=True,
    ):
        fd = os.open(
            destination / name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(fd, "w") as stream:
            stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def verify(
    config: dict,
    containers: list[dict],
    networks: list[dict],
    volumes: list[dict],
    project: str,
    pins: dict,
    binding: dict,
    runner_id: str,
    receipt: dict | None = None,
    exit_code: int | None = None,
    source: str = "",
) -> dict:
    require(
        set(binding)
        == {
            "version",
            "source",
            "project",
            "container_name",
            "nonce",
            "argv",
            "image_id",
        }
        and binding["version"] == 1
        and binding["source"] == source
        and re.fullmatch(r"[a-f0-9]{40}", binding["source"]) is not None
        and re.fullmatch(r"[a-f0-9]{32}", binding["nonce"]) is not None
        and binding["argv"]
        in [
            [
                "pytest",
                *UNITS,
                "-v",
                "--durations=25",
                "--junitxml=/tmp/bifrost/test-results.xml",
            ],
            [
                "pytest",
                CASE,
                "-v",
                "--durations=25",
                "--junitxml=/tmp/bifrost/test-results.xml",
            ],
        ],
        "unapproved runner binding",
    )
    require(set(pins) == set(SERVICES), "missing prebuild service image witnesses")
    require(re.fullmatch(r"[a-f0-9]{64}", runner_id) is not None, "invalid runner ID")
    require(
        binding["project"] == project
        and binding["container_name"] == f"{project}-pytest-runner"
        and binding["image_id"] == pins["test-runner"]["id"],
        "runner binding mismatch",
    )
    require(
        len(networks) == 1
        and networks[0]["Name"] == f"{project}_default"
        and networks[0].get("Internal") is True
        and networks[0].get("Labels", {}).get("com.docker.compose.project") == project,
        "effective network is not owned/internal",
    )
    require(
        {v["Name"] for v in volumes} == {v["name"] for v in config["volumes"].values()}
        and all(
            v.get("Labels", {}).get("com.docker.compose.project") == project
            for v in volumes
        ),
        "effective named volume custody differs",
    )
    observed = {}
    services_seen = set()
    for container in containers:
        labels = container["Config"].get("Labels", {})
        require(
            labels.get("com.docker.compose.project") == project, "foreign container"
        )
        service = labels.get("com.docker.compose.service")
        require(service in SERVICES, "foreign service")
        require(service not in services_seen, "duplicate service")
        services_seen.add(service)
        planned = config["services"][service]
        require(
            container["Image"] == pins[service]["id"],
            "effective image ID differs from prebuild witness",
        )
        require(
            argv(container["Config"].get("Cmd"))
            == argv(planned.get("command", pins[service]["cmd"]))
            and argv(container["Config"].get("Entrypoint"))
            == argv(planned.get("entrypoint", pins[service]["entrypoint"])),
            "effective startup command differs",
        )
        require(
            container["Config"].get("User", "")
            == str(planned.get("user", pins[service]["user"]))
            and container["Config"].get("WorkingDir", "")
            == planned.get("working_dir", pins[service]["working_dir"]),
            "effective startup user/workdir differs",
        )
        if service == "test-runner":
            require(
                container["Id"] == runner_id
                and container["Name"] == f"/{binding['container_name']}"
                and container["Config"]["Hostname"] == runner_id[:12],
                "actual runner identity differs",
            )
            if exit_code is None:
                require(
                    receipt is None and container["State"]["Running"] is True,
                    "runner not waiting for pre-effect release",
                )
            else:
                require(
                    receipt == {**binding, "container_id": runner_id}
                    and container["State"]["Status"] == "exited"
                    and container["State"]["ExitCode"] == exit_code,
                    "runner release/exit differs",
                )
        require(
            set(container["NetworkSettings"]["Networks"]) == {f"{project}_default"},
            "effective foreign network",
        )
        host = container["HostConfig"]
        require(
            not host.get("Privileged")
            and not host.get("PortBindings")
            and not host.get("ExtraHosts")
            and not host.get("Devices")
            and not host.get("CapAdd"),
            "unsafe effective host configuration",
        )
        env = dict(item.split("=", 1) for item in container["Config"].get("Env", []))
        require(
            len(env) == len(container["Config"].get("Env", [])),
            "duplicate effective environment slots",
        )
        expected = config["services"][service]["environment"]
        require(
            all(
                (slot in env) == (slot in expected)
                for slot in (
                    "BIFROST_AGENT_REFERENCE_LANE_ID",
                    "BIFROST_AGENT_REFERENCE_ROLE",
                )
            ),
            "unexpected runtime construction identity slot",
        )
        require(
            container["Config"]["Image"] == config["services"][service]["image"],
            "effective image tag differs",
        )
        require(
            all(env.get(key) == value for key, value in expected.items()),
            "effective service environment differs",
        )
        require(
            not any(
                value and (key.lower().endswith("_proxy") or key.lower() == "all_proxy")
                for key, value in env.items()
            ),
            "effective proxy configured",
        )
        require(
            not any(
                value
                and (
                    re.search(r"(TOKEN|SECRET|PASSWORD|CREDENTIALS?|API_KEY|PAT)$", key)
                    or key.startswith(
                        (
                            "OPENAI_",
                            "ANTHROPIC_",
                            "AZURE_",
                            "AWS_",
                            "GITHUB_",
                            "BIFROST_AGENT_REFERENCE_OBSERVER",
                        )
                    )
                )
                and key not in expected
                for key, value in env.items()
            ),
            "ambient credential environment",
        )
        expected_mounts = {
            m["target"]: m for m in config["services"][service].get("volumes", [])
        }
        require(
            {m["Destination"] for m in container["Mounts"]} == set(expected_mounts),
            "effective mounts differ",
        )
        for mount in container["Mounts"]:
            planned = expected_mounts[mount["Destination"]]
            require(mount["Type"] == planned["type"], "effective mount type differs")
            if mount["Type"] == "bind":
                require(
                    mount["Source"] == planned["source"]
                    and mount["RW"] is not planned.get("read_only", False),
                    "effective host mount differs",
                )
            elif planned.get("source"):
                require(
                    mount["Name"] == config["volumes"][planned["source"]]["name"]
                    and mount["RW"] is not planned.get("read_only", False),
                    "effective named volume mount differs",
                )
        if service in {"api", "api-replica", "test-runner"}:
            # Effective volume name/RW alone cannot prove role-subpath isolation.
            target = "/app/reference-observer-status"
            if service != "test-runner":
                target += f"/{service}"
            actual_specs = [
                m
                for m in container["HostConfig"].get("Mounts", [])
                if m.get("Target") == target
            ]
            require(
                len(actual_specs) == 1, "missing observer volume mount specification"
            )
            actual = actual_specs[0]
            options = actual.get("VolumeOptions") or {}
            require(
                actual.get("Type") == "volume"
                and actual.get("Source") == config["volumes"]["observer-status"]["name"]
                and actual.get("ReadOnly") is (service == "test-runner")
                and options.get("NoCopy") is True
                and options.get("Subpath", "")
                == ("" if service == "test-runner" else service),
                "observer volume subpath differs",
            )
        if service in PROCESSES:
            require(service not in observed, "duplicate process service")
            observed[service] = {
                "backend": env["BIFROST_WORK_DELIVERY_BACKEND"],
                "image": container["Image"],
            }
    require(services_seen == set(SERVICES), "missing service custody witness")
    require(set(observed) == set(PROCESSES), "missing process custody witness")
    require(
        len({p["image"] for p in observed.values()}) == 1,
        "process image digests differ",
    )
    return {
        "project": project,
        "internal_network": True,
        "processes": observed,
        "worker_consumers": CONSUMERS,
        "optional_credentials_absent": True,
        "prebuild_image_ids_verified": True,
        "startup_commands_verified": True,
        "http_token_event_observer": "not supplied by this infrastructure lane",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    render_args = commands.add_parser("render")
    for flag in (
        "root",
        "project",
        "log",
        "source",
        "context",
        "lane-id",
        "input",
        "output",
    ):
        render_args.add_argument(f"--{flag}", required=True)
    inner = commands.add_parser("check-inner")
    inner.add_argument("--context", type=Path, required=True)
    inner.add_argument("--root", type=Path, required=True)
    inner.add_argument("args", nargs=argparse.REMAINDER)
    pins_args = commands.add_parser("pin-images")
    for flag in ("config", "input", "output"):
        pins_args.add_argument(f"--{flag}", required=True)
    bind = commands.add_parser("bind-run")
    bind.add_argument("--context", type=Path, required=True)
    bind.add_argument("--root", type=Path, required=True)
    bind.add_argument("args", nargs=argparse.REMAINDER)
    release = commands.add_parser("release-run")
    release.add_argument("--context", type=Path, required=True)
    release.add_argument("--runner-id", required=True)
    publish_args = commands.add_parser("publish")
    for flag in ("context", "destination", "source", "project"):
        publish_args.add_argument(f"--{flag}", required=True)
    evidence_args = commands.add_parser("check-evidence")
    for flag in ("destination", "source", "project"):
        evidence_args.add_argument(f"--{flag}", required=True)
    inspect = commands.add_parser("verify")
    for flag in (
        "config",
        "containers",
        "networks",
        "volumes",
        "project",
        "images",
        "source",
        "binding",
        "runner-id",
        "output",
    ):
        inspect.add_argument(f"--{flag}", required=True)
    inspect.add_argument("--receipt")
    inspect.add_argument("--exit-code", type=int)
    args = parser.parse_args()
    try:
        if args.command == "render":
            value = render(
                json.loads(Path(args.input).read_text()),
                Path(args.root),
                args.project,
                Path(args.log),
                args.source,
                Path(args.context),
                args.lane_id,
            )
            Path(args.output).write_text(
                json.dumps(value, indent=2, sort_keys=True) + "\n"
            )
        elif args.command == "check-inner":
            check_inner(
                args.context,
                args.root,
                args.args[1:] if args.args[:1] == ["--"] else args.args,
            )
        elif args.command == "pin-images":
            value = pin_images(
                json.loads(Path(args.config).read_text()),
                json.loads(Path(args.input).read_text()),
            )
            Path(args.output).write_text(json.dumps(value, sort_keys=True) + "\n")
        elif args.command == "bind-run":
            bind_run(
                args.context,
                args.root,
                args.args[1:] if args.args[:1] == ["--"] else args.args,
            )
        elif args.command == "release-run":
            release_run(args.context, args.runner_id)
        elif args.command == "publish":
            publish(
                Path(args.context), Path(args.destination), args.source, args.project
            )
        elif args.command == "check-evidence":
            destination = Path(args.destination)
            require(
                destination
                == Path(f"/tmp/bifrost-agent-reference-evidence-{args.project}")
                and not destination.is_symlink()
                and destination.stat().st_uid == os.getuid()
                and destination.stat().st_mode & 0o777 == 0o700,
                "invalid published evidence custody",
            )
            names = (
                "agent-reference-source.json",
                "agent-reference-custody.json",
                "agent-reference-cleanup.json",
            )
            require(
                {p.name for p in destination.iterdir()} == set(names)
                and all(not (destination / n).is_symlink() for n in names),
                "unexpected evidence files",
            )
            validate_evidence(
                *[json.loads((destination / name).read_text()) for name in names],
                args.source,
                args.project,
            )
        else:
            value = verify(
                json.loads(Path(args.config).read_text()),
                json.loads(Path(args.containers).read_text()),
                json.loads(Path(args.networks).read_text()),
                json.loads(Path(args.volumes).read_text()),
                args.project,
                json.loads(Path(args.images).read_text()),
                json.loads(Path(args.binding).read_text()),
                args.runner_id,
                json.loads(Path(args.receipt).read_text()) if args.receipt else None,
                args.exit_code,
                source=args.source,
            )
            Path(args.output).write_text(
                json.dumps(value, indent=2, sort_keys=True) + "\n"
            )
    except (ValueError, KeyError, OSError, TypeError):
        parser.exit(
            1, "Agent reference custody validation failed; inspect private context.\n"
        )


if __name__ == "__main__":
    main()
