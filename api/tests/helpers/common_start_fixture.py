"""Synthetic prepared SQL fixtures, never admission or Rust Start evidence.

Native parents precede held C1-C5 activation. Common stage requires independently
qualified installed authority, explicit genuine logins and separately approved
execution. No DDL/roles/grants/issuer/source writes/runtime. Exact private intents
are durable before common mutation. Any failure retains all files/database; no
retry, reset, reopen, owner transfer or inferred physical facts.

The explicit row construction is adapted from the existing test-only cancellation
producer; its Running/Start producer is unchanged. This is fixture setup, not a
second implementation of production lifecycle business logic.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.services.solutions.deployment_manifest import canonical_json, sha256_digest
from tests.helpers.common_cancel_fixture import (
    COMMON_REVISION,
    FixturePreparationError,
    _clock,
    _identity,
    _insert_claimed_attempt,
    _native_evidence,
    _private_output,
    _read_seed,
    _revision,
    _seed_native_history,
)

PAIR_SCHEMA = "bifrost.common-start-sql-fixtures/v1"


async def _seed_one_prepared(
    native_factory: async_sessionmaker[AsyncSession],
    core_factory: async_sessionmaker[AsyncSession],
    *,
    expected_native_principal: str,
    native_seed: Path,
    destination: Path,
    timeout_seconds: int,
) -> dict:
    """Create only synthetic Pending/claimed common records, never Start."""
    if type(timeout_seconds) is not int or timeout_seconds not in {0, 30}:
        raise FixturePreparationError("fixture timeout must be native30 or0")
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
    # Read-only preflight before durable intent or any common write. The
    # accepted native pin, not the mutable workflow row alone, owns timeout.
    async with native_factory() as db:
        await _identity(db, expected_native_principal)
        await _revision(db, COMMON_REVISION)
        evidence = await _native_evidence(db, seed)
        if evidence.get("workflow_timeout_seconds") != timeout_seconds:
            raise FixturePreparationError("prepared fixture native timeout differs")
        now = await _clock(db)
    prepare_digest = hashlib.sha256(b"synthetic-sql-prepare").hexdigest()
    artifact_hash = hashlib.sha256(b"synthetic-non-runnable-start-fixture").hexdigest()
    fixture = {
        "execution_id": str(seed["execution_id"]),
        **{
            name: str(value)
            for name, value in identifiers.items()
            if name not in {"prepare_id", "start_id", "start_message_id"}
        },
        "binding_sha256": binding,
        "channel_custody_sha256": custody,
        "prepare_message_id": str(identifiers["prepare_id"]),
        "prepare_sha256": prepare_digest,
        "committed_start_id": str(identifiers["start_id"]),
        "start_message_id": str(identifiers["start_message_id"]),
        "artifact_id": "sha256:" + artifact_hash,
        "input_json": "{}",
        "context_json": "{}",
        "deadline_utc": (now + timedelta(seconds=timeout_seconds)).isoformat()
        if timeout_seconds
        else None,
    }
    with _private_output(
        destination,
        {
            "schema": "bifrost.fixture-pending/v1",
            "intent": fixture,
            "setup_ids": {key: str(value) for key, value in identifiers.items()},
        },
    ) as publish:
        values = {
            **seed,
            **identifiers,
            "binding": binding,
            "custody": custody,
            "prepare_digest": prepare_digest,
            "evidence": json.dumps(evidence),
            "evidence_hash": sha256_digest(canonical_json(evidence)),
        }
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
            await _insert_claimed_attempt(
                db,
                execution_id=seed["execution_id"],
                attempt_id=identifiers["attempt_id"],
                claim_token=identifiers["claim_token"],
                worker_incarnation_id=identifiers["worker_incarnation_id"],
                now=now,
            )
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
        # Fresh native-owner read catches two DSNs pointing at different clones.
        async with native_factory() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT e.control_owner,e.status::text,a.status,a.phase,"
                        "e.started_at IS NULL AND a.started_at IS NULL AND a.completed_at IS NULL AS untouched "
                        "FROM public.executions e JOIN public.workflow_execution_attempts a ON a.execution_id=e.id "
                        "WHERE e.id=:execution_id AND a.id=:attempt_id"
                    ),
                    values,
                )
            ).one()
            if tuple(row) != ("rust", "Pending", "claimed", "claim", True):
                raise FixturePreparationError("prepared native readback differs")
        async with core_factory() as db:
            await _identity(db, "wex_core")
            if not await db.scalar(
                text(
                    "SELECT s.closed_at IS NULL AND s.close_reason IS NULL "
                    "AND l.native_attempt_id=s.workflow_attempt_id "
                    "AND NOT EXISTS (SELECT 1 FROM public.runtime_starts WHERE session_id=s.id) "
                    "AND NOT EXISTS (SELECT 1 FROM public.runtime_admissions WHERE session_id=s.id) "
                    "AND NOT EXISTS (SELECT 1 FROM public.workflow_runtime_sdk_grants WHERE runtime_session_id=s.id) "
                    "AND NOT EXISTS (SELECT 1 FROM public.runtime_report_receipts WHERE session_id=s.id) "
                    "AND NOT EXISTS (SELECT 1 FROM public.runtime_resource_settlements WHERE session_id=s.id) "
                    "FROM public.runtime_sessions s JOIN public.runtime_workflow_live_links l ON l.session_id=s.id "
                    "WHERE s.id=:session_id AND s.execution_id=:execution_id "
                    "AND s.owner_incarnation_id=:owner_incarnation_id AND s.claim_token=:claim_token "
                    "AND s.worker_incarnation_id=:worker_incarnation_id "
                    "AND s.supervisor_incarnation_id=:supervisor_incarnation_id "
                    "AND s.runtime_incarnation_id=:runtime_incarnation_id "
                    "AND s.prepare_id=:prepare_id AND s.prepare_sha256=:prepare_digest"
                ),
                values,
            ):
                raise FixturePreparationError("prepared common readback differs")
        publish(fixture)
    return fixture


async def seed_start_native_history(
    factory: async_sessionmaker[AsyncSession],
    *,
    expected_principal: str,
    destination: Path,
    timeout_seconds: int,
) -> None:
    """Native30/0 only, before any common schema. No default factory."""
    try:
        await _seed_native_history(
            factory,
            expected_principal=expected_principal,
            destination=destination,
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        raise FixturePreparationError(
            "Start native fixture failed; preserve files/database"
        ) from None


async def seed_prepared_pair(
    native_factory: async_sessionmaker[AsyncSession],
    core_factory: async_sessionmaker[AsyncSession],
    *,
    expected_native_principal: str,
    finite_seed: Path,
    no_timeout_seed: Path,
    destination: Path,
) -> None:
    """Publish only after both genuine core cohorts and fresh readbacks commit.

    Compile/warm the Rust consumer BEFORE this finite30-second fixture. Never
    refresh its recorded deadline or recreate a cohort after setup/test failure.
    Synthetic Prepare/custody/artifact hashes prove no actual runtime facts.
    """
    try:
        finite_ids, unlimited_ids = _read_seed(finite_seed), _read_seed(no_timeout_seed)
        if set(finite_ids.values()) & set(unlimited_ids.values()):
            raise FixturePreparationError("Start native histories must be disjoint")
        paths = [
            destination.with_name(destination.name + suffix)
            for suffix in (".finite-prepared", ".no-timeout-prepared")
        ]
        if any(
            os.path.lexists(path)
            or os.path.lexists(path.with_name(path.name + ".pending"))
            for path in paths
        ):
            raise FixturePreparationError("Start child output already exists")
        with _private_output(
            destination,
            {
                "schema": "bifrost.fixture-pending/v1",
                "fixture_schema": PAIR_SCHEMA,
                "finite_native_ids": {k: str(v) for k, v in finite_ids.items()},
                "no_timeout_native_ids": {k: str(v) for k, v in unlimited_ids.items()},
            },
        ) as publish:
            finite = await _seed_one_prepared(
                native_factory,
                core_factory,
                expected_native_principal=expected_native_principal,
                native_seed=finite_seed,
                destination=paths[0],
                timeout_seconds=30,
            )
            unlimited = await _seed_one_prepared(
                native_factory,
                core_factory,
                expected_native_principal=expected_native_principal,
                native_seed=no_timeout_seed,
                destination=paths[1],
                timeout_seconds=0,
            )
            publish({"schema": PAIR_SCHEMA, "finite": finite, "no_timeout": unlimited})
    except Exception:
        raise FixturePreparationError(
            "Start prepared fixture failed; preserve files/database; no retry"
        ) from None
