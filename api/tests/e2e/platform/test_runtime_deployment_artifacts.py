"""Real migrated platform-table identity/immutability, not Rust admission.

The descriptor is synthetic schema-test data. No artifact is accepted for launch,
no lifecycle owner is installed, and no workflow execution is dispatched here.
"""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.constants import PROVIDER_ORG_ID
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from src.models.orm.runtime_execution import (
    RuntimeDeploymentArtifact,
    RuntimeExecutionOwner,
    RuntimeSession,
)
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


OWNER_INSERT = text("""
    INSERT INTO runtime_execution_owners
        (execution_id, owner_incarnation_id, workflow_id, deployment_id, artifact_id,
         caller_snapshot, caller_sha256)
    VALUES (:execution, :owner, :workflow, :deployment, :artifact_id,
            '{"fixture":"schema-only"}'::jsonb, :source)
""")
SESSION_INSERT = text("""
    INSERT INTO runtime_sessions
        (id, execution_id, owner_incarnation_id, workflow_attempt_id, claim_token,
         worker_incarnation_id, supervisor_incarnation_id, runtime_incarnation_id,
         channel_custody_sha256, binding_sha256, prepare_id, prepare_sha256)
    VALUES (:session, :execution, :owner, :attempt, :claim, :worker, :supervisor,
            :runtime, :source, :source, :prepare, :source)
""")


@pytest.fixture
async def owner_session(db_session: AsyncSession, association):
    """Synthetic identity records only; no Rust admission or actual channel."""
    facts = {
        **association,
        **{
            key: uuid4()
            for key in (
                "execution",
                "owner",
                "attempt",
                "claim",
                "worker",
                "supervisor",
                "runtime",
                "session",
                "prepare",
            )
        },
    }
    await db_session.execute(INSERT, facts)
    db_session.add(
        Execution(
            id=facts["execution"],
            workflow_id=facts["workflow"],
            solution_deployment_id=facts["deployment"],
            workflow_name="Schema-only native",
            executed_by_name="Synthetic",
            status=ExecutionStatus.PENDING,
        )
    )
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add(
        WorkflowExecutionAttempt(
            id=facts["attempt"],
            execution_id=facts["execution"],
            attempt_number=1,
            claim_token=facts["claim"],
            worker_incarnation_id=facts["worker"],
            status="claimed",
            phase="admission",
            published_at=now,
            claimed_at=now,
        )
    )
    await db_session.flush()
    await db_session.execute(OWNER_INSERT, facts)
    return facts


async def test_session_binds_actual_platform_attempt_and_retains_close(
    db_session, owner_session
):
    await db_session.execute(SESSION_INSERT, owner_session)
    await db_session.execute(
        text("""
        UPDATE runtime_sessions SET closed_at = clock_timestamp(), close_reason = 'cancelled'
        WHERE id = :session
    """),
        owner_session,
    )
    row = (
        await db_session.execute(
            text("""
        SELECT s.execution_id, s.workflow_attempt_id, s.claim_token, s.close_reason,
               s.closed_at IS NOT NULL, a.execution_id
        FROM runtime_sessions s JOIN workflow_execution_attempts a ON a.id = s.workflow_attempt_id
        WHERE s.id = :session
    """),
            owner_session,
        )
    ).one()
    assert tuple(row) == (
        owner_session["execution"],
        owner_session["attempt"],
        owner_session["claim"],
        "cancelled",
        True,
        owner_session["execution"],
    )


@pytest.mark.parametrize("field", ["owner", "attempt", "claim", "worker"])
async def test_session_rejects_cross_identity_without_partial_record(
    db_session, owner_session, field
):
    with pytest.raises(DBAPIError) as caught:
        async with db_session.begin_nested():
            await db_session.execute(SESSION_INSERT, {**owner_session, field: uuid4()})
    assert getattr(caught.value.orig, "sqlstate", None) == "23503"
    assert (
        await db_session.execute(
            text("SELECT count(*) FROM runtime_sessions WHERE id = :session"),
            owner_session,
        )
    ).scalar_one() == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE runtime_execution_owners SET owner_incarnation_id = gen_random_uuid() WHERE execution_id = :execution",
        "DELETE FROM runtime_execution_owners WHERE execution_id = :execution",
        "UPDATE runtime_sessions SET prepare_id = gen_random_uuid() WHERE id = :session",
        "DELETE FROM runtime_sessions WHERE id = :session",
        "UPDATE runtime_sessions SET closed_at = NULL, close_reason = NULL WHERE id = :session",
        "UPDATE runtime_sessions SET close_reason = 'different' WHERE id = :session",
    ],
)
async def test_owner_identity_and_closed_session_cannot_be_reassigned_or_erased(
    db_session, owner_session, mutation
):
    await db_session.execute(SESSION_INSERT, owner_session)
    await db_session.execute(
        text("""
        UPDATE runtime_sessions SET closed_at = clock_timestamp(), close_reason = 'cancelled'
        WHERE id = :session
    """),
        owner_session,
    )
    with pytest.raises(DBAPIError) as caught:
        async with db_session.begin_nested():
            await db_session.execute(text(mutation), owner_session)
    assert getattr(caught.value.orig, "sqlstate", None) == "23514"
    assert (
        await db_session.execute(
            text("SELECT close_reason FROM runtime_sessions WHERE id = :session"),
            owner_session,
        )
    ).scalar_one() == "cancelled"


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE workflow_execution_attempts SET claim_token = gen_random_uuid() WHERE id = :attempt",
        "UPDATE workflow_execution_attempts SET worker_incarnation_id = gen_random_uuid() WHERE id = :attempt",
        "UPDATE executions SET workflow_id = NULL WHERE id = :execution",
        "DELETE FROM executions WHERE id = :execution",
    ],
)
async def test_parent_fence_and_source_cannot_drift_under_retained_session(
    db_session, owner_session, mutation
):
    await db_session.execute(SESSION_INSERT, owner_session)
    with pytest.raises(DBAPIError) as caught:
        async with db_session.begin_nested():
            await db_session.execute(text(mutation), owner_session)
    assert getattr(caught.value.orig, "sqlstate", None) == "23503"


async def test_duplicate_session_cannot_create_a_second_launch_identity(
    db_session, owner_session
):
    await db_session.execute(SESSION_INSERT, owner_session)
    with pytest.raises(DBAPIError) as caught:
        async with db_session.begin_nested():
            await db_session.execute(
                SESSION_INSERT, {**owner_session, "session": uuid4()}
            )
    assert getattr(caught.value.orig, "sqlstate", None) == "23505"


@pytest.mark.parametrize(
    "table_name",
    [
        "runtime_deployment_artifacts",
        "runtime_execution_owners",
        "runtime_sessions",
    ],
)
async def test_alembic_runtime_tables_remain_registered_in_platform_metadata(
    db_session, table_name
):
    """Prevent a later autogenerate pass from dropping retained custody tables."""
    metadata = {
        model.__table__.name: model.__table__
        for model in (
            RuntimeDeploymentArtifact,
            RuntimeExecutionOwner,
            RuntimeSession,
        )
    }[table_name]
    connection = await db_session.connection()
    columns, foreign_keys, uniques = await connection.run_sync(
        lambda sync: (
            inspect(sync).get_columns(table_name),
            inspect(sync).get_foreign_keys(table_name),
            inspect(sync).get_unique_constraints(table_name),
        )
    )
    assert {col["name"]: col["nullable"] for col in columns} == {
        col.name: col.nullable for col in metadata.columns
    }
    assert {
        (
            tuple(key["constrained_columns"]),
            key["referred_table"],
            tuple(key["referred_columns"]),
            key["options"].get("ondelete"),
        )
        for key in foreign_keys
    } == {
        (
            tuple(col.name for col in key.columns),
            key.referred_table.name,
            tuple(element.column.name for element in key.elements),
            key.ondelete,
        )
        for key in metadata.foreign_key_constraints
    }
    from sqlalchemy import UniqueConstraint

    assert {key["name"] for key in uniques} == {
        key.name for key in metadata.constraints if isinstance(key, UniqueConstraint)
    }
