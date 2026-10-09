"""Real PostgreSQL preserves retired registrations even through Core writers."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from src.models.orm.workflows import Workflow
from src.services.workflow_registration_retirement import (
    build_workflow_retirement_evidence,
    validate_workflow_retirement_evidence,
    workflow_retirement_snapshot_hash,
)

from tests.e2e.platform.test_solution_source_revision import db_session as db_session

pytestmark = pytest.mark.e2e


async def _registration(db_session):
    row = Workflow(id=uuid4(), name="Retirement storage fixture", function_name="retire",
        path=f"features\\retirement_storage\\{uuid4().hex}.py", organization_id=None,
        is_active=True, roles=[])
    db_session.add(row)
    await db_session.flush()
    before = workflow_retirement_snapshot_hash(row)
    row.is_active = False
    evidence = build_workflow_retirement_evidence(row,
        release_id="sha256:" + "a" * 64, artifact_id=uuid4(), before_registration_hash=before,
        caller_inventory_digest="sha256:" + "b" * 64, review_digest="sha256:" + "c" * 64,
        reason="Reviewed obsolete registration", retired_by_user_id=uuid4(), retired_at=datetime.now(UTC))
    # The marker and state must be one mutation, as in the production service.
    with db_session.no_autoflush:
        await db_session.execute(update(Workflow).where(Workflow.id == row.id).values(
            is_active=False, retirement_evidence=evidence).execution_options(synchronize_session=False))
        await db_session.refresh(row)
    return row, evidence


@pytest.mark.asyncio
async def test_native_retirement_persists_original_uuid_and_valid_evidence(db_session):
    row, evidence = await _registration(db_session)
    # Benign timestamp changes do not rewrite the retained disposition.
    await db_session.execute(update(Workflow).where(Workflow.id == row.id).values(last_seen_at=datetime.now(UTC)))
    current = await db_session.scalar(select(Workflow).where(Workflow.id == row.id)
        .options(selectinload(Workflow.roles)).execution_options(populate_existing=True))
    assert current is not None and current.id == row.id and not current.is_active
    assert validate_workflow_retirement_evidence(current) == evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"is_active": True}, {"endpoint_enabled": True}, {"public_endpoint": True},
    {"api_key_enabled": True}, {"solution_id": uuid4()}, {"id": uuid4()},
    {"organization_id": uuid4()}, {"path": "features/changed.py"},
    {"function_name": "replacement"}, {"retirement_evidence": None},
    {"retirement_evidence": {}},
])
async def test_core_writers_cannot_reactivate_reown_repoint_or_clear_retired_registration(db_session, change):
    row, evidence = await _registration(db_session)
    with pytest.raises(IntegrityError, match="retired workflow|retirement evidence"):
        async with db_session.begin_nested():
            await db_session.execute(update(Workflow).where(Workflow.id == row.id).values(**change)
                .execution_options(synchronize_session=False))
    await db_session.refresh(row)
    assert row.retirement_evidence == evidence and row.is_active is False


@pytest.mark.asyncio
async def test_core_writer_cannot_replace_marker_with_another_valid_review(db_session):
    row, evidence = await _registration(db_session)
    with pytest.raises(IntegrityError, match="write-once"):
        async with db_session.begin_nested():
            await db_session.execute(update(Workflow).where(Workflow.id == row.id).values(
                retirement_evidence={**evidence, "reason": "Different review"}))


@pytest.mark.asyncio
async def test_core_delete_cannot_remove_retired_identity_or_history(db_session):
    row, _ = await _registration(db_session)
    with pytest.raises(IntegrityError, match="history cannot be deleted"):
        async with db_session.begin_nested():
            await db_session.execute(delete(Workflow).where(Workflow.id == row.id))


@pytest.mark.asyncio
async def test_marker_cannot_be_attached_while_changing_original_identity(db_session):
    row = Workflow(id=uuid4(), name="Original retirement identity", function_name="retire",
        path=f"features/retirement_storage/{uuid4().hex}.py", is_active=False, roles=[])
    db_session.add(row)
    await db_session.flush()
    evidence = build_workflow_retirement_evidence(row,
        release_id="sha256:" + "a" * 64, artifact_id=uuid4(),
        before_registration_hash=workflow_retirement_snapshot_hash(row),
        caller_inventory_digest="sha256:" + "b" * 64, review_digest="sha256:" + "c" * 64,
        reason="Reviewed original identity", retired_by_user_id=uuid4(), retired_at=datetime.now(UTC))
    with pytest.raises(IntegrityError, match="identity is immutable"):
        async with db_session.begin_nested():
            await db_session.execute(update(Workflow).where(Workflow.id == row.id).values(
                path="features/repointed.py", retirement_evidence=evidence))


@pytest.mark.asyncio
async def test_new_registration_cannot_start_with_a_retirement_marker(db_session):
    _, evidence = await _registration(db_session)
    with pytest.raises(IntegrityError, match="existing workflow registration"):
        async with db_session.begin_nested():
            await db_session.execute(insert(Workflow).values(id=uuid4(), name="Forged marker",
                path=f"features/retirement_storage/{uuid4().hex}.py", function_name="retire",
                is_active=False, retirement_evidence=evidence))


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", [{}, {"schema_version": "unreviewed"}])
async def test_core_writer_cannot_attach_a_marker_without_original_identity(db_session, evidence):
    row = Workflow(id=uuid4(), name="Unmarked inactive registration", function_name="retire",
        path=f"features/retirement_storage/{uuid4().hex}.py", is_active=False, roles=[])
    db_session.add(row)
    await db_session.flush()
    with pytest.raises(IntegrityError, match="evidence identity is invalid"):
        async with db_session.begin_nested():
            await db_session.execute(update(Workflow).where(Workflow.id == row.id).values(
                retirement_evidence=evidence))
    await db_session.refresh(row)
    assert row.retirement_evidence is None


@pytest.mark.asyncio
async def test_ordinary_inactive_registration_keeps_existing_reactivation_behavior(db_session):
    row = Workflow(id=uuid4(), name="Ordinary inactive registration", function_name="retire",
        path=f"features/retirement_storage/{uuid4().hex}.py", is_active=False, roles=[])
    db_session.add(row)
    await db_session.flush()
    await db_session.execute(update(Workflow).where(Workflow.id == row.id).values(is_active=True))
    await db_session.refresh(row)
    assert row.is_active is True and row.retirement_evidence is None


@pytest.mark.asyncio
@pytest.mark.parametrize("decorator", ["workflow", "tool", "data_provider", "service"])
async def test_retained_decorated_source_with_stale_prefetch_does_not_reopen_or_poison_transaction(db_session, decorator):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.services.file_storage.indexers.workflow import WorkflowIndexer

    row, evidence = await _registration(db_session)
    stale = SimpleNamespace(id=row.id, name=row.name, function_name=row.function_name,
        path=row.path, type="workflow", is_active=False, description=None,
        category="General", tags=[], retirement_evidence=None)
    indexer = WorkflowIndexer(db_session)
    indexer.set_prefetch_cache({(row.path, row.function_name): stale})
    indexer.refresh_workflow_endpoint = AsyncMock()
    source = (f"from bifrost import {decorator}\n@{decorator}(name='Source edit')\n"
              "async def retire(value: str = 'new'):\n    return value\n").encode()
    await indexer.index_python_file(row.path, source)
    current = await db_session.scalar(select(Workflow).where(Workflow.id == row.id)
        .options(selectinload(Workflow.roles)).execution_options(populate_existing=True))
    assert current is not None and current.is_active is False
    assert validate_workflow_retirement_evidence(current) == evidence
    indexer.refresh_workflow_endpoint.assert_not_awaited()
    # The transaction remains usable after the skipped index operation.
    assert await db_session.scalar(select(Workflow.id).where(Workflow.id == row.id)) == row.id
