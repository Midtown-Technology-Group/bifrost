"""Real migrated platform-table identity/immutability, not Rust admission.

The descriptor is synthetic schema-test data. No artifact is accepted for launch,
no lifecycle owner is installed, and no workflow execution is dispatched here.
"""

import json
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.constants import PROVIDER_ORG_ID
from src.models.orm.solution_deployments import SolutionDeployment
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.fixture
async def association(db_session: AsyncSession, platform_admin):
    solution_id, workflow_id, deployment_id = uuid4(), uuid4(), uuid4()
    db_session.add(
        Solution(
            id=solution_id,
            slug=f"native-schema-{solution_id}",
            name="Native schema fixture",
            organization_id=PROVIDER_ORG_ID,
            execution_runtime_mode="repo-v1",
        )
    )
    await db_session.flush()
    db_session.add(
        Workflow(
            id=workflow_id,
            name=f"native-schema-{workflow_id}",
            function_name="Run",
            path="cmd/workflow",
            type="workflow",
            solution_id=solution_id,
            organization_id=PROVIDER_ORG_ID,
            is_active=True,
            endpoint_enabled=False,
            public_endpoint=False,
            api_key_enabled=False,
        )
    )
    db_session.add(
        SolutionDeployment(
            id=deployment_id,
            solution_id=solution_id,
            organization_id=PROVIDER_ORG_ID,
            state="draft",
            bundle_hash="sha256:" + "a" * 64,
            compiled_manifest={},
            compiled_manifest_hash="sha256:" + "b" * 64,
            resolution_map={},
            resolution_map_hash="sha256:" + "c" * 64,
            source_artifact_key="synthetic/schema-only",
            runtime_storage_prefix="synthetic/schema-only",
            created_by=platform_admin.user_id,
        )
    )
    await db_session.flush()
    return {
        "solution": solution_id,
        "workflow": workflow_id,
        "deployment": deployment_id,
        "artifact_id": "sha256:" + "a" * 64,
        "source": "b" * 64,
        "build": "c" * 64,
        "reviewer": platform_admin.user_id,
        "artifact": json.dumps(
            {
                "kind": "native-executable/v1",
                "artifact_id": "sha256:" + "a" * 64,
                "runtime_protocol": "bifrost.runtime/v1",
                "build_evidence_sha256": "c" * 64,
            }
        ),
    }


INSERT = text("""
    INSERT INTO runtime_deployment_artifacts
        (deployment_id, workflow_id, solution_id, artifact_id, runtime, runtime_protocol,
         artifact, input_schema, output_schema, source_sha256, build_evidence_sha256, reviewed_by)
    VALUES (:deployment, :workflow, :solution, :artifact_id, 'go-native/v1', 'bifrost.runtime/v1',
            CAST(:artifact AS jsonb), '{}'::jsonb, '{}'::jsonb, :source, :build, :reviewer)
""")


async def test_actual_platform_association_preserves_deployment_and_descriptor(
    db_session, association
):
    await db_session.execute(INSERT, association)
    row = (
        await db_session.execute(
            text("""
        SELECT a.artifact_id, a.runtime, d.solution_id, w.solution_id
        FROM runtime_deployment_artifacts a
        JOIN solution_deployments d ON d.id = a.deployment_id
        JOIN workflows w ON w.id = a.workflow_id
        WHERE a.deployment_id = :deployment AND a.workflow_id = :workflow
    """),
            association,
        )
    ).one()
    assert tuple(row) == (
        association["artifact_id"],
        "go-native/v1",
        association["solution"],
        association["solution"],
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE runtime_deployment_artifacts SET source_sha256 = repeat('d', 64) WHERE deployment_id = :deployment",
        'UPDATE runtime_deployment_artifacts SET input_schema = \'{"type":"string"}\'::jsonb WHERE deployment_id = :deployment',
        "DELETE FROM runtime_deployment_artifacts WHERE deployment_id = :deployment",
    ],
)
async def test_association_custody_is_immutable(db_session, association, mutation):
    await db_session.execute(INSERT, association)
    with pytest.raises(DBAPIError, match="runtime artifact association is immutable"):
        async with db_session.begin_nested():
            await db_session.execute(text(mutation), association)
    assert (
        await db_session.execute(
            text(
                "SELECT source_sha256 FROM runtime_deployment_artifacts WHERE deployment_id = :deployment"
            ),
            association,
        )
    ).scalar_one() == association["source"]


@pytest.mark.parametrize(
    "field, value, code",
    [
        ("solution", uuid4(), "23503"),
        ("artifact_id", "sha256:" + "d" * 64, "23514"),
        ("artifact", "{}", "23514"),
        ("source", "BAD", "23514"),
    ],
)
async def test_wrong_identity_or_missing_binding_rejects_without_partial_record(
    db_session, association, field, value, code
):
    altered = {**association, field: value}
    with pytest.raises(DBAPIError) as caught:
        async with db_session.begin_nested():
            await db_session.execute(INSERT, altered)
    assert getattr(caught.value.orig, "sqlstate", None) == code
    assert (
        await db_session.execute(
            text(
                "SELECT count(*) FROM runtime_deployment_artifacts WHERE deployment_id = :deployment"
            ),
            association,
        )
    ).scalar_one() == 0
