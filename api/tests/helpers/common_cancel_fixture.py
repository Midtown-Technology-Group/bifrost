"""Synthetic SQL-only fixture producer for the held R1 cancellation packet.

Two explicit stages: native history before candidate DDL, then common records
after the reviewed schema/principals are installed. Nothing here applies DDL,
creates roles/grants, issues credentials, reads/writes source blobs or launches
a runtime. A stored fixture Start is NOT evidence that Rust committed Start.
Synthetic hashes below are NOT physical channel/artifact observations.

Run only inside the supported, approved disposable SQL lane. Callers supply
explicit session factories; no application/default DSN is used. Preserve every
private output and the disposable database after failure/uncertain commit.
Never reset/reopen/retry a cohort or delete accepted history as cleanup.
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from src.models.orm.organizations import Organization
from src.models.orm.solution_deployments import SolutionDeployment
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.models.orm.workflows import Workflow
from src.repositories.solution_deployments import SolutionDeploymentRepository
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

NATIVE_REVISION = "20261012_work_delivery_started"
COMMON_REVISION = "20261016_runtime_source_drain"
SEED_SCHEMA = "bifrost.common-cancel-native-seed/v1"
FIXTURE_SCHEMA = "bifrost.common-cancel-fixture/v1"
SEED_IDS = (
    "organization_id",
    "user_id",
    "solution_id",
    "deployment_id",
    "workflow_id",
    "execution_id",
    "incumbent_execution_id",
)
COMMON_TABLES = (
    "runtime_deployment_artifacts",
    "runtime_execution_owners",
    "runtime_sessions",
    "runtime_workflow_live_links",
    "runtime_starts",
    "runtime_report_receipts",
    "workflow_runtime_sdk_grants",
    "workflow_runtime_sdk_grant_operations",
    "runtime_admissions",
    "runtime_resource_settlements",
)


class FixturePreparationError(ValueError):
    """Fixed diagnostic only; never include DSNs, private claims or SQL details."""


@contextmanager
def _private_output(path: Path, pending: dict):
    # O_EXCL refuses existing files/symlinks before any database mutation.
    # The caller owns the containing private directory and its mount/custody.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        os.fchmod(output.fileno(), 0o600)

        def write(document: dict) -> None:
            output.seek(0)
            json.dump(document, output, sort_keys=True)
            output.write("\n")
            output.truncate()
            output.flush()
            os.fsync(output.fileno())

        write(pending)
        # Exceptions preserve this private pending record, not a valid fixture.
        yield write


async def _identity(db: AsyncSession, expected: str) -> None:
    row = (
        await db.execute(
            text(
                "SELECT current_database()='bifrost_test' AS database_matches, "
                "session_user=:expected AND current_user=session_user AS login_matches, "
                "current_setting('transaction_isolation')='read committed' AS isolation_matches"
            ),
            {"expected": expected},
        )
    ).one()
    if not all(row):
        raise FixturePreparationError("fixture database/login/isolation differs")


async def _revision(db: AsyncSession, expected: str) -> None:
    revision = (
        await db.execute(text("SELECT version_num FROM public.alembic_version"))
    ).scalar_one()
    if revision != expected:
        raise FixturePreparationError("fixture requires its exact Alembic stage")


def _read_seed(path: Path) -> dict[str, UUID]:
    with path.open("rb") as source:
        raw = source.read(8193)
    if len(raw) > 8192:
        raise FixturePreparationError("native seed exceeds its bound")
    try:
        document = json.loads(raw)
        if set(document) != {"schema", *SEED_IDS} or document["schema"] != SEED_SCHEMA:
            raise ValueError
        if any(type(document[name]) is not str for name in SEED_IDS):
            raise ValueError
        seed = {name: UUID(document[name]) for name in SEED_IDS}
        if any(str(seed[name]) != document[name] for name in SEED_IDS):
            raise ValueError
        if len(set(seed.values())) != len(SEED_IDS):
            raise ValueError
        return seed
    except (ValueError, TypeError, KeyError):
        raise FixturePreparationError("native seed identity is invalid") from None


async def _clock(db: AsyncSession) -> datetime:
    now = await db.scalar(text("SELECT clock_timestamp()"))
    if not isinstance(now, datetime) or now.utcoffset() != timedelta(0):
        raise FixturePreparationError("fixture database clock is unavailable")
    return now


async def _native_evidence(db: AsyncSession, seed: dict[str, UUID]) -> dict:
    deployment = await db.get(SolutionDeployment, seed["deployment_id"])
    solution = await db.get(Solution, seed["solution_id"])
    workflow = await db.get(Workflow, seed["workflow_id"])
    user = await db.get(User, seed["user_id"])
    if (
        deployment is None
        or solution is None
        or workflow is None
        or user is None
        or deployment.solution_id != solution.id
        or workflow.solution_id != solution.id
        or deployment.organization_id != seed["organization_id"]
        or solution.organization_id != seed["organization_id"]
        or workflow.organization_id != seed["organization_id"]
        or user.organization_id != seed["organization_id"]
    ):
        raise FixturePreparationError("native fixture source relationships differ")
    return _pin_from_deployment(
        workflow.id, solution, deployment, allow_superseded=False
    ).queue_evidence()


async def _seed_native_history(
    factory: async_sessionmaker[AsyncSession],
    *,
    expected_principal: str,
    destination: Path,
) -> None:
    """Prepare only native parents/Python positive before common DDL exists."""
    if not expected_principal or expected_principal in {
        "wex_core",
        "wex_incumbent",
        "runtime_lifecycle_guard",
        "runtime_artifact_lock_guard",
    }:
        raise FixturePreparationError("explicit native fixture owner required")
    seed = {name: uuid4() for name in SEED_IDS}
    document = {
        "schema": SEED_SCHEMA,
        **{key: str(value) for key, value in seed.items()},
    }
    with _private_output(
        destination, {"schema": "bifrost.fixture-pending/v1", "intent": document}
    ) as publish:
        async with factory() as db, db.begin():
            await _identity(db, expected_principal)
            await _revision(db, NATIVE_REVISION)
            for table in COMMON_TABLES:
                if await db.scalar(
                    text("SELECT pg_catalog.to_regclass(:relation) IS NOT NULL"),
                    {"relation": f"public.{table}"},
                ):
                    raise FixturePreparationError("native stage found common schema")
            suffix = seed["solution_id"].hex
            db.add(
                Organization(
                    id=seed["organization_id"],
                    name=f"sql-cancel-{suffix}",
                    created_by="sql-fixture",
                )
            )
            await db.flush()
            db.add_all(
                [
                    User(
                        id=seed["user_id"],
                        email=f"sql-cancel-{suffix}@example.invalid",
                        name="SQL fixture",
                        organization_id=seed["organization_id"],
                    ),
                    Solution(
                        id=seed["solution_id"],
                        slug=f"sql-cancel-{suffix}",
                        name="SQL cancellation fixture",
                        organization_id=seed["organization_id"],
                        allow_outbound_access=False,
                    ),
                ]
            )
            await db.flush()
            db.add(
                Workflow(
                    id=seed["workflow_id"],
                    name=f"sql-cancel-{suffix}",
                    function_name="run",
                    path="fixture.py",
                    solution_id=seed["solution_id"],
                    organization_id=seed["organization_id"],
                    timeout_seconds=30,
                )
            )
            await db.flush()
            prefix = f"_solutions/{seed['solution_id']}/{seed['deployment_id']}/"
            source_hash = sha256_digest(b"async def run():\n    return None\n")
            entity = RuntimeEntityDefinition(
                portable_ref="fixture.py::run",
                resolved_id=seed["workflow_id"],
                source_ref="fixture.py",
                source_hash=source_hash,
                definition={
                    "name": f"sql-cancel-{suffix}",
                    "function_name": "run",
                    "path": "fixture.py",
                    "timeout_seconds": 30,
                    "type": "workflow",
                },
            )
            resolution = DeploymentResolutionMap(
                workflows={"fixture.py::run": entity},
                sources={
                    "fixture.py": RuntimeSourceResolution(
                        object_key=prefix + "fixture.py",
                        content_hash=source_hash,
                    )
                },
            )
            manifest = CompiledDeploymentManifest(
                solution_id=seed["solution_id"],
                deployment_id=seed["deployment_id"],
                bundle_hash=sha256_digest(b"synthetic-non-runnable-sql-fixture"),
                resolution_map_hash=sha256_digest(canonical_json(resolution)),
                source=DeploymentSource(
                    artifact_key=prefix + "bundle.zip", runtime_prefix=prefix
                ),
                workflows={"fixture.py::run": entity},
            )
            deployment = SolutionDeployment(
                id=seed["deployment_id"],
                solution_id=seed["solution_id"],
                organization_id=seed["organization_id"],
                state="draft",
                bundle_hash=manifest.bundle_hash,
                compiled_manifest=manifest.model_dump(mode="json", exclude_none=True),
                compiled_manifest_hash=manifest.content_hash(),
                resolution_map=resolution.model_dump(mode="json", exclude_none=True),
                resolution_map_hash=manifest.resolution_map_hash,
                source_artifact_key=manifest.source.artifact_key,
                runtime_storage_prefix=prefix,
                created_by=seed["user_id"],
                dependencies=[],
            )
            repository = SolutionDeploymentRepository(db)
            await repository.create(deployment)
            previous = "draft"
            for state in ("building", "validated", "ready", "activating", "active"):
                await repository.transition(
                    seed["deployment_id"],
                    seed["organization_id"],
                    expected_state=previous,
                    new_state=state,
                )
                previous = state
            solution = await db.get(Solution, seed["solution_id"])
            if solution is None:
                raise FixturePreparationError("native fixture install disappeared")
            solution.active_deployment_id = seed["deployment_id"]
            evidence = await _native_evidence(db, seed)
            db.add(
                Execution(
                    id=seed["incumbent_execution_id"],
                    workflow_id=seed["workflow_id"],
                    workflow_name=evidence["workflow_name"],
                    executed_by=seed["user_id"],
                    executed_by_name="SQL fixture",
                    organization_id=seed["organization_id"],
                    status=ExecutionStatus.RUNNING,
                    started_at=datetime.now(UTC),
                    runtime_mode="deployment-v1",
                    solution_deployment_id=seed["deployment_id"],
                    runtime_evidence=evidence,
                    runtime_evidence_hash=sha256_digest(canonical_json(evidence)),
                )
            )
        # Publish only after observed commit. No exception path retries writes.
        publish(document)


async def _seed_common_running(
    native_factory: async_sessionmaker[AsyncSession],
    core_factory: async_sessionmaker[AsyncSession],
    *,
    expected_native_principal: str,
    native_seed: Path,
    destination: Path,
) -> None:
    """Create a synthetic cohort after the separately approved common apply.

    Native owner performs READS only in this stage. Core performs every common
    and Rust-owned native write. No issuer/SDK role or runtime is involved.
    Exact installed-catalog proof remains an external prerequisite, not inferred
    from matching names/revision here. An administrator cannot SET ROLE to fake
    either actual login. Accepted rows remain until disposable DB teardown.
    """
    seed = _read_seed(native_seed)
    identifiers = {
        name: uuid4()
        for name in (
            "owner_incarnation_id",
            "attempt_id",
            "claim_token",
            "worker_incarnation_id",
            "session_id",
            "supervisor_incarnation_id",
            "runtime_incarnation_id",
            "prepare_id",
            "start_id",
            "start_message_id",
        )
    }
    binding = hashlib.sha256(
        f"synthetic-binding:{identifiers['session_id']}".encode()
    ).hexdigest()
    custody = hashlib.sha256(
        f"synthetic-custody:{identifiers['session_id']}".encode()
    ).hexdigest()
    fixture = {
        "schema": FIXTURE_SCHEMA,
        "execution_id": str(seed["execution_id"]),
        "incumbent_execution_id": str(seed["incumbent_execution_id"]),
        **{
            name: str(value)
            for name, value in identifiers.items()
            if name not in {"prepare_id", "start_id", "start_message_id"}
        },
        "binding_sha256": binding,
        "channel_custody_sha256": custody,
    }
    with _private_output(
        destination,
        {
            "schema": "bifrost.fixture-pending/v1",
            "intent": fixture,
            "setup_ids": {key: str(value) for key, value in identifiers.items()},
        },
    ) as publish:
        async with native_factory() as db:
            await _identity(db, expected_native_principal)
            await _revision(db, COMMON_REVISION)
            evidence = await _native_evidence(db, seed)
        values = {
            **seed,
            **identifiers,
            "binding": binding,
            "custody": custody,
            "prepare_digest": hashlib.sha256(b"synthetic-sql-prepare").hexdigest(),
            "evidence": json.dumps(evidence),
            "evidence_hash": sha256_digest(canonical_json(evidence)),
        }
        artifact_hash = hashlib.sha256(
            b"synthetic-non-runnable-sql-fixture"
        ).hexdigest()
        values.update(
            artifact_id="sha256:" + artifact_hash,
            artifact=json.dumps(
                {
                    "kind": "native-executable/v1",
                    "artifact_id": "sha256:" + artifact_hash,
                    "runtime_protocol": "bifrost.runtime/v1",
                    "build_evidence_sha256": artifact_hash,
                    "fixture_only": True,
                }
            ),
            artifact_hash=artifact_hash,
        )
        # Artifact association is admitted separately before any execution; its
        # parent NOWAIT locks must not become a later execution->source wait.
        async with core_factory() as db, db.begin():
            await _identity(db, "wex_core")
            await db.execute(
                text(
                    "INSERT INTO public.runtime_deployment_artifacts (deployment_id,workflow_id,solution_id,artifact_id,runtime,runtime_protocol,artifact,input_schema,output_schema,build_evidence_sha256,source_sha256,reviewed_by,live_deployment_id,live_workflow_id,live_solution_id,live_reviewed_by) VALUES (:deployment_id,:workflow_id,:solution_id,:artifact_id,'fixture/v1','bifrost.runtime/v1',CAST(:artifact AS jsonb),'{}','{}',:artifact_hash,:artifact_hash,:user_id,:deployment_id,:workflow_id,:solution_id,:user_id)"
                ),
                values,
            )
        async with core_factory() as db, db.begin():
            await _identity(db, "wex_core")
            if not await db.scalar(
                text(
                    "SELECT pg_catalog.pg_try_advisory_xact_lock_shared(pg_catalog.hashtext('bifrost:workspace-release'))"
                )
            ):
                raise FixturePreparationError("fixture source admission contended")
            if not await db.scalar(
                text(
                    "SELECT pg_catalog.pg_try_advisory_xact_lock(pg_catalog.hashtext('bifrost:workflow-execution:' || CAST(CAST(:execution_id AS uuid) AS text)))"
                ),
                values,
            ):
                raise FixturePreparationError("fixture execution admission contended")
            await db.execute(
                text(
                    "INSERT INTO public.executions (id,workflow_name,status,parameters,time_saved,value,executed_by,executed_by_name,organization_id,workflow_id,solution_deployment_id,runtime_mode,runtime_evidence,runtime_evidence_hash,is_local_execution,attempt_tracking_version,control_owner) VALUES (:execution_id,'synthetic-sql-fixture','Pending','{}',0,0,:user_id,'SQL fixture',:organization_id,:workflow_id,:deployment_id,'deployment-v1',CAST(:evidence AS jsonb),:evidence_hash,false,'v1','rust')"
                ),
                values,
            )
            now = await _clock(db)
            db.add(
                WorkflowExecutionAttempt(
                    id=identifiers["attempt_id"],
                    execution_id=seed["execution_id"],
                    attempt_number=1,
                    claim_token=identifiers["claim_token"],
                    status="claimed",
                    phase="claim",
                    worker_id="synthetic-sql-fixture",
                    worker_incarnation_id=identifiers["worker_incarnation_id"],
                    published_at=now,
                    claimed_at=now,
                    heartbeat_at=now,
                    runtime_mode="deployment-v1",
                )
            )
            await db.flush()
            caller = {
                "caller_user_id": str(seed["user_id"]),
                "caller_organization_id": str(seed["organization_id"]),
                "effective_organization_id": str(seed["organization_id"]),
                "fixture_only": True,
            }
            values.update(
                caller=json.dumps(caller),
                caller_digest=hashlib.sha256(canonical_json(caller)).hexdigest(),
            )
            await db.execute(
                text(
                    "INSERT INTO public.runtime_execution_owners (execution_id,owner_incarnation_id,workflow_id,deployment_id,artifact_id,caller_snapshot,caller_sha256,live_execution_id) VALUES (:execution_id,:owner_incarnation_id,:workflow_id,:deployment_id,:artifact_id,CAST(:caller AS jsonb),:caller_digest,:execution_id)"
                ),
                values,
            )
            await db.execute(
                text(
                    "INSERT INTO public.runtime_sessions (id,execution_id,owner_incarnation_id,workflow_attempt_id,claim_token,worker_incarnation_id,supervisor_incarnation_id,runtime_incarnation_id,channel_custody_sha256,binding_sha256,prepare_id,prepare_sha256) VALUES (:session_id,:execution_id,:owner_incarnation_id,:attempt_id,:claim_token,:worker_incarnation_id,:supervisor_incarnation_id,:runtime_incarnation_id,:custody,:binding,:prepare_id,:prepare_digest)"
                ),
                values,
            )
            await db.execute(
                text(
                    "INSERT INTO public.runtime_workflow_live_links (session_id,execution_id,owner_incarnation_id,workflow_attempt_id,claim_token,worker_incarnation_id,native_attempt_id) VALUES (:session_id,:execution_id,:owner_incarnation_id,:attempt_id,:claim_token,:worker_incarnation_id,:attempt_id)"
                ),
                values,
            )
            started = await _clock(db)
            values.update(
                started=started,
                deadline=started + timedelta(seconds=30),
                empty_digest=hashlib.sha256(b"{}").hexdigest(),
            )
            await db.execute(
                text(
                    "UPDATE public.executions SET status='Running',started_at=:started WHERE id=:execution_id"
                ),
                values,
            )
            await db.execute(
                text(
                    "UPDATE public.workflow_execution_attempts SET status='running',phase='execution',started_at=:started,heartbeat_at=:started WHERE id=:attempt_id"
                ),
                values,
            )
            # B2 requires native Running/execution and the same clock BEFORE
            # inserting Start. Do not copy prototype insert-before-update order.
            await db.execute(
                text(
                    "INSERT INTO public.runtime_starts (id,session_id,execution_id,owner_incarnation_id,workflow_attempt_id,start_message_id,input_sha256,context_sha256,started_at,deadline_utc) VALUES (:start_id,:session_id,:execution_id,:owner_incarnation_id,:attempt_id,:start_message_id,:empty_digest,:empty_digest,:started,:deadline)"
                ),
                values,
            )
        # Fresh native-owner read catches two DSNs pointing at different clones.
        async with native_factory() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT e.control_owner,e.status::text,s.id FROM public.executions e JOIN public.runtime_sessions s ON s.execution_id=e.id JOIN public.runtime_execution_owners o ON o.execution_id=e.id AND o.owner_incarnation_id=s.owner_incarnation_id WHERE e.id=:execution_id AND s.id=:session_id AND o.owner_incarnation_id=:owner_incarnation_id"
                    ),
                    values,
                )
            ).one()
            if tuple(row) != ("rust", "Running", identifiers["session_id"]):
                raise FixturePreparationError("fixture committed readback differs")
        publish(fixture)


async def seed_native_history(
    factory: async_sessionmaker[AsyncSession],
    *,
    expected_principal: str,
    destination: Path,
) -> None:
    """Explicit native-only stage; never expose SQL/parameter exception details."""
    try:
        await _seed_native_history(
            factory, expected_principal=expected_principal, destination=destination
        )
    except Exception:
        raise FixturePreparationError(
            "native fixture setup failed; preserve private output/database; no retry"
        ) from None


async def seed_common_running(
    native_factory: async_sessionmaker[AsyncSession],
    core_factory: async_sessionmaker[AsyncSession],
    *,
    expected_native_principal: str,
    native_seed: Path,
    destination: Path,
) -> None:
    """Explicit common stage; setup is not runtime/Start/custody acceptance."""
    try:
        await _seed_common_running(
            native_factory,
            core_factory,
            expected_native_principal=expected_native_principal,
            native_seed=native_seed,
            destination=destination,
        )
    except Exception:
        raise FixturePreparationError(
            "common fixture setup failed; preserve private output/database; no retry"
        ) from None
