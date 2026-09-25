"""Focused tests for the native Alembic migration-session lock."""

from __future__ import annotations

import importlib.util
import sys
import types
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self
from unittest.mock import MagicMock

import pytest


class _ScalarResult:
    def __init__(self, value: bool) -> None:
        self._value = value

    def scalar_one(self) -> bool:
        return self._value


class _Connection:
    def __init__(self, results: list[bool]) -> None:
        self.results = iter(results)
        self.calls: list[tuple[object, object]] = []
        self.commits = 0

    def execute(self, statement: object, parameters: object = None) -> _ScalarResult:
        self.calls.append((statement, parameters))
        return _ScalarResult(next(self.results))

    def commit(self) -> None:
        self.commits += 1


def _load_alembic_env(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, Any]:
    """Load env.py with its migration entry point held behind fake Alembic modules."""
    context: Any = types.ModuleType("alembic.context")
    context.config = SimpleNamespace(
        config_file_name=None,
        config_ini_section="alembic",
        attributes={},
        get_section=lambda _section, default: default,
    )
    context.is_offline_mode = lambda: True
    context.configure = MagicMock()
    context.begin_transaction = lambda: nullcontext()
    context.run_migrations = MagicMock()
    context.get_context = MagicMock()

    alembic: Any = types.ModuleType("alembic")
    alembic.context = context
    monkeypatch.setitem(sys.modules, "alembic", alembic)
    monkeypatch.setitem(sys.modules, "alembic.context", context)

    config: Any = types.ModuleType("src.config")
    config.get_settings = lambda: SimpleNamespace(
        database_url_sync="postgresql://unused",
        database_url="postgresql+asyncpg://unused",
    )
    database: Any = types.ModuleType("src.core.database")
    database.Base = SimpleNamespace(metadata=object())
    models: Any = types.ModuleType("src.models")
    for model_name in (
        "Organization",
        "User",
        "Role",
        "UserRole",
        "Form",
        "FormRole",
        "Execution",
        "ExecutionLog",
        "CLISession",
        "Config",
        "Workflow",
        "ServiceDefinition",
        "ServiceAttempt",
        "ServiceLog",
        "OAuthProvider",
        "OAuthToken",
        "AuditLog",
        "UserMFAMethod",
        "MFARecoveryCode",
        "TrustedDevice",
        "UserOAuthAccount",
        "GlobalBranding",
        "Application",
        "AppRole",
    ):
        setattr(models, model_name, type(model_name, (), {}))
    monkeypatch.setitem(sys.modules, "src.config", config)
    monkeypatch.setitem(sys.modules, "src.core.database", database)
    monkeypatch.setitem(sys.modules, "src.models", models)

    env_path = Path(__file__).parents[2] / "alembic" / "env.py"
    module_name = f"_test_alembic_env_{id(context)}"
    spec = importlib.util.spec_from_file_location(module_name, env_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    context.configure.reset_mock()
    context.run_migrations.reset_mock()
    return module, context


def test_migration_lock_is_held_through_commit_and_released(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, context = _load_alembic_env(monkeypatch)
    connection = _Connection([True, True])
    events: list[str] = []
    context.configure.side_effect = lambda **_kwargs: events.append("configure")
    context.begin_transaction = lambda: _transaction_events(events)
    context.run_migrations.side_effect = lambda: events.append("migrate")

    module.do_run_migrations(connection)

    assert events == ["configure", "begin", "migrate", "commit"]
    assert connection.commits == 1
    assert "pg_try_advisory_lock" in str(connection.calls[0][0])
    assert "pg_advisory_unlock" in str(connection.calls[1][0])
    assert (
        connection.calls[0][1]
        == connection.calls[1][1]
        == {"lock_key": module.MIGRATION_ADVISORY_LOCK_KEY}
    )


def test_busy_migration_lock_fails_without_running_or_unlocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, context = _load_alembic_env(monkeypatch)
    connection = _Connection([False])

    with pytest.raises(RuntimeError, match="already in progress"):
        module.do_run_migrations(connection)

    assert len(connection.calls) == 1
    assert connection.commits == 0
    context.configure.assert_not_called()
    context.run_migrations.assert_not_called()


def test_migration_failure_releases_lock_and_preserves_original_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, context = _load_alembic_env(monkeypatch)
    connection = _Connection([True, True])
    migration_error = RuntimeError("migration failed")
    context.run_migrations.side_effect = migration_error

    with pytest.raises(RuntimeError, match="migration failed") as raised:
        module.do_run_migrations(connection)

    assert raised.value is migration_error
    assert len(connection.calls) == 2
    assert "pg_advisory_unlock" in str(connection.calls[-1][0])


def test_stale_expected_heads_fail_before_revision_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, context = _load_alembic_env(monkeypatch)
    connection = _Connection([True, True])
    module.config.attributes["expected_current_heads"] = ["expected_head"]
    context.get_context.return_value.get_current_heads.return_value = ("database_head",)

    with pytest.raises(RuntimeError, match="heads changed"):
        module.do_run_migrations(connection)

    context.run_migrations.assert_not_called()
    assert len(connection.calls) == 2


def test_expected_heads_require_a_simple_revision_string_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, context = _load_alembic_env(monkeypatch)
    connection = _Connection([True, True])
    module.config.attributes["expected_current_heads"] = "database_head"

    with pytest.raises(RuntimeError, match="list of non-empty revision strings"):
        module.do_run_migrations(connection)

    context.run_migrations.assert_not_called()
    assert len(connection.calls) == 2


def test_matching_expected_heads_allow_revision_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, context = _load_alembic_env(monkeypatch)
    connection = _Connection([True, True])
    module.config.attributes["expected_current_heads"] = ["database_head"]
    context.get_context.return_value.get_current_heads.return_value = ("database_head",)

    module.do_run_migrations(connection)

    context.run_migrations.assert_called_once()
    assert len(connection.calls) == 2


class _transaction_events:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def __enter__(self) -> Self:
        self.events.append("begin")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.events.append("rollback" if exc_type else "commit")
