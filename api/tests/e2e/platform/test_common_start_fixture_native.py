"""Native pinned timeout evidence only; no common schema/principal activation."""

import json

import pytest
from sqlalchemy import text

from src.models.orm.workflows import Workflow
from tests.helpers.common_cancel_fixture import _native_evidence, _read_seed
from tests.helpers.common_start_fixture import seed_start_native_history

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout_seconds", [30, 0])
async def test_start_history_pins_native_finite_and_no_timeout(
    async_session_factory,
    tmp_path,
    timeout_seconds,
):
    async with async_session_factory() as db:
        assert await db.scalar(
            text("SELECT to_regclass('public.runtime_sessions') IS NULL")
        )
        principal = await db.scalar(text("SELECT session_user::text"))
    output = tmp_path / "native.json"
    await seed_start_native_history(
        async_session_factory,
        expected_principal=principal,
        destination=output,
        timeout_seconds=timeout_seconds,
    )
    seed = _read_seed(output)
    async with async_session_factory() as db:
        workflow = await db.get(Workflow, seed["workflow_id"])
        assert workflow.timeout_seconds == timeout_seconds
        evidence = await _native_evidence(db, seed)
        assert evidence["workflow_timeout_seconds"] == timeout_seconds
        assert await db.scalar(
            text("SELECT to_regclass('public.runtime_sessions') IS NULL")
        )
    # Immutable pending record and observed final publication both remain.
    assert json.loads(output.read_text())["workflow_id"] == str(seed["workflow_id"])
    assert (tmp_path / "native.json.pending").is_file()
