"""Contract checks for the migration initializer's database connection."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.dev.yml",
    "docker-compose.debug.yml",
    "docker-compose.test.yml",
)


def _find_compose(name: str) -> Path:
    """Locate a Compose file from the host checkout or test-runner mounts."""
    candidates = [Path("/app") / name, Path("/repo") / name]
    candidates.extend(parent / name for parent in Path(__file__).resolve().parents)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"{name} not found in test harness mounts")


@pytest.mark.parametrize("compose_name", COMPOSE_FILES)
def test_migration_init_uses_direct_postgres_and_runtime_services_use_pooler(
    compose_name: str,
) -> None:
    compose = yaml.safe_load(_find_compose(compose_name).read_text())
    services = compose["services"]
    init_environment = services["init"]["environment"]

    for variable in ("BIFROST_DATABASE_URL", "BIFROST_DATABASE_URL_SYNC"):
        assert "@postgres:5432/" in init_environment[variable]
        assert "@pgbouncer:5432/" not in init_environment[variable]

    for service_name in ("api", "scheduler", "worker"):
        environment = services[service_name]["environment"]
        for variable in ("BIFROST_DATABASE_URL", "BIFROST_DATABASE_URL_SYNC"):
            assert "@pgbouncer:5432/" in environment[variable]
