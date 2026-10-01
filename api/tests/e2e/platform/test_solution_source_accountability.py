"""Real PostgreSQL must reject incomplete Solution accounting evidence."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import PROVIDER_ORG_ID
from src.models.orm.workspace_promotions import WorkspaceSourceRelease

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "missing_schema", "missing_commit", "missing_tree", "wrong_commit",
    "wrong_schema", "empty_paths", "missing_paths", "paths_not_object", "missing_resolution"])
async def test_solution_completion_constraint_requires_exact_nonempty_evidence(async_engine, platform_admin, fault):
    source_sha, tree_sha = uuid4().hex + "a" * 8, "b" * 40
    evidence = {"schema_version": "bifrost.solution-owned-source-completion/v1",
        "source_commit_sha": source_sha, "source_tree_sha": tree_sha,
        "paths": {"features/fixture.py": {"sha256": "c" * 64}}}
    if fault == "missing_schema":
        del evidence["schema_version"]
    elif fault == "missing_commit":
        del evidence["source_commit_sha"]
    elif fault == "missing_tree":
        del evidence["source_tree_sha"]
    elif fault == "wrong_commit":
        evidence["source_commit_sha"] = "f" * 40
    elif fault == "wrong_schema":
        evidence["schema_version"] = "unverified"
    elif fault == "empty_paths":
        evidence["paths"] = {}
    elif fault == "missing_paths":
        del evidence["paths"]
    elif fault == "paths_not_object":
        evidence["paths"] = []
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            async with AsyncSession(bind=connection, expire_on_commit=False) as db:
                db.add(WorkspaceSourceRelease(id=uuid4(), organization_id=PROVIDER_ORG_ID,
                    source_commit_sha=source_sha, source_tree_sha=tree_sha,
                    paths={"features/fixture.py": "c" * 64}, declaration_actor="platform_admin",
                    declared_disposition="pending", disposition="released", created_by=platform_admin.user_id,
                    release_row_id=None, completion_evidence=evidence,
                    resolved_at=None if fault == "missing_resolution" else datetime.now(UTC)))
                if fault is None:
                    await db.flush()
                else:
                    with pytest.raises(IntegrityError, match="ck_workspace_source_release_released_evidence"):
                        await db.flush()
        finally:
            await transaction.rollback()
