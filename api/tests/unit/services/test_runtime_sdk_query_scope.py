"""Executed query-boundary proof; not installed-role or common-runtime acceptance.

Observe the actual SQL used by positive credential operations. A statement that
reads unrelated tables/columns is refused before execution; the operation must
still succeed, using real native deployment/grant validation. Fixture setup is
outside the observation window and no database privileges are changed.
"""

import re

import pytest
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError

from src.services import runtime_sdk_grants as grants
from tests.unit.services import test_runtime_sdk_grants as foundation

credential_cohort = foundation.credential_cohort
make_work = foundation.make_work

# Includes predicate/order columns as well as projected columns. These are the
# existing validation inputs, not permission for any SQL grant or new reader.
READ_COLUMNS = {
    "workflow_execution_attempts": {
        "id", "execution_id", "attempt_number", "claim_token", "status", "phase",
        "worker_incarnation_id", "claimed_at", "started_at", "heartbeat_at", "completed_at",
    },
    "executions": {
        "id", "workflow_id", "executed_by", "organization_id", "runtime_mode",
        "solution_deployment_id", "runtime_evidence", "runtime_evidence_hash",
    },
    "solution_deployments": {
        "id", "organization_id", "solution_id", "state", "bundle_hash",
        "compiled_manifest", "compiled_manifest_hash", "resolution_map",
        "resolution_map_hash", "runtime_storage_prefix", "git_commit_sha",
    },
    "solution_deployment_dependencies": {
        "deployment_id", "dependency_solution_id", "dependency_deployment_id", "resolved_bundle_hash",
    },
    "solutions": {"id", "organization_id", "status", "global_repo_access"},
    "users": {"id", "organization_id", "is_active", "is_superuser", "is_external"},
    "organizations": {"id", "is_active", "is_provider"},
    "roles": {"id", "name"},
    "user_roles": {"user_id", "role_id"},
}
GRANT_TABLES = {"workflow_runtime_sdk_grants", "workflow_runtime_sdk_grant_operations"}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "issue", "load", "ingress", "renew"])
async def test_positive_credential_operation_has_bounded_reads(make_work, async_engine, operation):
    work = await make_work(timeout=0)
    reference = None if operation == "create" else await work.provision()
    bundle = (
        await grants.issue_workflow_runtime_sdk_token(work.cohort.factory, reference=reference)
        if operation in {"load", "ingress", "renew"}
        else None
    )
    observed, rejected = set(), []

    def observe(_connection, _cursor, statement, _parameters, _context, _executemany):
        sql = statement.lower()
        if not sql.lstrip().startswith("select"):
            return
        tables = set(re.findall(r"\b(?:from|join)\s+(?:public\.)?([a-z_]+)\b", sql))
        invalid = tables - READ_COLUMNS.keys() - GRANT_TABLES
        for table in tables & READ_COLUMNS.keys():
            columns = set(re.findall(rf"\b{table}\.([a-z_]+)\b", sql))
            invalid.update(f"{table}.{name}" for name in columns - READ_COLUMNS[table])
        if invalid:
            # Record identifiers only, never SQL parameters or payload bytes.
            rejected.extend(sorted(invalid))
            raise SQLAlchemyError("credential query exceeded the bounded read interface")
        observed.update(tables)

    event.listen(async_engine.sync_engine, "before_cursor_execute", observe)
    try:
        if operation == "create":
            assert (await work.provision()).grant_id
        elif operation == "issue":
            assert (await grants.issue_workflow_runtime_sdk_token(
                work.cohort.factory, reference=reference
            )).access_token
        elif operation == "load":
            authority = await grants.load_runtime_sdk_authority(
                work.cohort.factory, token=bundle.access_token
            )
            assert authority.policy == work.policy
        elif operation == "ingress":
            authority = await grants.load_runtime_sdk_ingress_authority(
                work.cohort.factory, token=bundle.access_token
            )
            assert authority.policy == work.policy
        else:
            renewed = await grants.renew_workflow_runtime_sdk_token(
                work.cohort.factory, token=bundle.refresh_token
            )
            assert renewed.access_token
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", observe)
    assert not rejected
    assert {"workflow_execution_attempts", "executions", "solutions",
            "solution_deployments", "solution_deployment_dependencies"} <= observed
    if operation == "ingress":
        assert {"users", "organizations", "roles", "user_roles"} <= observed
