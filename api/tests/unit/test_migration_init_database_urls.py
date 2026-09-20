"""Contract checks for the migration initializer's database connection."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.dev.yml",
    "docker-compose.debug.yml",
    "docker-compose.test.yml",
)


@pytest.mark.parametrize("compose_name", COMPOSE_FILES)
def test_migration_init_uses_direct_postgres_and_runtime_services_use_pooler(
    compose_name: str,
) -> None:
    compose = yaml.safe_load((REPO_ROOT / compose_name).read_text())
    services = compose["services"]
    init_environment = services["init"]["environment"]

    for variable in ("BIFROST_DATABASE_URL", "BIFROST_DATABASE_URL_SYNC"):
        assert "@postgres:5432/" in init_environment[variable]
        assert "@pgbouncer:5432/" not in init_environment[variable]

    for service_name in ("api", "scheduler", "worker"):
        environment = services[service_name]["environment"]
        for variable in ("BIFROST_DATABASE_URL", "BIFROST_DATABASE_URL_SYNC"):
            assert "@pgbouncer:5432/" in environment[variable]
