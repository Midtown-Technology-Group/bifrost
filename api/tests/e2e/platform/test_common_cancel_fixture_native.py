"""Native PostgreSQL namespace proof, without candidate schema or role changes."""

from uuid import uuid4

import pytest
from sqlalchemy import text

from src.models.orm.executions import Execution
from tests.helpers.common_cancel_fixture import _insert_claimed_attempt

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
@pytest.mark.parametrize("shadow", [False, True])
async def test_fixture_claim_uses_public_table_with_trusted_search_path(
    db_session, shadow
):
    # Existing native test identity only. This is not restricted-role or C5 proof.
    assert await db_session.scalar(
        text(
            "SELECT current_database()='bifrost_test' "
            "AND to_regclass('public.runtime_sessions') IS NULL"
        )
    )
    execution_id, attempt_id, claim_token, worker_id = (uuid4() for _ in range(4))
    db_session.add(
        Execution(
            id=execution_id,
            workflow_name="synthetic-namespace-fixture",
            executed_by_name="SQL namespace fixture",
        )
    )
    await db_session.flush()
    now = await db_session.scalar(text("SELECT clock_timestamp()"))
    if shadow:
        await db_session.execute(
            text(
                "CREATE TEMP TABLE workflow_execution_attempts "
                "(LIKE public.workflow_execution_attempts INCLUDING DEFAULTS) "
                "ON COMMIT DROP"
            )
        )
    await db_session.execute(text("SET LOCAL search_path=pg_catalog,pg_temp"))
    await _insert_claimed_attempt(
        db_session,
        execution_id=execution_id,
        attempt_id=attempt_id,
        claim_token=claim_token,
        worker_incarnation_id=worker_id,
        now=now,
    )
    row = (
        await db_session.execute(
            text(
                "SELECT execution_id,claim_token,worker_incarnation_id,status,phase,"
                "policy_version,created_at IS NOT NULL AS created "
                "FROM public.workflow_execution_attempts WHERE id=:attempt_id"
            ),
            {"attempt_id": attempt_id},
        )
    ).one()
    assert tuple(row) == (
        execution_id,
        claim_token,
        worker_id,
        "claimed",
        "claim",
        "workflow-attempt/v1",
        True,
    )
    if shadow:
        assert (
            await db_session.scalar(
                text("SELECT count(*) FROM pg_temp.workflow_execution_attempts")
            )
            == 0
        )
    assert await db_session.scalar(text("SHOW search_path")) == "pg_catalog, pg_temp"
    # db_session rolls back the root/claim and any temporary table together.
