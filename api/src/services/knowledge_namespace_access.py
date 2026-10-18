"""Namespace selection preserves sharing rows without creating content grants.

The junction identifies which namespaces may be selected for an agent; it is
not a universal knowledge content ACL. A sharing role need not carry any
permission rows, but its current assignment must cover the caller's home org.
"""

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import PROVIDER_ORG_ID
from src.models.orm.knowledge_sources import KnowledgeNamespaceRole
from src.services.authorization.context import BoundaryKind
from src.services.authorization.enforce import Caller


def effective_namespace_role_ids(caller: Caller) -> frozenset[UUID]:
    """Include the base role and currently bounded additional sharing roles."""
    ctx = caller.ctx
    if ctx is None:
        return frozenset()
    if ctx.is_platform_admin:
        return frozenset([ctx.base_role_id, *(r.role_id for r in ctx.role_grants)])
    if ctx.home_organization_id is None:
        return frozenset()
    return frozenset(
        [ctx.base_role_id]
        + [
            role.role_id
            for role in ctx.role_grants
            if any(
                (
                    boundary.kind == BoundaryKind.ORGANIZATION
                    and boundary.organization_id == ctx.home_organization_id
                )
                or (
                    boundary.kind == BoundaryKind.MANAGED_ORGANIZATIONS
                    and ctx.home_organization_id != PROVIDER_ORG_ID
                )
                for boundary in role.boundaries
            )
        ]
    )


async def accessible_knowledge_namespaces(db: AsyncSession, caller: Caller) -> list[str]:
    """Intersect effective roles with the existing home/global assignment scope."""
    role_ids = effective_namespace_role_ids(caller)
    if not role_ids:
        return []
    stmt = select(KnowledgeNamespaceRole.namespace).where(
        KnowledgeNamespaceRole.role_id.in_(role_ids)
    )
    assert caller.ctx is not None
    if not caller.ctx.is_platform_admin:
        stmt = stmt.where(
            or_(
                KnowledgeNamespaceRole.organization_id.is_(None),
                KnowledgeNamespaceRole.organization_id == caller.ctx.home_organization_id,
            )
        )
    result = await db.execute(stmt.distinct())
    return sorted(result.scalars().all())
