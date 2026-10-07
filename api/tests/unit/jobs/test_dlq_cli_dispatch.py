"""Operator commands select one domain path and preserve exact recovery inputs."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from src.jobs import dlq_cli


@pytest.mark.parametrize("backend,command", [
    ("rabbitmq", "inspect"), ("rabbitmq", "replay"), ("rabbitmq", "discard"),
    ("rabbitmq", "reconcile-discard"), ("postgres", "inspect"),
    ("postgres", "discard"), ("postgres", "reconcile"), ("postgres", "status"),
])
def test_cli_dispatches_once_with_operator_and_exact_identity(backend, command, monkeypatch, capsys):
    monkeypatch.setattr(dlq_cli, "get_settings", lambda: SimpleNamespace(work_delivery_backend=backend))
    handlers = {}
    for name in ["inspect", "replay", "discard", "reconcile_discard", "postgres_inspect", "postgres_reconcile", "postgres_discard", "postgres_status"]:
        handlers[name] = AsyncMock(return_value=[{"observed": True}])
        monkeypatch.setattr(dlq_cli, name, handlers[name])
    identity = str(uuid4())
    args = [command, "workflow-executions"]
    if command in {"discard", "replay", "reconcile", "reconcile-discard"}:
        args += ["--actor", "fixture-operator", "--reason", "reviewed recovery", "--dry-run"]
    if backend == "postgres" and command in {"discard", "reconcile"}:
        args += ["--delivery-id", identity]
    if command == "reconcile-discard":
        args += ["--message-id", "original-message", "--execution-id", identity, "--expected-reason", "original-reason"]
    if command == "inspect" and backend == "postgres":
        args += ["--status", "interrupted"]
    assert dlq_cli.main(args) == 0
    selected = ("postgres_" if backend == "postgres" else "") + command.replace("-", "_")
    assert sum(handler.await_count for handler in handlers.values()) == 1
    call = handlers[selected].await_args
    assert call.args[0] == "workflow-executions"
    if command in {"discard", "replay", "reconcile", "reconcile-discard"}:
        assert call.kwargs["actor"] == "fixture-operator"
        if not (command == "discard" and backend == "rabbitmq"):
            assert call.kwargs["reason"] == "reviewed recovery"
    if backend == "postgres" and command in {"discard", "reconcile"}:
        assert call.kwargs["delivery_id"] == identity
        assert call.kwargs["dry_run"] is True
    if command == "reconcile-discard":
        assert call.kwargs["message_id"] == "original-message"
        assert call.kwargs["execution_id"] == identity
        assert call.kwargs["expected_reason"] == "original-reason"
    assert json.loads(capsys.readouterr().out) == [{"observed": True}]


@pytest.mark.parametrize("backend,args", [
    ("rabbitmq", ["status"]),
    ("rabbitmq", ["reconcile", "--delivery-id", "original", "--actor", "actor", "--reason", "reason"]),
    ("postgres", ["discard", "workflow-executions", "--actor", "actor", "--reason", "reason"]),
    ("postgres", ["reconcile-discard", "workflow-executions", "--message-id", "original", "--execution-id", str(uuid4()), "--expected-reason", "original", "--actor", "actor", "--reason", "reason"]),
])
def test_unsupported_or_bulk_transport_recovery_fails_before_domain_mutation(monkeypatch, backend, args):
    monkeypatch.setattr(dlq_cli, "get_settings", lambda: SimpleNamespace(work_delivery_backend=backend))
    recover = AsyncMock()
    discard = AsyncMock()
    monkeypatch.setattr(dlq_cli, "postgres_reconcile", recover)
    monkeypatch.setattr(dlq_cli, "postgres_discard", discard)
    with pytest.raises(ValueError):
        dlq_cli.main(args)
    recover.assert_not_awaited()
    discard.assert_not_awaited()


def test_postgres_generic_replay_remains_refused_even_with_dry_run(monkeypatch):
    monkeypatch.setattr(dlq_cli, "get_settings", lambda: SimpleNamespace(work_delivery_backend="postgres"))
    with pytest.raises(RuntimeError, match="no generic replay"):
        dlq_cli.main(["replay", "workflow-executions", "--dry-run", "--actor", "actor", "--reason", "reason"])
