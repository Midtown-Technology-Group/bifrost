"""Trusted private CRED-P1 storage/issuance, with independent committed reads.

The supplied factory is the trusted platform session factory, never a runtime
DSN/session. No consumer, router or runtime calls these helpers in this packet.
Active Solution/executable deployment checks are conservative private-profile
eligibility. Deactivation during an existing run and general original/effective
caller parity need separate admission decisions before runtime integration.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.runtime_sdk_credentials import (
    GRANT_FIELDS,
    GRANT_SCHEMA_VERSION,
    RENEWAL_SECONDS,
    AcceptedManifestIdentity,
    AuthorizedCallerSnapshot,
    CommittedWorkflowStart,
    CredentialBundle,
    GrantReference,
    GrantSnapshot,
    RuntimeSDKDenied,
    RuntimeSDKTokenClaims,
    SDKOperation,
    SelectedSDKPolicy,
    _PrivateRecord,
    caller_digest,
    claim_digest,
    decode_runtime_sdk_access,
    epoch_us,
    grant_digest,
    operations_digest,
    sign_runtime_sdk_token,
    source_digest,
    utc_from_us,
)
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from src.models.orm.runtime_sdk_grants import (
    WorkflowRuntimeSDKGrant,
    WorkflowRuntimeSDKGrantOperation,
)
from src.models.orm.solution_deployments import SolutionDeployment
from src.models.orm.solutions import Solution
from src.services.solutions.deployment_manifest import validate_runtime_closure
from src.services.solutions.deployment_runtime import (
    DeploymentRuntimeError,
    _pin_from_deployment,
    verify_runtime_evidence,
)


class ValidatedRuntimeSDKAuthority(_PrivateRecord):
    snapshot: GrantSnapshot
    policy: SelectedSDKPolicy
    # Private API fence, never a bearer field/public response.
    private_claim_token: UUID = Field(repr=False)


def _now(value: datetime | None) -> datetime:
    result = value if value is not None else datetime.now(UTC)
    epoch_us(result)
    return result


def _snapshot(row: WorkflowRuntimeSDKGrant) -> GrantSnapshot:
    return GrantSnapshot(**{name: getattr(row, name) for name in GRANT_FIELDS})


def _source(snapshot: GrantSnapshot) -> AcceptedManifestIdentity:
    return AcceptedManifestIdentity(
        source_id=snapshot.source_id,
        solution_install_id=snapshot.solution_install_id,
        source_manifest_digest=snapshot.source_manifest_digest,
        source_resolution_digest=snapshot.source_resolution_digest,
        source_global_permission=bool(snapshot.source_global_permission),
    )


def _check_policy(
    policy: SelectedSDKPolicy,
    caller: AuthorizedCallerSnapshot,
    source: AcceptedManifestIdentity,
) -> None:
    # Conservative first-packet eligibility, not the general SDK scope rule.
    # Real non-bypass effective-default parity needs trusted admission evidence.
    if not (caller.caller_admin or caller.caller_provider) and (
        caller.caller_organization_id is None
        or caller.caller_organization_id != caller.effective_organization_id
    ):
        raise RuntimeSDKDenied("original/effective caller entitlement is unsupported")
    for operation in policy.operations:
        if operation.solution_install_id not in {None, source.solution_install_id}:
            raise RuntimeSDKDenied("operation install differs from accepted source")
        if (
            operation.scope_kind == "default"
            and operation.resolved_organization_id != caller.effective_organization_id
        ):
            raise RuntimeSDKDenied("operation default scope differs from execution")
        bypass = caller.caller_admin or caller.caller_provider
        if (
            not bypass
            and operation.resolved_organization_id != caller.caller_organization_id
        ):
            raise RuntimeSDKDenied("operation exceeds original caller scope")
        if not bypass and operation.scope_kind == "global":
            raise RuntimeSDKDenied("global operation requires trusted caller authority")


async def _read_work(
    db: AsyncSession,
    *,
    attempt_id: UUID,
    execution_id: UUID,
    source: AcceptedManifestIdentity,
    workflow_id: UUID | None,
    effective_org: UUID | None,
    lock: bool = False,
) -> tuple[WorkflowExecutionAttempt, Execution]:
    statement = select(WorkflowExecutionAttempt).where(
        WorkflowExecutionAttempt.id == attempt_id
    )
    if lock:
        # Serialize provisioning for one attempt, not any future vendor effect.
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )
    attempt = await db.scalar(statement)
    execution_statement = select(Execution).where(Execution.id == execution_id)
    deployment_statement = select(SolutionDeployment).where(
        SolutionDeployment.id == source.source_id
    )
    solution_statement = select(Solution).where(
        Solution.id == source.solution_install_id
    )
    if lock:
        # Existing terminal writers use both execution-first and attempt-first
        # orders. Never wait while holding the attempt for a later shared lock;
        # contention denies and rolls back this entire private transaction.
        execution_statement = execution_statement.with_for_update(
            read=True, nowait=True
        ).execution_options(populate_existing=True)
        deployment_statement = deployment_statement.with_for_update(
            read=True, nowait=True
        ).execution_options(populate_existing=True)
        solution_statement = solution_statement.with_for_update(
            read=True, nowait=True
        ).execution_options(populate_existing=True)
    execution = await db.scalar(execution_statement)
    deployment = await db.scalar(deployment_statement)
    solution = await db.scalar(solution_statement)
    if (
        attempt is None
        or execution is None
        or deployment is None
        or solution is None
        or attempt.execution_id != execution.id
        or attempt.claim_token is None
        or attempt.completed_at is not None
        or attempt.status not in {"claimed", "running"}
        or attempt.started_at is None
        or attempt.worker_incarnation_id is None
        or execution.runtime_mode != "deployment-v1"
        or execution.solution_deployment_id != deployment.id
        or execution.organization_id != effective_org
        or execution.workflow_id is None
        or (workflow_id is not None and execution.workflow_id != workflow_id)
        or deployment.solution_id != source.solution_install_id
        or deployment.organization_id != solution.organization_id
        or deployment.organization_id not in {None, effective_org}
        or solution.status != "active"
        or deployment.compiled_manifest_hash != source.source_manifest_digest
        or deployment.resolution_map_hash != source.source_resolution_digest
    ):
        raise RuntimeSDKDenied("committed workflow/source binding is unavailable")
    manifest, _ = validate_runtime_closure(
        deployment.compiled_manifest,
        deployment.resolution_map,
        deployment.dependencies,
        expected_manifest_hash=source.source_manifest_digest,
        expected_resolution_hash=source.source_resolution_digest,
    )
    if manifest.solution_id != solution.id or manifest.deployment_id != deployment.id:
        raise RuntimeSDKDenied("manifest identity differs from accepted deployment")
    pin = _pin_from_deployment(
        execution.workflow_id, solution, deployment, allow_superseded=True
    )
    verify_runtime_evidence(
        str(deployment.id),
        execution.runtime_evidence,
        execution.runtime_evidence,
        execution.runtime_evidence_hash,
        pin.queue_evidence(),
    )
    if pin.can_access_global_repo != source.source_global_permission:
        raise RuntimeSDKDenied("source permission differs from accepted evidence")
    return attempt, execution


async def create_workflow_runtime_sdk_grant(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    start: CommittedWorkflowStart,
    caller: AuthorizedCallerSnapshot,
    policy: SelectedSDKPolicy,
    source: AcceptedManifestIdentity,
    now: datetime | None = None,
) -> GrantReference:
    """Atomically commit a grant using a fresh transaction's committed Start.

    A caller-owned session would see its own uncommitted running transition.
    This independently created session cannot issue for a flush-only Start.
    Trusted authorization must supply the separate original/effective caller.
    """
    if not all(
        isinstance(value, expected)
        for value, expected in (
            (start, CommittedWorkflowStart),
            (caller, AuthorizedCallerSnapshot),
            (policy, SelectedSDKPolicy),
            (source, AcceptedManifestIdentity),
        )
    ):
        raise RuntimeSDKDenied("trusted typed provisioning inputs required")
    start = CommittedWorkflowStart.model_validate(start)
    caller = AuthorizedCallerSnapshot.model_validate(caller)
    policy = SelectedSDKPolicy.model_validate(policy)
    source = AcceptedManifestIdentity.model_validate(source)
    issued_at = _now(now)
    initial_expiry = utc_from_us(start.predeclared_initial_access_expires_at_us)
    if epoch_us(initial_expiry) // 1_000_000 <= epoch_us(issued_at) // 1_000_000:
        raise RuntimeSDKDenied("predeclared initial credential expiry has passed")
    maximum_window = (
        start.timeout_seconds + 300 if start.timeout_seconds else RENEWAL_SECONDS
    )
    if epoch_us(initial_expiry) - epoch_us(issued_at) > maximum_window * 1_000_000:
        raise RuntimeSDKDenied(
            "predeclared initial expiry exceeds its existing lifetime"
        )
    _check_policy(policy, caller, source)
    try:
        async with session_factory() as db, db.begin():
            committed_attempt, _ = await _read_work(
                db,
                attempt_id=start.workflow_attempt_id,
                execution_id=start.execution_id,
                source=source,
                workflow_id=None,
                effective_org=caller.effective_organization_id,
            )
            if (
                committed_attempt.status != "running"
                or committed_attempt.phase != "execution"
            ):
                raise RuntimeSDKDenied("committed Start is unavailable")
            attempt, execution = await _read_work(
                db,
                attempt_id=start.workflow_attempt_id,
                execution_id=start.execution_id,
                source=source,
                workflow_id=None,
                effective_org=caller.effective_organization_id,
                lock=True,
            )
            if (
                attempt.status != "running"
                or attempt.phase != "execution"
                or attempt.attempt_number != start.attempt_number
                or attempt.claim_token != start.private_claim_token
                or attempt.worker_incarnation_id != start.worker_incarnation_id
                or epoch_us(attempt.started_at) != start.committed_started_at_us
                or execution.executed_by != caller.caller_user_id
                or execution.runtime_evidence is None
                or execution.runtime_evidence.get("workflow_timeout_seconds")
                != start.timeout_seconds
            ):
                raise RuntimeSDKDenied("committed Start/caller differs from provision")
            existing = await db.scalar(
                select(WorkflowRuntimeSDKGrant).where(
                    WorkflowRuntimeSDKGrant.workflow_attempt_id == attempt.id,
                )
            )
            snapshot = GrantSnapshot(
                id=existing.id if existing is not None else uuid4(),
                schema_version=GRANT_SCHEMA_VERSION,
                workflow_attempt_id=attempt.id,
                execution_id=execution.id,
                attempt_number=start.attempt_number,
                claim_token_digest=claim_digest(start.private_claim_token),
                worker_incarnation_id=start.worker_incarnation_id,
                supervisor_incarnation_id=start.supervisor_incarnation_id,
                runtime_session_id=start.runtime_session_id,
                started_at=utc_from_us(start.committed_started_at_us),
                issued_at=existing.issued_at if existing is not None else issued_at,
                timeout_seconds=start.timeout_seconds,
                credential_deadline=(
                    utc_from_us(start.predeclared_credential_deadline_us)
                    if start.predeclared_credential_deadline_us is not None
                    else None
                ),
                initial_access_expires_at=initial_expiry,
                caller_user_id=caller.caller_user_id,
                caller_organization_id=caller.caller_organization_id,
                effective_organization_id=caller.effective_organization_id,
                caller_email=caller.caller_email,
                caller_name=caller.caller_name,
                caller_admin=int(caller.caller_admin),
                caller_provider=int(caller.caller_provider),
                caller_external=int(caller.caller_external),
                caller_snapshot_digest=caller_digest(caller),
                workflow_id=execution.workflow_id,
                solution_install_id=source.solution_install_id,
                source_kind="solution-deployment",
                source_id=source.source_id,
                source_manifest_digest=source.source_manifest_digest,
                source_resolution_digest=source.source_resolution_digest,
                source_global_permission=int(source.source_global_permission),
                source_digest=source_digest(source),
                operations_digest=operations_digest(policy),
            )
            digest = grant_digest(snapshot)
            reference = GrantReference(grant_id=snapshot.id, grant_digest=digest)
            if existing is not None:
                await _load(db, reference)
                if existing.grant_digest != digest:
                    raise RuntimeSDKDenied("attempt already has a different grant")
            else:
                db.add(
                    WorkflowRuntimeSDKGrant(
                        **snapshot.model_dump(), grant_digest=digest
                    )
                )
                await db.flush()
                for ordinal, operation in enumerate(policy.operations):
                    db.add(
                        WorkflowRuntimeSDKGrantOperation(
                            grant_id=snapshot.id,
                            ordinal=ordinal,
                            **operation.model_dump(),
                        )
                    )
                await db.flush()
        return reference  # db.begin has committed both tables before exposure.
    except (SQLAlchemyError, ValueError, KeyError, DeploymentRuntimeError):
        raise RuntimeSDKDenied("runtime SDK grant provisioning denied") from None


async def _load(
    db: AsyncSession, reference: GrantReference
) -> ValidatedRuntimeSDKAuthority:
    row = await db.get(WorkflowRuntimeSDKGrant, reference.grant_id)
    if row is None or row.revoked_at is not None or row.revocation_reason is not None:
        raise RuntimeSDKDenied("runtime SDK grant is unavailable")
    snapshot = _snapshot(row)
    operation_rows = list(
        (
            await db.scalars(
                select(WorkflowRuntimeSDKGrantOperation)
                .where(
                    WorkflowRuntimeSDKGrantOperation.grant_id == row.id,
                )
                .order_by(WorkflowRuntimeSDKGrantOperation.ordinal)
            )
        ).all()
    )
    if [item.ordinal for item in operation_rows] != list(range(len(operation_rows))):
        raise RuntimeSDKDenied("operation ordinals differ from the closed policy")
    policy = SelectedSDKPolicy(
        operations=tuple(
            SDKOperation(
                operation=item.operation,
                integration_name=item.integration_name,
                scope_kind=item.scope_kind,
                scope_organization_id=item.scope_organization_id,
                resolved_organization_id=item.resolved_organization_id,
                solution_install_id=item.solution_install_id,
            )
            for item in operation_rows
        )
    )
    source = _source(snapshot)
    if (
        operations_digest(policy) != snapshot.operations_digest
        or source_digest(source) != snapshot.source_digest
        or grant_digest(snapshot) != row.grant_digest
        or row.grant_digest != reference.grant_digest
    ):
        raise RuntimeSDKDenied("runtime SDK immutable authority changed")
    attempt, execution = await _read_work(
        db,
        attempt_id=snapshot.workflow_attempt_id,
        execution_id=snapshot.execution_id,
        source=source,
        workflow_id=snapshot.workflow_id,
        effective_org=snapshot.effective_organization_id,
    )
    if (
        attempt.attempt_number != snapshot.attempt_number
        or claim_digest(attempt.claim_token) != snapshot.claim_token_digest
        or attempt.worker_incarnation_id != snapshot.worker_incarnation_id
        or epoch_us(attempt.started_at) != epoch_us(snapshot.started_at)
        or execution.executed_by != snapshot.caller_user_id
        or execution.runtime_evidence is None
        or execution.runtime_evidence.get("workflow_timeout_seconds")
        != snapshot.timeout_seconds
    ):
        raise RuntimeSDKDenied("runtime SDK attempt fence changed")
    return ValidatedRuntimeSDKAuthority(
        snapshot=snapshot, policy=policy, private_claim_token=attempt.claim_token
    )


async def issue_workflow_runtime_sdk_token(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    reference: GrantReference,
    now: datetime | None = None,
) -> CredentialBundle:
    issued_at = _now(now)
    try:
        async with session_factory() as db:
            authority = await _load(db, reference)
            expires_at = authority.snapshot.initial_access_expires_at
            if epoch_us(expires_at) // 1_000_000 <= epoch_us(issued_at) // 1_000_000:
                raise RuntimeSDKDenied("predeclared initial expiry has passed")
        return sign_runtime_sdk_token(
            reference, issued_at=issued_at, expires_at=expires_at
        )
    except (SQLAlchemyError, ValueError, KeyError, DeploymentRuntimeError):
        raise RuntimeSDKDenied("runtime SDK initial issuance denied") from None


async def load_runtime_sdk_authority(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    token: str,
) -> ValidatedRuntimeSDKAuthority:
    claims = decode_runtime_sdk_access(token)
    try:
        async with session_factory() as db:
            authority = await _load(
                db,
                GrantReference(
                    grant_id=UUID(claims.sub), grant_digest=claims.grant_digest
                ),
            )
            _check_token_window(authority.snapshot, claims)
            return authority
    except (SQLAlchemyError, ValueError, KeyError, DeploymentRuntimeError):
        raise RuntimeSDKDenied("runtime SDK authority lookup denied") from None


async def renew_workflow_runtime_sdk_token(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    token: str,
    now: datetime | None = None,
) -> CredentialBundle:
    claims = decode_runtime_sdk_access(token, allow_expired_for_renewal=True)
    reference = GrantReference(
        grant_id=UUID(claims.sub), grant_digest=claims.grant_digest
    )
    issued_at = _now(now)
    try:
        async with session_factory() as db:
            authority = await _load(db, reference)
            _check_token_window(authority.snapshot, claims)
            if authority.snapshot.timeout_seconds != 0:
                raise RuntimeSDKDenied("finite credentials cannot renew")
        return sign_runtime_sdk_token(
            reference,
            issued_at=issued_at,
            expires_at=issued_at + timedelta(seconds=RENEWAL_SECONDS),
        )
    except (
        SQLAlchemyError,
        ValueError,
        KeyError,
        OverflowError,
        DeploymentRuntimeError,
    ):
        raise RuntimeSDKDenied("runtime SDK renewal denied") from None


def _check_token_window(snapshot: GrantSnapshot, claims: RuntimeSDKTokenClaims) -> None:
    if (
        claims.iat < epoch_us(snapshot.issued_at) // 1_000_000
        or claims.exp <= claims.iat
    ):
        raise RuntimeSDKDenied("token issue time differs from its grant")
    if snapshot.timeout_seconds == 0:
        if claims.exp - claims.iat > RENEWAL_SECONDS:
            raise RuntimeSDKDenied("token exceeds its renewable access window")
    elif claims.exp > epoch_us(snapshot.initial_access_expires_at) // 1_000_000:
        raise RuntimeSDKDenied("token exceeds its predeclared deadline")


async def revoke_workflow_runtime_sdk_grant(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    grant_id: UUID,
    supervisor_incarnation_id: UUID,
    runtime_session_id: UUID,
    reason: Literal["session_closed", "supervisor_replaced", "explicit_revoke"],
    now: datetime | None = None,
) -> bool:
    """Return True only for the first committed revocation; never write lifecycle."""
    if not all(
        isinstance(value, UUID)
        for value in (grant_id, supervisor_incarnation_id, runtime_session_id)
    ):
        raise RuntimeSDKDenied("typed revocation identity required")
    if reason not in {"session_closed", "supervisor_replaced", "explicit_revoke"}:
        raise RuntimeSDKDenied("invalid revocation reason")
    revoked_at = _now(now)
    try:
        async with session_factory() as db, db.begin():
            row = await db.scalar(
                select(WorkflowRuntimeSDKGrant)
                .where(
                    WorkflowRuntimeSDKGrant.id == grant_id,
                )
                .with_for_update()
            )
            if (
                row is None
                or row.supervisor_incarnation_id != supervisor_incarnation_id
                or row.runtime_session_id != runtime_session_id
            ):
                raise RuntimeSDKDenied("revocation session differs from grant")
            if row.revoked_at is not None:
                return False
            row.revoked_at, row.revocation_reason = revoked_at, reason
        return True
    except (SQLAlchemyError, ValueError):
        raise RuntimeSDKDenied("runtime SDK revocation denied") from None
