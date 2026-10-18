"""Manifest cache intent survives real PostgreSQL insert and update operations."""

from uuid import uuid4

import pytest
from sqlalchemy import delete

from bifrost.manifest import ManifestWorkflow
from bifrost.manifest_codec import Destination
from src.models.orm.workflows import Workflow
from src.services.sync_ops import Upsert

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("destination", [Destination.GIT_SYNC, Destination.INSTALL])
@pytest.mark.parametrize("initial", [None, 0, 600])
async def test_upsert_preserves_omitted_cache_and_applies_explicit_zero(
    db_session,
    destination,
    initial,
):
    workflow_id = uuid4()
    entry = {
        "id": str(workflow_id),
        "name": "cache_" + workflow_id.hex,
        "path": "workflows/cache_contract.py",
        "function_name": "cache_contract",
    }

    async def upsert(values):
        await Upsert(
            model=Workflow,
            id=workflow_id,
            values=ManifestWorkflow.model_validate(values)
            .to_orm_values(destination)
            .direct,
            match_on="id",
        ).execute(db_session)
        await db_session.commit()

    try:
        await upsert(
            entry if initial is None else {**entry, "cache_ttl_seconds": initial}
        )
        row = await db_session.get(Workflow, workflow_id)
        assert row is not None
        assert row.cache_ttl_seconds == (300 if initial is None else initial)

        row.cache_ttl_seconds = 777
        await db_session.commit()
        await upsert(entry)
        await db_session.refresh(row)
        assert row.cache_ttl_seconds == 777

        await upsert({**entry, "cache_ttl_seconds": 0})
        await db_session.refresh(row)
        assert row.cache_ttl_seconds == 0
    finally:
        await db_session.execute(delete(Workflow).where(Workflow.id == workflow_id))
        await db_session.commit()
