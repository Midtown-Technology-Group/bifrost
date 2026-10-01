"""Private foundation proof against the migrated disposable PostgreSQL lane.

The module owns a committed synthetic installation/history cohort. History is
retained until stack teardown because accepted deployments must not be deleted.
No fixture bypasses deployment guards or writes source blobs. These tests do not
prove source-byte access, public HTTP behavior, supervisor hooks or SDK parity.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
import pytest_asyncio
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session
from src.core.runtime_sdk_credentials import (
    AcceptedManifestIdentity,
    AuthorizedCallerSnapshot,
    CommittedWorkflowStart,
    RuntimeSDKDenied,
    SDKOperation,
    SelectedSDKPolicy,
    decode_runtime_sdk_access,
    epoch_us,
    sign_runtime_sdk_token,
)
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from src.models.orm.organizations import Organization
from src.models.orm.runtime_sdk_grants import (
    WorkflowRuntimeSDKGrant,
    WorkflowRuntimeSDKGrantOperation,
)
from src.models.orm.solution_deployments import SolutionDeployment
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.models.orm.workflows import Workflow
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.runtime_sdk_grants import (
    create_workflow_runtime_sdk_grant,
    issue_workflow_runtime_sdk_token,
    load_runtime_sdk_authority,
    renew_workflow_runtime_sdk_token,
    revoke_workflow_runtime_sdk_grant,
)
from src.services.solutions.deployment_manifest import (
    CompiledDeploymentManifest,
    DeploymentResolutionMap,
    DeploymentSource,
    RuntimeEntityDefinition,
    RuntimeSourceResolution,
    canonical_json,
    sha256_digest,
)
from src.services.solutions.deployment_runtime import _pin_from_deployment


@dataclass(frozen=True)
class Cohort:
    factory: async_sessionmaker[AsyncSession]
    source: AcceptedManifestIdentity
    organization_id: UUID
    provider_organization_id: UUID
    users: tuple[UUID, UUID, UUID, UUID]
    workflows: tuple[UUID, UUID]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def credential_cohort(async_session_factory, record_testsuite_property):
    org, provider_org, install, deployment_id = (uuid4() for _ in range(4))
    ordinary, provider, admin, finite, renewable, foreign_org, foreign_user = (
        uuid4() for _ in range(7)
    )
    suffix = install.hex
    async with async_session_factory() as db, db.begin():
        db.add_all(
            [
                Organization(
                    id=org, name=f"cred-p1-org-{suffix}", created_by="cred-p1-test"
                ),
                Organization(
                    id=provider_org,
                    name=f"cred-p1-provider-{suffix}",
                    created_by="cred-p1-test",
                    is_provider=True,
                ),
                Organization(
                    id=foreign_org,
                    name=f"cred-p1-foreign-{suffix}",
                    created_by="cred-p1-test",
                    is_provider=False,
                ),
            ]
        )
        await db.flush()
        db.add_all(
            [
                User(
                    id=ordinary,
                    email=f"ordinary-{suffix}@example.invalid",
                    name="Ordinary",
                    organization_id=org,
                    is_superuser=False,
                ),
                User(
                    id=provider,
                    email=f"provider-{suffix}@example.invalid",
                    name="Provider",
                    organization_id=provider_org,
                    is_superuser=False,
                ),
                User(
                    id=admin,
                    email=f"admin-{suffix}@example.invalid",
                    name="Admin",
                    organization_id=org,
                    is_superuser=True,
                ),
                User(
                    id=foreign_user,
                    email=f"foreign-{suffix}@example.invalid",
                    name="Foreign ordinary",
                    organization_id=foreign_org,
                    is_superuser=False,
                ),
                Solution(
                    id=install,
                    slug=f"cred-p1-{suffix}",
                    name="Credential foundation fixture",
                    organization_id=None,
                    allow_outbound_access=False,
                ),
            ]
        )
        await db.flush()
        db.add_all(
            [
                Workflow(
                    id=finite,
                    name=f"finite-{suffix}",
                    function_name="run",
                    path="finite.py",
                    solution_id=install,
                    timeout_seconds=30,
                ),
                Workflow(
                    id=renewable,
                    name=f"renewable-{suffix}",
                    function_name="run",
                    path="renewable.py",
                    solution_id=install,
                    timeout_seconds=0,
                ),
            ]
        )
    prefix = f"_solutions/{install}/{deployment_id}/"
    entities = {}
    sources = {}
    for workflow_id, path, timeout in (
        (finite, "finite.py", 30),
        (renewable, "renewable.py", 0),
    ):
        ref = f"{path}::run"
        content_hash = sha256_digest(b"async def run():\n    return None\n")
        sources[path] = RuntimeSourceResolution(
            object_key=prefix + path, content_hash=content_hash
        )
        entities[ref] = RuntimeEntityDefinition(
            portable_ref=ref,
            resolved_id=workflow_id,
            source_ref=path,
            source_hash=content_hash,
            definition={
                "name": ref,
                "function_name": "run",
                "path": path,
                "timeout_seconds": timeout,
                "type": "workflow",
            },
        )
    resolution = DeploymentResolutionMap(workflows=entities, sources=sources)
    resolution_hash = sha256_digest(canonical_json(resolution))
    manifest = CompiledDeploymentManifest(
        solution_id=install,
        deployment_id=deployment_id,
        bundle_hash=sha256_digest(b"synthetic-credential-bundle"),
        resolution_map_hash=resolution_hash,
        source=DeploymentSource(
            artifact_key=prefix + "bundle.zip", runtime_prefix=prefix
        ),
        workflows=entities,
    )
    async with async_session_factory() as db, db.begin():
        repository = SolutionDeploymentRepository(db)
        deployment = SolutionDeployment(
            id=deployment_id,
            solution_id=install,
            organization_id=None,
            state="draft",
            bundle_hash=manifest.bundle_hash,
            compiled_manifest=manifest.model_dump(mode="json", exclude_none=True),
            compiled_manifest_hash=manifest.content_hash(),
            resolution_map=resolution.model_dump(mode="json", exclude_none=True),
            resolution_map_hash=resolution_hash,
            source_artifact_key=manifest.source.artifact_key,
            runtime_storage_prefix=prefix,
            created_by=admin,
            dependencies=[],
        )
        await repository.create(deployment)
        previous = "draft"
        for state in ("building", "validated", "ready", "activating", "active"):
            await repository.transition(
                deployment_id, None, expected_state=previous, new_state=state
            )
            previous = state
        solution = await db.get(Solution, install)
        solution.active_deployment_id = deployment_id
    # Exact disposable ownership is recorded by the authorized test lane, never
    # credentials or source bodies. Retain all parents/history until teardown.
    for key, value in {
        "installation": install,
        "deployment": deployment_id,
        "ordinary_org": org,
        "provider_org": provider_org,
        "ordinary_user": ordinary,
        "provider_user": provider,
        "admin_user": admin,
        "foreign_org": foreign_org,
        "foreign_user": foreign_user,
        "finite_workflow": finite,
        "renewable_workflow": renewable,
    }.items():
        record_testsuite_property(f"cred_p1_owned_{key}", str(value))
    yield Cohort(
        factory=async_session_factory,
        source=AcceptedManifestIdentity(
            source_id=deployment_id,
            solution_install_id=install,
            source_manifest_digest=manifest.content_hash(),
            source_resolution_digest=resolution_hash,
            source_global_permission=False,
        ),
        organization_id=org,
        provider_organization_id=provider_org,
        users=(ordinary, provider, admin, foreign_user),
        workflows=(finite, renewable),
    )


@dataclass(frozen=True)
class Work:
    cohort: Cohort
    start: CommittedWorkflowStart
    caller: AuthorizedCallerSnapshot
    policy: SelectedSDKPolicy

    async def provision(self, **changes):
        return await create_workflow_runtime_sdk_grant(
            self.cohort.factory,
            start=changes.get("start", self.start),
            caller=changes.get("caller", self.caller),
            policy=changes.get("policy", self.policy),
            source=changes.get("source", self.cohort.source),
        )


@pytest_asyncio.fixture
async def make_work(credential_cohort):
    cohort = credential_cohort
    executions = []

    async def make(*, timeout=30, actor=0, committed_start=True):
        now = datetime.now(UTC)
        execution_id, attempt_id, claim, worker, supervisor, session = (
            uuid4() for _ in range(6)
        )
        workflow_id = cohort.workflows[0 if timeout else 1]
        async with cohort.factory() as db, db.begin():
            source = await db.get(SolutionDeployment, cohort.source.source_id)
            solution = await db.get(Solution, cohort.source.solution_install_id)
            user = await db.get(User, cohort.users[actor])
            organization = await db.get(Organization, user.organization_id)
            evidence = _pin_from_deployment(
                workflow_id, solution, source, allow_superseded=True
            ).queue_evidence()
            caller = AuthorizedCallerSnapshot(
                caller_user_id=user.id,
                caller_organization_id=user.organization_id,
                effective_organization_id=cohort.organization_id,
                caller_email=user.email,
                caller_name=user.name,
                caller_admin=user.is_superuser,
                caller_provider=organization.is_provider,
                caller_external=user.is_external,
                roles=(),
            )
            db.add(
                Execution(
                    id=execution_id,
                    workflow_id=workflow_id,
                    workflow_name=evidence["workflow_name"],
                    executed_by=user.id,
                    executed_by_name=user.name,
                    organization_id=cohort.organization_id,
                    status=ExecutionStatus.RUNNING,
                    started_at=now,
                    runtime_mode="deployment-v1",
                    solution_deployment_id=source.id,
                    runtime_evidence=evidence,
                    runtime_evidence_hash=sha256_digest(canonical_json(evidence)),
                )
            )
            await db.flush()
            db.add(
                WorkflowExecutionAttempt(
                    id=attempt_id,
                    execution_id=execution_id,
                    attempt_number=1,
                    claim_token=claim,
                    status="running" if committed_start else "claimed",
                    phase="execution" if committed_start else "claim",
                    worker_id="cred-p1-synthetic-worker",
                    worker_incarnation_id=worker,
                    published_at=now - timedelta(seconds=2),
                    claimed_at=now - timedelta(seconds=1),
                    started_at=now if committed_start else None,
                )
            )
        executions.append(execution_id)
        expiry = epoch_us(now + timedelta(seconds=timeout + 290 if timeout else 590))
        start = CommittedWorkflowStart(
            execution_id=execution_id,
            workflow_attempt_id=attempt_id,
            attempt_number=1,
            private_claim_token=claim,
            worker_incarnation_id=worker,
            supervisor_incarnation_id=supervisor,
            runtime_session_id=session,
            committed_started_at_us=epoch_us(now),
            timeout_seconds=timeout,
            predeclared_credential_deadline_us=expiry if timeout else None,
            predeclared_initial_access_expires_at_us=expiry,
        )
        policy = SelectedSDKPolicy(
            operations=tuple(
                SDKOperation(
                    operation=operation,
                    integration_name="Synthetic integration",
                    scope_kind="organization",
                    scope_organization_id=cohort.organization_id,
                    resolved_organization_id=cohort.organization_id,
                    solution_install_id=cohort.source.solution_install_id
                    if operation == "integration-get"
                    else None,
                )
                for operation in ("integration-get", "mapping-get")
            )
        )
        return Work(cohort, start, caller, policy)

    yield make
    # Only owned executions and their cascade-owned attempts/grants/operations.
    # No accepted deployment, installation, managed workflow or parent deletion.
    async with cohort.factory() as db, db.begin():
        await db.execute(delete(Execution).where(Execution.id.in_(executions)))


async def _bundle(work):
    reference = await work.provision()
    return reference, await issue_workflow_runtime_sdk_token(
        work.cohort.factory, reference=reference
    )


@pytest.mark.asyncio
async def test_committed_exact_grant_idempotence_and_predeclared_deadline(make_work):
    work = await make_work()
    first, second = await asyncio.gather(work.provision(), work.provision())
    assert first == second
    bundle = await issue_workflow_runtime_sdk_token(
        work.cohort.factory, reference=first
    )
    authority = await load_runtime_sdk_authority(
        work.cohort.factory, token=bundle.access_token
    )
    claims = decode_runtime_sdk_access(bundle.access_token)
    assert (
        claims.exp == work.start.predeclared_initial_access_expires_at_us // 1_000_000
    )
    assert (
        authority.snapshot.source_manifest_digest
        == work.cohort.source.source_manifest_digest
    )
    assert authority.policy == work.policy
    assert authority.private_claim_token == work.start.private_claim_token
    assert str(work.start.private_claim_token) not in repr(authority)
    async with work.cohort.factory() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(WorkflowRuntimeSDKGrant)
                .where(
                    WorkflowRuntimeSDKGrant.workflow_attempt_id
                    == work.start.workflow_attempt_id
                )
            )
            == 1
        )
    with pytest.raises(RuntimeSDKDenied):
        await work.provision(
            start=work.start.model_copy(update={"runtime_session_id": uuid4()})
        )
    with pytest.raises(RuntimeSDKDenied):
        await work.provision(
            caller=work.caller.model_copy(update={"caller_name": "Changed authority"})
        )
    with pytest.raises(RuntimeSDKDenied):
        await renew_workflow_runtime_sdk_token(
            work.cohort.factory, token=bundle.refresh_token
        )


@pytest.mark.asyncio
async def test_flushed_only_start_is_not_committed_admission(make_work):
    work = await make_work(committed_start=False)
    async with work.cohort.factory() as uncommitted:
        attempt = await uncommitted.get(
            WorkflowExecutionAttempt, work.start.workflow_attempt_id
        )
        attempt.status, attempt.phase = "running", "execution"
        attempt.started_at = datetime.fromtimestamp(
            work.start.committed_started_at_us // 1_000_000, UTC
        ).replace(microsecond=work.start.committed_started_at_us % 1_000_000)
        await uncommitted.flush()
        # Plain committed MVCC reads reject before acquiring the writer's lock.
        with pytest.raises(RuntimeSDKDenied):
            await asyncio.wait_for(work.provision(), timeout=5)
        await uncommitted.rollback()
    async with work.cohort.factory() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(WorkflowRuntimeSDKGrant)
                .where(
                    WorkflowRuntimeSDKGrant.workflow_attempt_id
                    == work.start.workflow_attempt_id
                )
            )
            == 0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("contended", ["execution", "deployment", "solution"])
async def test_contended_later_lock_denies_and_releases_attempt(make_work, contended):
    work = await make_work()
    model, row_id = {
        "execution": (Execution, work.start.execution_id),
        "deployment": (SolutionDeployment, work.cohort.source.source_id),
        "solution": (Solution, work.cohort.source.solution_install_id),
    }[contended]
    # A separate real connection holds the conflicting lock before provisioning
    # starts. No sleeps, retries, altered lock timeout or deadlock recovery.
    async with work.cohort.factory() as owner, owner.begin():
        assert (
            await owner.scalar(
                select(model).where(model.id == row_id).with_for_update()
            )
            is not None
        )
        with pytest.raises(RuntimeSDKDenied):
            await asyncio.wait_for(work.provision(), timeout=5)
        # NOWAIT proves provisioning has released its attempt lock after denial.
        attempt = await owner.scalar(
            select(WorkflowExecutionAttempt)
            .where(WorkflowExecutionAttempt.id == work.start.workflow_attempt_id)
            .with_for_update(nowait=True)
        )
        assert attempt is not None
        completed_at = datetime.now(UTC)
        attempt.status, attempt.phase, attempt.completed_at = (
            "failed",
            "terminal",
            completed_at,
        )
        execution = await owner.get(Execution, work.start.execution_id)
        execution.status, execution.completed_at = ExecutionStatus.FAILED, completed_at
    async with work.cohort.factory() as verification:
        assert (
            await verification.scalar(
                select(func.count())
                .select_from(WorkflowRuntimeSDKGrant)
                .where(
                    WorkflowRuntimeSDKGrant.workflow_attempt_id
                    == work.start.workflow_attempt_id
                )
            )
            == 0
        )
        assert (
            await verification.scalar(
                select(func.count())
                .select_from(WorkflowRuntimeSDKGrantOperation)
                .join(
                    WorkflowRuntimeSDKGrant,
                    WorkflowRuntimeSDKGrantOperation.grant_id
                    == WorkflowRuntimeSDKGrant.id,
                )
                .where(
                    WorkflowRuntimeSDKGrant.workflow_attempt_id
                    == work.start.workflow_attempt_id
                )
            )
            == 0
        )
        attempt = await verification.get(
            WorkflowExecutionAttempt, work.start.workflow_attempt_id
        )
        execution = await verification.get(Execution, work.start.execution_id)
        assert attempt.status == "failed" and attempt.completed_at is not None
        assert execution.status == ExecutionStatus.FAILED


@pytest.mark.asyncio
async def test_failed_commit_never_exposes_partial_grant(make_work, async_engine):
    work = await make_work()

    class FailingCommitSession(Session):
        pass

    def reject_commit(_session):
        raise SQLAlchemyError("synthetic commit failure")

    event.listen(FailingCommitSession, "before_commit", reject_commit)
    factory = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        sync_session_class=FailingCommitSession,
        expire_on_commit=False,
    )
    try:
        with pytest.raises(RuntimeSDKDenied):
            await create_workflow_runtime_sdk_grant(
                factory,
                start=work.start,
                caller=work.caller,
                policy=work.policy,
                source=work.cohort.source,
            )
    finally:
        event.remove(FailingCommitSession, "before_commit", reject_commit)
    async with work.cohort.factory() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(WorkflowRuntimeSDKGrant)
                .where(
                    WorkflowRuntimeSDKGrant.workflow_attempt_id
                    == work.start.workflow_attempt_id
                )
            )
            == 0
        )


@pytest.mark.asyncio
async def test_initial_expiry_cannot_reset_or_signed_token_expand_window(make_work):
    for timeout in (0, 30):
        work = await make_work(timeout=timeout)
        reference, bundle = await _bundle(work)
        past_deadline = datetime.fromtimestamp(
            work.start.predeclared_initial_access_expires_at_us // 1_000_000 + 1, UTC
        )
        with pytest.raises(RuntimeSDKDenied):
            await issue_workflow_runtime_sdk_token(
                work.cohort.factory, reference=reference, now=past_deadline
            )
        # A correctly signed reference still cannot exceed stored authority.
        expanded = sign_runtime_sdk_token(
            reference,
            issued_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(seconds=900),
        )
        with pytest.raises(RuntimeSDKDenied):
            await load_runtime_sdk_authority(
                work.cohort.factory, token=expanded.access_token
            )
        with pytest.raises(RuntimeSDKDenied):
            await create_workflow_runtime_sdk_grant(
                work.cohort.factory,
                start=work.start,
                caller=work.caller,
                policy=work.policy,
                source=work.cohort.source,
                now=past_deadline,
            )
        assert (
            decode_runtime_sdk_access(bundle.access_token).exp
            == work.start.predeclared_initial_access_expires_at_us // 1_000_000
        )


@pytest.mark.asyncio
async def test_closed_policy_same_org_and_authoritative_global_pairs(make_work):
    ordinary = await make_work()
    global_policy = SelectedSDKPolicy(
        operations=(
            SDKOperation(
                operation="integration-get",
                integration_name="Synthetic integration",
                scope_kind="global",
                scope_organization_id=None,
                resolved_organization_id=None,
                solution_install_id=ordinary.cohort.source.solution_install_id,
            ),
        )
    )
    with pytest.raises(RuntimeSDKDenied):
        await ordinary.provision(policy=global_policy)
    cross_policy = SelectedSDKPolicy(
        operations=(
            SDKOperation(
                operation="integration-get",
                integration_name="Synthetic integration",
                scope_kind="organization",
                scope_organization_id=ordinary.cohort.provider_organization_id,
                resolved_organization_id=ordinary.cohort.provider_organization_id,
                solution_install_id=None,
            ),
        )
    )
    with pytest.raises(RuntimeSDKDenied):
        await ordinary.provision(policy=cross_policy)
    for actor in (1, 2):
        trusted = await make_work(actor=actor)
        reference = await trusted.provision(policy=global_policy)
        bundle = await issue_workflow_runtime_sdk_token(
            trusted.cohort.factory, reference=reference
        )
        assert (
            await load_runtime_sdk_authority(
                trusted.cohort.factory, token=bundle.access_token
            )
        ).policy == global_policy
    # A genuine original/effective mismatch is conservatively unsupported even
    # with no operations; no synthetic "approved" flag grants an entitlement.
    foreign = await make_work(actor=3)
    assert (
        foreign.caller.caller_organization_id
        != foreign.caller.effective_organization_id
    )
    assert not foreign.caller.caller_admin and not foreign.caller.caller_provider
    with pytest.raises(RuntimeSDKDenied):
        await foreign.provision(policy=SelectedSDKPolicy(operations=()))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "private_claim_token",
        "worker_incarnation_id",
        "execution_id",
        "workflow_attempt_id",
        "committed_started_at_us",
        "attempt_number",
    ],
)
async def test_wrong_trusted_start_binding_denied(make_work, field):
    work = await make_work()
    value = getattr(work.start, field)
    changed = value + 1 if type(value) is int else uuid4()
    with pytest.raises(RuntimeSDKDenied):
        await work.provision(start=work.start.model_copy(update={field: changed}))


@pytest.mark.asyncio
async def test_source_hash_and_source_identity_mismatch_denied(make_work):
    work = await make_work()
    for changes in (
        {"source_id": uuid4()},
        {"source_manifest_digest": "sha256:" + "f" * 64},
        {"source_resolution_digest": "sha256:" + "f" * 64},
        {"source_global_permission": True},
    ):
        with pytest.raises(RuntimeSDKDenied):
            await work.provision(source=work.cohort.source.model_copy(update=changes))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    ["claim", "worker", "terminal", "organization", "caller", "grant", "operation"],
)
async def test_fresh_lookup_denies_committed_fence_or_authority_drift(
    make_work, mutation
):
    work = await make_work()
    reference, bundle = await _bundle(work)
    async with work.cohort.factory() as db, db.begin():
        if mutation in {"claim", "worker", "terminal"}:
            values = (
                {"claim_token": uuid4()}
                if mutation == "claim"
                else {"worker_incarnation_id": uuid4()}
                if mutation == "worker"
                else {
                    "status": "succeeded",
                    "phase": "terminal",
                    "completed_at": datetime.now(UTC),
                }
            )
            await db.execute(
                update(WorkflowExecutionAttempt)
                .where(WorkflowExecutionAttempt.id == work.start.workflow_attempt_id)
                .values(**values)
            )
        elif mutation in {"organization", "caller"}:
            values = (
                {"organization_id": work.cohort.provider_organization_id}
                if mutation == "organization"
                else {"executed_by": work.cohort.users[1]}
            )
            await db.execute(
                update(Execution)
                .where(Execution.id == work.start.execution_id)
                .values(**values)
            )
        elif mutation == "grant":
            await db.execute(
                update(WorkflowRuntimeSDKGrant)
                .where(WorkflowRuntimeSDKGrant.id == reference.grant_id)
                .values(caller_admin=1)
            )
        else:
            await db.execute(
                update(WorkflowRuntimeSDKGrantOperation)
                .where(
                    WorkflowRuntimeSDKGrantOperation.grant_id == reference.grant_id,
                    WorkflowRuntimeSDKGrantOperation.ordinal == 0,
                )
                .values(integration_name="Changed integration")
            )
    with pytest.raises(RuntimeSDKDenied):
        await load_runtime_sdk_authority(work.cohort.factory, token=bundle.access_token)


@pytest.mark.asyncio
async def test_request_bound_revocation_is_session_specific_and_lifecycle_free(
    make_work,
):
    work = await make_work(timeout=0)
    reference, bundle = await _bundle(work)
    kwargs = {
        "grant_id": reference.grant_id,
        "supervisor_incarnation_id": work.start.supervisor_incarnation_id,
        "runtime_session_id": work.start.runtime_session_id,
        "reason": "session_closed",
    }
    with pytest.raises(RuntimeSDKDenied):
        await revoke_workflow_runtime_sdk_grant(
            work.cohort.factory, **{**kwargs, "runtime_session_id": uuid4()}
        )
    admitted = await load_runtime_sdk_authority(
        work.cohort.factory, token=bundle.access_token
    )
    assert await revoke_workflow_runtime_sdk_grant(work.cohort.factory, **kwargs)
    assert not await revoke_workflow_runtime_sdk_grant(work.cohort.factory, **kwargs)
    assert admitted.snapshot.id == reference.grant_id  # No locks across effects.
    for helper in (load_runtime_sdk_authority, renew_workflow_runtime_sdk_token):
        with pytest.raises(RuntimeSDKDenied):
            await helper(work.cohort.factory, token=bundle.access_token)
    async with work.cohort.factory() as db:
        attempt = await db.get(WorkflowExecutionAttempt, work.start.workflow_attempt_id)
        assert (
            attempt.status,
            attempt.phase,
            attempt.completed_at,
            attempt.claim_token,
        ) == ("running", "execution", None, work.start.private_claim_token)


@pytest.mark.asyncio
async def test_no_timeout_expired_renewal_never_upgrades_authority(
    make_work, monkeypatch
):
    work = await make_work(timeout=0)
    reference, bundle = await _bundle(work)
    initial = decode_runtime_sdk_access(bundle.access_token)
    future = datetime.fromtimestamp(initial.exp + 1, UTC)

    class VerificationClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return future if tz else future.replace(tzinfo=None)

    monkeypatch.setattr(jwt.api_jwt, "datetime", VerificationClock)
    with pytest.raises(RuntimeSDKDenied):
        await load_runtime_sdk_authority(work.cohort.factory, token=bundle.access_token)
    renewed = await renew_workflow_runtime_sdk_token(
        work.cohort.factory, token=bundle.refresh_token, now=future
    )
    claims = decode_runtime_sdk_access(renewed.access_token)
    assert (
        claims.sub,
        claims.grant_digest,
        claims.aud,
        claims.purpose,
        claims.type,
    ) == (initial.sub, initial.grant_digest, initial.aud, initial.purpose, "access")
    assert claims.exp == int(future.timestamp()) + 600 and claims.jti != initial.jti
    assert (
        await load_runtime_sdk_authority(
            work.cohort.factory, token=renewed.access_token
        )
    ).policy == work.policy
    assert claims.sub == str(reference.grant_id)


@pytest.mark.asyncio
async def test_session_uniqueness_database_enforced(make_work):
    first, second = await make_work(), await make_work()
    await first.provision()
    with pytest.raises(RuntimeSDKDenied):
        await second.provision(
            start=second.start.model_copy(
                update={"runtime_session_id": first.start.runtime_session_id}
            )
        )
    async with first.cohort.factory() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(WorkflowRuntimeSDKGrant)
                .where(
                    WorkflowRuntimeSDKGrant.workflow_attempt_id
                    == second.start.workflow_attempt_id
                )
            )
            == 0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    ["ordinal", "duplicate_operation", "byte_bound", "nullable", "revocation_pair"],
)
async def test_migrated_database_enforces_cardinality_bounds_and_shape(
    make_work, failure
):
    work = await make_work()
    reference = await work.provision(policy=SelectedSDKPolicy(operations=()))
    async with work.cohort.factory() as db:
        with pytest.raises(IntegrityError):
            if failure in {"nullable", "revocation_pair"}:
                values = (
                    {"caller_name": None}
                    if failure == "nullable"
                    else {"revoked_at": datetime.now(UTC), "revocation_reason": None}
                )
                await db.execute(
                    update(WorkflowRuntimeSDKGrant)
                    .where(WorkflowRuntimeSDKGrant.id == reference.grant_id)
                    .values(**values)
                )
            else:
                db.add(
                    WorkflowRuntimeSDKGrantOperation(
                        grant_id=reference.grant_id,
                        ordinal=0,
                        operation="integration-get",
                        integration_name="Synthetic",
                        scope_kind="global",
                        scope_organization_id=None,
                        resolved_organization_id=None,
                        solution_install_id=None,
                    )
                )
                await db.flush()
                db.add(
                    WorkflowRuntimeSDKGrantOperation(
                        grant_id=reference.grant_id,
                        ordinal=2 if failure == "ordinal" else 1,
                        operation="integration-get"
                        if failure == "duplicate_operation"
                        else "mapping-get",
                        integration_name="é" * 128
                        if failure == "byte_bound"
                        else "Synthetic",
                        scope_kind="global",
                        scope_organization_id=None,
                        resolved_organization_id=None,
                        solution_install_id=None,
                    )
                )
                await db.flush()
        await db.rollback()


@pytest.mark.asyncio
async def test_deleted_attempt_cascades_private_grant_without_source_deletion(
    make_work,
):
    work = await make_work()
    reference, bundle = await _bundle(work)
    async with work.cohort.factory() as db, db.begin():
        await db.execute(
            delete(WorkflowExecutionAttempt).where(
                WorkflowExecutionAttempt.id == work.start.workflow_attempt_id
            )
        )
    with pytest.raises(RuntimeSDKDenied):
        await load_runtime_sdk_authority(work.cohort.factory, token=bundle.access_token)
    async with work.cohort.factory() as db:
        assert await db.get(WorkflowRuntimeSDKGrant, reference.grant_id) is None
        assert (
            await db.get(SolutionDeployment, work.cohort.source.source_id) is not None
        )


@pytest.mark.asyncio
async def test_store_failure_is_closed_without_cached_authority(
    make_work, async_engine
):
    work = await make_work()
    _, bundle = await _bundle(work)

    def reject_query(*_args):
        raise SQLAlchemyError("synthetic store outage")

    event.listen(async_engine.sync_engine, "before_cursor_execute", reject_query)
    try:
        with pytest.raises(RuntimeSDKDenied) as error:
            await load_runtime_sdk_authority(
                work.cohort.factory, token=bundle.access_token
            )
        assert bundle.access_token not in str(error.value)
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", reject_query)
