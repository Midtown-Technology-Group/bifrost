"""Exact Root table contracts for reviewed immutable Solution runtimes."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from bifrost.solution_delivery_review import SharedRootTableGrant, require_shared_table_bindings

from src.models.contracts.policies import PolicyRuleRef, TablePolicies
from src.models.orm.tables import Table
from src.services.solutions.deployment_manifest import (
    SharedRootTableBinding,
    canonical_json,
    sha256_digest,
)

if TYPE_CHECKING:
    from src.core.auth import ExecutionContext


class SharedTableBindingError(ValueError):
    """The existing table no longer has the reviewed scope or metadata."""


def table_metadata_hash(table: Table, *, organization_id: UUID | None = None) -> str:
    """Freeze identity, scope, schema and inline policies, never document data."""
    if table.solution_id is not None or table.organization_id != organization_id:
        raise SharedTableBindingError("shared binding requires a global Root table or its explicitly reviewed organization Root table")
    try:
        policies = TablePolicies.model_validate(table.access or {})
    except ValidationError as exc:
        raise SharedTableBindingError("shared table policies are invalid") from exc
    if any(isinstance(item, PolicyRuleRef) for item in policies.policies):
        raise SharedTableBindingError("shared table policy references need a separately pinned rule contract")
    return sha256_digest(canonical_json({
        "id": str(table.id), "name": table.name,
        "organization_id": str(organization_id) if organization_id else None, "solution_id": None,
        "schema": table.schema, "policies": policies.model_dump(mode="json"),
    }))


async def require_shared_table(
    db: AsyncSession, name: str, binding: SharedRootTableGrant
) -> Table:
    # Keep this shared metadata lock through the document transaction. Metadata
    # updates and ownership changes cannot race a checked write. Refresh the
    # identity-map row so a previously loaded Table cannot authorize stale writes.
    table = await db.scalar(
        select(Table).where(Table.id == binding.table_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if table is None or table.name != name:
        raise SharedTableBindingError("shared table identity changed")
    if table_metadata_hash(table, organization_id=binding.organization_id) != binding.metadata_hash:
        raise SharedTableBindingError("shared table metadata changed")
    return table


async def require_shared_tables(
    db: AsyncSession, bindings: dict[str, SharedRootTableBinding], *,
    solution_organization_id: UUID | None = None,
) -> None:
    # Stable lock order across overlapping workflow batches.
    require_shared_table_bindings(bindings)
    grants = [(name, grant) for name, binding in bindings.items() for grant in binding.grants()]
    for name, binding in sorted(grants, key=lambda item: str(item[1].table_id)):
        if (binding.organization_id is not None and solution_organization_id is not None
                and binding.organization_id != solution_organization_id):
            raise SharedTableBindingError("shared table organization differs from the Solution installation")
        await require_shared_table(db, name, binding)


async def resolve_execution_shared_table(
    ctx: ExecutionContext, requested: str, *, write: bool = False,
    organization_id: UUID | None = None, explicit_scope: bool = False,
) -> Table | None:
    """Grant only a signed, active engine attempt its durable deployment's binding."""
    from src.core.auth import _active_engine_attempt
    from src.models.orm.executions import Execution
    from src.models.orm.solutions import Solution
    from src.repositories.solution_deployments import SolutionDeploymentRepository
    from src.services.solution_scope import parse_ctx_solution_id
    from src.services.solutions.deployment_manifest import validate_runtime_closure
    from src.services.solutions.deployment_runtime import (
        DeploymentRuntimeError, _pin_from_deployment, verify_runtime_evidence,
    )

    user = ctx.user
    if not user.is_engine_token or user.engine_execution_id is None:
        return None
    target = parse_ctx_solution_id(ctx)
    if target is None or str(target) != user.engine_solution_id:
        return None
    execution = await ctx.db.get(Execution, user.engine_execution_id, populate_existing=True)
    if execution is None or execution.runtime_mode != "deployment-v1":
        return None
    if (
        user.engine_attempt_token is None
        or not await _active_engine_attempt(ctx.db, execution.id, user.engine_attempt_token)
        or execution.solution_deployment_id is None
        or execution.organization_id != ctx.org_id
    ):
        raise SharedTableBindingError("engine execution binding is no longer active")
    deployment = await SolutionDeploymentRepository(ctx.db).get_by_id_for_runtime(
        execution.solution_deployment_id
    )
    solution = await ctx.db.get(Solution, target)
    if (
        deployment is None or deployment.solution_id != target
        or solution is None or solution.status != "active"
        or deployment.organization_id != solution.organization_id
        or deployment.organization_id not in {None, ctx.org_id}
        or execution.workflow_id is None
    ):
        raise SharedTableBindingError("engine deployment binding is invalid")
    try:
        manifest, _ = validate_runtime_closure(
            deployment.compiled_manifest, deployment.resolution_map, deployment.dependencies,
            expected_manifest_hash=deployment.compiled_manifest_hash,
            expected_resolution_hash=deployment.resolution_map_hash,
        )
        pin = _pin_from_deployment(execution.workflow_id, solution, deployment, allow_superseded=True)
        verify_runtime_evidence(
            str(deployment.id), execution.runtime_evidence, execution.runtime_evidence,
            execution.runtime_evidence_hash, pin.queue_evidence(),
        )
    except (ValueError, KeyError, DeploymentRuntimeError) as exc:
        raise SharedTableBindingError("engine deployment evidence changed") from exc
    if manifest.solution_id != target or manifest.deployment_id != deployment.id:
        raise SharedTableBindingError("engine manifest identity changed")
    for name, group in manifest.shared_tables.items():
        grants = group.grants()
        if requested != name and not any(requested == str(grant.table_id) for grant in grants):
            continue
        if explicit_scope and organization_id not in {None, ctx.org_id}:
            raise SharedTableBindingError("shared table organization differs from the signed execution")
        eligible = [grant for grant in grants if grant.organization_id in {None, ctx.org_id}]
        if explicit_scope and organization_id is None:
            eligible = [grant for grant in eligible if grant.organization_id is None]
        if requested != name:
            eligible = [grant for grant in eligible if str(grant.table_id) == requested]
        # Match Root's default name cascade, but only within exact reviewed grants.
        eligible.sort(key=lambda grant: grant.organization_id != ctx.org_id)
        if not eligible:
            raise SharedTableBindingError("shared table scope differs from the signed execution or requested scope")
        binding = eligible[0]
        if binding.organization_id is not None and (
            (solution.organization_id is not None and binding.organization_id != solution.organization_id)
            or binding.organization_id != ctx.org_id
        ):
            raise SharedTableBindingError("shared table organization differs from the signed execution")
        if write and binding.access != "read-write":
            raise SharedTableBindingError("shared binding does not allow document writes")
        return await require_shared_table(ctx.db, name, binding)
    return None
