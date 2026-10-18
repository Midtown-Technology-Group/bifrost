"""The upstream sync must preserve MTG's active CLI runner and its stored state."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_upstream_runner_removal_revision_preserves_existing_schema(direction):
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/20260927_drop_local_runner.py"
    )
    spec = importlib.util.spec_from_file_location("runner_compat_migration", migration_path)
    assert spec is not None and spec.loader is not None
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    operations = MagicMock()
    revision.op = operations

    getattr(revision, direction)()

    assert operations.mock_calls == []


def test_retained_cli_runner_schema_and_contract_are_connected():
    from src.models.contracts import cli
    from src.models.orm import CLISession, Execution

    assert CLISession.__tablename__ == "cli_sessions"
    session_column = Execution.__table__.c.session_id
    assert {key.target_fullname for key in session_column.foreign_keys} == {"cli_sessions.id"}
    assert "is_local_execution" in Execution.__table__.c
    assert Execution.__mapper__.relationships["cli_session"].mapper.class_ is CLISession
    assert CLISession.__mapper__.relationships["executions"].mapper.class_ is Execution
    assert "session_id" in cli.CLISessionRegisterRequest.model_fields
    assert "id" in cli.CLISessionResponse.model_fields
    assert "executions" in cli.CLISessionResponse.model_fields
