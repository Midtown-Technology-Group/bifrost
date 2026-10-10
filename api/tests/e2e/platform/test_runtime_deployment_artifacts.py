"""Real migrated platform-table identity/immutability, not Rust admission.

The descriptor is synthetic schema-test data. No artifact is accepted for launch,
no lifecycle owner is installed, and no workflow execution is dispatched here.
"""

import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.constants import PROVIDER_ORG_ID
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from src.models.orm.runtime_execution import (
    RuntimeAdmission,
    RuntimeDeploymentArtifact,
    RuntimeExecutionOwner,
    RuntimeReportReceipt,
    RuntimeSession,
    RuntimeStart,
    WorkflowRuntimeSDKGrant,
    WorkflowRuntimeSDKGrantOperation,
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


async def test_grant_cannot_store_an_already_expired_access_window(
    db_session, grant_storage
):
    with pytest.raises(DBAPIError) as caught:
        async with db_session.begin_nested():
            await db_session.execute(
                GRANT_INSERT, {**grant_storage, "expires": grant_storage["issued"]}
            )
    assert getattr(caught.value.orig, "sqlstate", None) == "23514"


OPERATION_INSERT = text("""
    INSERT INTO workflow_runtime_sdk_grant_operations
        (grant_id, ordinal, operation, integration_name, scope_kind, scope_organization_id,
         resolved_organization_id, solution_install_id)
    VALUES (:grant, 0, 'integration-get', 'SyntheticReadiness', 'organization', :org, :org, :solution)
""")


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE workflow_runtime_sdk_grant_operations SET integration_name = 'OtherIntegration' WHERE grant_id = :grant",
        "DELETE FROM workflow_runtime_sdk_grant_operations WHERE grant_id = :grant",
    ],
)
async def test_grant_operations_cannot_be_broadened_or_erased(
    db_session, grant_storage, mutation
):
    await db_session.execute(GRANT_INSERT, grant_storage)
    await db_session.execute(OPERATION_INSERT, grant_storage)
    with pytest.raises(DBAPIError) as caught:
        async with db_session.begin_nested():
            await db_session.execute(text(mutation), grant_storage)
    assert getattr(caught.value.orig, "sqlstate", None) == "23514"


async def test_grant_operation_cannot_reference_another_install(
    db_session, grant_storage
):
    await db_session.execute(GRANT_INSERT, grant_storage)
    with pytest.raises(DBAPIError) as caught:
        async with db_session.begin_nested():
            await db_session.execute(
                OPERATION_INSERT, {**grant_storage, "solution": uuid4()}
            )
    assert getattr(caught.value.orig, "sqlstate", None) == "23503"
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
        "runtime_admissions",
        "runtime_deployment_artifacts",
        "runtime_execution_owners",
        "runtime_sessions",
        "runtime_starts",
        "runtime_report_receipts",
        "workflow_runtime_sdk_grants",
        "workflow_runtime_sdk_grant_operations",
    ],
)
async def test_alembic_runtime_tables_remain_registered_in_platform_metadata(
    db_session, table_name
):
    """Prevent a later autogenerate pass from dropping retained custody tables."""
    metadata = {
        model.__table__.name: model.__table__
        for model in (
            RuntimeAdmission,
            RuntimeDeploymentArtifact,
            RuntimeExecutionOwner,
            RuntimeSession,
            RuntimeStart,
            RuntimeReportReceipt,
            WorkflowRuntimeSDKGrant,
            WorkflowRuntimeSDKGrantOperation,
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


START_INSERT = text("""
    INSERT INTO runtime_starts
        (id, session_id, execution_id, owner_incarnation_id, workflow_attempt_id,
         start_message_id, input_sha256, context_sha256, started_at)
    VALUES (:start, :session, :execution, :owner, :attempt, :start_message,
            :source, :source, clock_timestamp())
""")
RECEIPT_INSERT = text("""
    INSERT INTO runtime_report_receipts
        (session_id, result_message_id, committed_start_id, start_message_id,
         raw_result_payload, result_sha256, decision_id, disposition, winner)
    VALUES (:session, :result_message, :start, :start_message, :payload, :digest,
            :decision, 'accepted', 'result')
""")


@pytest.fixture
async def report_storage(db_session, owner_session):
    """Storage facts only; payload is not an admitted runtime protocol Result."""
    facts = {
        **owner_session,
        **{
            key: uuid4()
            for key in (
                "start",
                "start_message",
                "result_message",
                "decision",
            )
        },
    }
    facts["payload"] = b'{ "schema_fixture": true }'
    facts["digest"] = sha256(facts["payload"]).hexdigest()
    await db_session.execute(SESSION_INSERT, facts)
    await db_session.execute(START_INSERT, facts)
    return facts


async def test_receipt_storage_retains_exact_unreencoded_payload(
    db_session, report_storage
):
    await db_session.execute(RECEIPT_INSERT, report_storage)
    row = (
        await db_session.execute(
            text("""
        SELECT raw_result_payload, result_sha256, committed_start_id
        FROM runtime_report_receipts WHERE session_id = :session
    """),
            report_storage,
        )
    ).one()
    assert tuple(row) == (
        report_storage["payload"],
        report_storage["digest"],
        report_storage["start"],
    )


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("digest", "0" * 64, "23514"),
        ("payload", b'{"schema_fixture":true}', "23514"),
        ("start", uuid4(), "23503"),
        ("start_message", uuid4(), "23503"),
        ("session", uuid4(), "23503"),
    ],
)
async def test_receipt_rejects_byte_drift_or_wrong_start_identity(
    db_session, report_storage, field, value, code
):
    with pytest.raises(DBAPIError) as caught:
        async with db_session.begin_nested():
            await db_session.execute(RECEIPT_INSERT, {**report_storage, field: value})
    assert getattr(caught.value.orig, "sqlstate", None) == code
    assert (
        await db_session.execute(
            text(
                "SELECT count(*) FROM runtime_report_receipts WHERE committed_start_id = :start"
            ),
            report_storage,
        )
    ).scalar_one() == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE runtime_starts SET input_sha256 = repeat('d', 64) WHERE id = :start",
        "DELETE FROM runtime_starts WHERE id = :start",
        "UPDATE runtime_report_receipts SET decision_id = gen_random_uuid() WHERE session_id = :session",
        "DELETE FROM runtime_report_receipts WHERE session_id = :session",
        """INSERT INTO runtime_report_receipts
        SELECT session_id, result_message_id, committed_start_id, start_message_id,
               raw_result_payload, result_sha256, gen_random_uuid(), disposition, winner, created_at
        FROM runtime_report_receipts WHERE session_id = :session
        ON CONFLICT (session_id, result_message_id) DO UPDATE SET decision_id = EXCLUDED.decision_id""",
    ],
)
async def test_start_and_receipt_custody_rejects_mutation_delete_and_conflict_update(
    db_session, report_storage, mutation
):
    await db_session.execute(RECEIPT_INSERT, report_storage)
    with pytest.raises(DBAPIError) as caught:
        async with db_session.begin_nested():
            await db_session.execute(text(mutation), report_storage)
    assert getattr(caught.value.orig, "sqlstate", None) == "23514"


async def test_a_session_cannot_receive_a_second_committed_start(
    db_session, report_storage
):
    with pytest.raises(DBAPIError) as caught:
        async with db_session.begin_nested():
            await db_session.execute(START_INSERT, {**report_storage, "start": uuid4()})
    assert getattr(caught.value.orig, "sqlstate", None) == "23505"


GRANT_INSERT = text("""
    INSERT INTO workflow_runtime_sdk_grants
        (id, schema_version, workflow_attempt_id, execution_id, attempt_number,
         claim_token_digest, worker_incarnation_id, supervisor_incarnation_id, runtime_session_id,
         started_at, issued_at, timeout_seconds, credential_deadline, initial_access_expires_at,
         caller_user_id, caller_organization_id, effective_organization_id, caller_email, caller_name,
         caller_admin, caller_provider, caller_external, caller_snapshot_digest, workflow_id,
         solution_install_id, source_kind, source_id, source_manifest_digest, source_resolution_digest,
         source_global_permission, source_digest, operations_digest, grant_digest,
         owner_incarnation_id, committed_start_id, start_message_id)
    VALUES (:grant, 'cred-p1/v1', :attempt, :execution, 1, :claim_digest, :worker, :supervisor, :session,
            :started, :issued, 10, :expires, :expires, :reviewer, :org, :org, 'schema@example.test',
            'Synthetic schema', 1, 0, 0, :source, :workflow, :solution, 'solution-deployment', :deployment,
            :manifest_digest, :resolution_digest, 0, :source, :source, :source, :owner, :start, :start_message)
""")


@pytest.fixture
async def grant_storage(db_session, report_storage):
    """Schema-test evidence only: no signing, token, live session or SDK operation."""
    from datetime import timedelta

    facts = {**report_storage, "grant": uuid4(), "org": PROVIDER_ORG_ID}
    started = (
        await db_session.execute(
            text("SELECT started_at FROM runtime_starts WHERE id = :start"), facts
        )
    ).scalar_one()
    facts.update(
        started=started,
        issued=started + timedelta(milliseconds=1),
        expires=started + timedelta(seconds=10),
        manifest_digest="sha256:" + "b" * 64,
        resolution_digest="sha256:" + "c" * 64,
        claim_digest=sha256(
            b"16:cred-p1/claim/v1,36:" + str(facts["claim"]).encode("ascii") + b","
        ).hexdigest(),
    )
    assert (
        await db_session.execute(
            text("SELECT claim_token_digest FROM runtime_sessions WHERE id = :session"),
            facts,
        )
    ).scalar_one() == facts["claim_digest"]
    return facts


async def test_private_grant_storage_binds_real_session_start_and_finite_expiry(
    db_session, grant_storage
):
    await db_session.execute(GRANT_INSERT, grant_storage)
    row = (
        await db_session.execute(
            text("""
        SELECT runtime_session_id, committed_start_id, initial_access_expires_at
        FROM workflow_runtime_sdk_grants WHERE id = :grant
    """),
            grant_storage,
        )
    ).one()
    assert tuple(row) == (
        grant_storage["session"],
        grant_storage["start"],
        grant_storage["expires"],
    )


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("claim_digest", "0" * 64, "23503"),
        ("worker", uuid4(), "23503"),
        ("supervisor", uuid4(), "23503"),
        ("start", uuid4(), "23503"),
        ("owner", uuid4(), "23503"),
        ("solution", uuid4(), "23503"),
    ],
)
async def test_grant_rejects_wrong_attempt_session_start_or_install_fence(
    db_session, grant_storage, field, value, code
):
    with pytest.raises(DBAPIError) as caught:
        async with db_session.begin_nested():
            await db_session.execute(GRANT_INSERT, {**grant_storage, field: value})
    assert getattr(caught.value.orig, "sqlstate", None) == code


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE workflow_runtime_sdk_grants SET initial_access_expires_at = initial_access_expires_at + interval '1 day' WHERE id = :grant",
        "UPDATE workflow_runtime_sdk_grants SET revoked_at = NULL, revocation_reason = NULL WHERE id = :grant",
        "DELETE FROM workflow_runtime_sdk_grants WHERE id = :grant",
    ],
)
async def test_grant_expiry_identity_and_revocation_cannot_be_upgraded_or_erased(
    db_session, grant_storage, mutation
):
    await db_session.execute(GRANT_INSERT, grant_storage)
    await db_session.execute(
        text("""
        UPDATE workflow_runtime_sdk_grants SET revoked_at = :issued, revocation_reason = 'session_closed'
        WHERE id = :grant
    """),
        grant_storage,
    )
    with pytest.raises(DBAPIError) as caught:
        async with db_session.begin_nested():
            await db_session.execute(text(mutation), grant_storage)
    assert getattr(caught.value.orig, "sqlstate", None) == "23514"


ADMISSION_INSERT = text("""
    INSERT INTO runtime_admissions
        (id, purpose, session_id, committed_start_id, start_message_id, grant_id,
         delivery_id, operations_digest, expires_at, provision_admission_id,
         provision_purpose, frontier_sha256, admitted_at)
    VALUES (:admission, :purpose, :session, :start, :start_message, :grant,
            :delivery, :source, :expires, :provision, :provision_purpose, :frontier, :issued)
""")


@pytest.fixture
async def provision_storage(db_session, grant_storage):
    """Retained metadata only; this does not authorize issuance or process spawn."""
    facts = {
        **grant_storage,
        "admission": uuid4(),
        "delivery": uuid4(),
        "purpose": "provision",
        "provision": None,
        "provision_purpose": None,
        "frontier": "d" * 64,
    }
    await db_session.execute(GRANT_INSERT, facts)
    await db_session.execute(ADMISSION_INSERT, facts)
    return facts


async def test_release_binds_exact_preceding_provision_and_finite_grant(
    db_session, provision_storage
):
    facts = {
        **provision_storage,
        "admission": uuid4(),
        "purpose": "release",
        "provision": provision_storage["admission"],
        "provision_purpose": "provision",
    }
    await db_session.execute(ADMISSION_INSERT, facts)
    row = (
        await db_session.execute(
            text("""
        SELECT purpose, provision_admission_id, delivery_id, expires_at
        FROM runtime_admissions WHERE id = :admission
    """),
            facts,
        )
    ).one()
    assert tuple(row) == (
        "release",
        provision_storage["admission"],
        facts["delivery"],
        facts["expires"],
    )


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("session", uuid4(), "23503"),
        ("start", uuid4(), "23503"),
        ("start_message", uuid4(), "23503"),
        ("grant", uuid4(), "23503"),
        ("source", "e" * 64, "23503"),
        ("delivery", uuid4(), "23503"),
        ("provision", uuid4(), "23503"),
        ("provision", None, "23514"),
        ("provision_purpose", None, "23514"),
        ("provision_purpose", "release", "23514"),
        ("purpose", "renewal", "23514"),
        ("frontier", "BAD", "23514"),
    ],
)
async def test_release_rejects_identity_or_purpose_drift(
    db_session, provision_storage, field, value, code
):
    facts = {
        **provision_storage,
        "admission": uuid4(),
        "purpose": "release",
        "provision": provision_storage["admission"],
        "provision_purpose": "provision",
        field: value,
    }
    with pytest.raises(DBAPIError) as caught:
        async with db_session.begin_nested():
            await db_session.execute(ADMISSION_INSERT, facts)
    assert getattr(caught.value.orig, "sqlstate", None) == code
    assert (
        await db_session.execute(
            text("SELECT count(*) FROM runtime_admissions WHERE session_id = :session"),
            provision_storage,
        )
    ).scalar_one() == 1


async def test_admission_cannot_extend_exact_grant_expiry(
    db_session, provision_storage
):
    from datetime import timedelta

    facts = {
        **provision_storage,
        "admission": uuid4(),
        "purpose": "release",
        "provision": provision_storage["admission"],
        "provision_purpose": "provision",
        "expires": provision_storage["expires"] + timedelta(seconds=1),
    }
    with pytest.raises(DBAPIError) as caught:
        async with db_session.begin_nested():
            await db_session.execute(ADMISSION_INSERT, facts)
    assert getattr(caught.value.orig, "sqlstate", None) == "23503"


async def test_admission_at_expiry_is_rejected(db_session, provision_storage):
    facts = {
        **provision_storage,
        "admission": uuid4(),
        "purpose": "release",
        "provision": provision_storage["admission"],
        "provision_purpose": "provision",
        "issued": provision_storage["expires"],
    }
    with pytest.raises(DBAPIError) as caught:
        async with db_session.begin_nested():
            await db_session.execute(ADMISSION_INSERT, facts)
    assert getattr(caught.value.orig, "sqlstate", None) == "23514"


async def test_second_provision_is_not_a_replay(db_session, provision_storage):
    with pytest.raises(DBAPIError) as caught:
        async with db_session.begin_nested():
            await db_session.execute(
                ADMISSION_INSERT, {**provision_storage, "admission": uuid4()}
            )
    assert getattr(caught.value.orig, "sqlstate", None) == "23505"


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE runtime_admissions SET delivery_id = gen_random_uuid() WHERE id = :admission",
        "UPDATE runtime_admissions SET frontier_sha256 = repeat('e',64) WHERE id = :admission",
        "DELETE FROM runtime_admissions WHERE id = :admission",
    ],
)
async def test_admission_evidence_remains_immutable(
    db_session, provision_storage, mutation
):
    with pytest.raises(DBAPIError, match="immutable"):
        async with db_session.begin_nested():
            await db_session.execute(text(mutation), provision_storage)
    assert (
        await db_session.execute(
            text("SELECT count(*) FROM runtime_admissions WHERE id = :admission"),
            provision_storage,
        )
    ).scalar_one() == 1


async def test_admission_rollback_leaves_no_release_evidence(
    db_session, provision_storage
):
    facts = {
        **provision_storage,
        "admission": uuid4(),
        "purpose": "release",
        "provision": provision_storage["admission"],
        "provision_purpose": "provision",
    }
    nested = await db_session.begin_nested()
    await db_session.execute(ADMISSION_INSERT, facts)
    await nested.rollback()
    assert (
        await db_session.execute(
            text("SELECT count(*) FROM runtime_admissions WHERE id = :admission"), facts
        )
    ).scalar_one() == 0
    assert (
        await db_session.execute(
            text("SELECT count(*) FROM runtime_admissions WHERE id = :admission"),
            provision_storage,
        )
    ).scalar_one() == 1


async def test_conflict_update_cannot_rebind_retained_delivery(
    db_session, provision_storage
):
    statement = text(
        str(ADMISSION_INSERT)
        + " ON CONFLICT (session_id,purpose) DO UPDATE SET delivery_id = EXCLUDED.delivery_id"
    )
    with pytest.raises(DBAPIError, match="immutable"):
        async with db_session.begin_nested():
            await db_session.execute(
                statement,
                {**provision_storage, "admission": uuid4(), "delivery": uuid4()},
            )
    assert (
        await db_session.execute(
            text("SELECT delivery_id FROM runtime_admissions WHERE id = :admission"),
            provision_storage,
        )
    ).scalar_one() == provision_storage["delivery"]
