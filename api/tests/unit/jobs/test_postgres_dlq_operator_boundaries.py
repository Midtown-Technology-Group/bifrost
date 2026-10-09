"""Exact transport recovery keeps envelopes private and uncertain outcomes uncommitted."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from src.jobs import dlq_cli as cli


def delivery(status="interrupted"):
    now = datetime.now(UTC)
    return SimpleNamespace(id=uuid4(), queue_name="workflow-executions", message_id="original-message",
                           status=status, claim_count=2, created_at=now, available_at=now, started_at=now,
                           settled_at=None, lease_owner=None, lease_expires_at=None,
                           encrypted_envelope="private-ciphertext")


@pytest.fixture
def database(monkeypatch):
    from src.core import database as core
    db = MagicMock()
    for name in ["execute", "rollback", "commit", "refresh", "flush"]:
        setattr(db, name, AsyncMock())
    @asynccontextmanager
    async def context():
        yield db
    monkeypatch.setattr(core, "get_db_context", context)
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize("queue", [None, "package-installations", "workflow-executions"])
async def test_status_reports_only_transport_metadata_and_separate_package_control_rows(database, queue):
    first, packages = MagicMock(), MagicMock()
    first.all.return_value = [("workflow-executions", "interrupted", 2)]
    packages.all.return_value = [("pending", 3)]
    database.execute.side_effect = [first, packages]
    rows = await cli.postgres_status(queue)
    assert rows[0] == {"backend": "postgres", "queue": "workflow-executions", "status": "interrupted",
                       "count": 2, "source": "work_deliveries"}
    assert database.execute.await_count == (1 if queue == "workflow-executions" else 2)
    assert len(rows) == (1 if queue == "workflow-executions" else 2)
    if len(rows) == 2:
        assert rows[1]["source"] == "worker_control_commands"
    database.commit.assert_not_called()
    database.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, "all", "interrupted", "poison"])
async def test_inspect_never_decrypts_an_envelope_or_changes_delivery(database, status):
    row = delivery()
    result = MagicMock()
    result.scalars.return_value = [row]
    database.execute.return_value = result
    rows = await cli.postgres_inspect("workflow-executions", 10, status)
    assert rows[0]["delivery_id"] == str(row.id)
    assert "encrypted_envelope" not in rows[0]
    assert "body" not in rows[0]
    database.commit.assert_not_called()
    database.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,status", [(0, None), (101, None), (1, "unknown")])
async def test_invalid_inspection_stops_before_database_read(database, limit, status):
    with pytest.raises(ValueError):
        await cli.postgres_inspect("workflow-executions", limit, status)
    database.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["invalid-id", "missing", "wrong-queue", "exact"])
async def test_exact_lookup_rejects_invalid_missing_or_other_queue_delivery(database, case):
    row = delivery()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None if case == "missing" else row
    database.execute.return_value = result
    identity = "not-a-uuid" if case == "invalid-id" else str(row.id)
    queue = "other-queue" if case == "wrong-queue" else row.queue_name
    if case == "exact":
        assert await cli._postgres_delivery(database, identity, queue) is row
    else:
        with pytest.raises(ValueError):
            await cli._postgres_delivery(database, identity, queue)
    if case == "invalid-id":
        database.execute.assert_not_called()
    database.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["poison", "queued", "refused", "nonterminal", "dry-run", "applied", "receipt-failure"])
async def test_discard_requires_owned_terminal_recovery_and_durable_receipt(database, monkeypatch, case):
    from src.services import work_delivery_store
    row = delivery(case if case in {"poison", "queued"} else "interrupted")
    monkeypatch.setattr(cli, "_postgres_delivery", AsyncMock(return_value=row))
    async def recover(db, identity):
        assert db is database
        assert identity == row.id
        if case not in {"refused", "nonterminal"}:
            row.status = "completed"
        return case != "refused"
    recovery = AsyncMock(side_effect=recover)
    monkeypatch.setattr(work_delivery_store, "recover_interrupted_delivery", recovery)
    receipt = AsyncMock(side_effect=RuntimeError("receipt unavailable") if case == "receipt-failure" else None)
    monkeypatch.setattr(cli, "_record_postgres_disposition", receipt)
    operation = cli.postgres_discard(row.queue_name, delivery_id=str(row.id), dry_run=case == "dry-run",
                                     actor=" operator ", reason=" reviewed terminal delivery ")
    if case in {"dry-run", "applied"}:
        result = await operation
        assert result[0]["before"]["status"] == "interrupted"
        assert result[0]["after"]["discard"] == ("would_apply" if case == "dry-run" else "applied")
    else:
        with pytest.raises(RuntimeError):
            await operation
    if case in {"poison", "queued"}:
        recovery.assert_not_called()
    else:
        recovery.assert_awaited_once()
    if case == "applied":
        receipt.assert_awaited_once_with(database, row, action="discard", actor="operator", reason="reviewed terminal delivery")
        database.commit.assert_awaited_once()
    else:
        database.commit.assert_not_called()
    if case not in {"applied", "receipt-failure"}:
        receipt.assert_not_called()
        database.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["wrong-status", "refused", "dry-run", "applied", "receipt-failure"])
async def test_reconcile_never_commits_refused_uncertain_or_preview_recovery(database, monkeypatch, case):
    from src.services import work_delivery_store
    row = delivery("poison" if case == "wrong-status" else "interrupted")
    monkeypatch.setattr(cli, "_postgres_delivery", AsyncMock(return_value=row))
    recovery = AsyncMock(return_value=case != "refused")
    monkeypatch.setattr(work_delivery_store, "recover_interrupted_delivery", recovery)
    receipt = AsyncMock(side_effect=RuntimeError("receipt unavailable") if case == "receipt-failure" else None)
    monkeypatch.setattr(cli, "_record_postgres_disposition", receipt)
    operation = cli.postgres_reconcile(row.queue_name, delivery_id=str(row.id), dry_run=case == "dry-run",
                                       actor="operator", reason="reviewed recovery")
    if case in {"wrong-status", "receipt-failure"}:
        with pytest.raises(RuntimeError):
            await operation
    else:
        result = await operation
        assert result[0]["after"]["recovery"] == {"refused": "refused", "dry-run": "would_apply", "applied": "applied"}[case]
        assert "encrypted_envelope" not in result[0]["after"]
    if case == "wrong-status":
        recovery.assert_not_called()
    else:
        recovery.assert_awaited_once_with(database, row.id)
    if case == "applied":
        receipt.assert_awaited_once_with(database, row, action="reconcile", actor="operator", reason="reviewed recovery")
        database.commit.assert_awaited_once()
    else:
        database.commit.assert_not_called()
    if case in {"refused", "dry-run"}:
        database.rollback.assert_awaited_once()
        receipt.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("actor,reason", [("", "review"), ("operator", "  ")])
async def test_missing_audit_identity_stops_before_recovery(database, monkeypatch, actor, reason):
    lookup = AsyncMock()
    monkeypatch.setattr(cli, "_postgres_delivery", lookup)
    for operation in [cli.postgres_reconcile, cli.postgres_discard]:
        with pytest.raises(ValueError):
            await operation("workflow-executions", delivery_id=str(uuid4()), dry_run=True, actor=actor, reason=reason)
    lookup.assert_not_called()
    database.commit.assert_not_called()
