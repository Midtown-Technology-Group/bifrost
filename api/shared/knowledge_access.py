"""Authorization helpers for direct knowledge-namespace reads."""

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.principal import PLATFORM_ADMIN_ROLE_NAMES, UserPrincipal
from src.models.orm.knowledge_sources import KnowledgeNamespaceRole
from src.models.orm.users import UserRole


async def accessible_knowledge_namespaces(
    session: AsyncSession,
    user: UserPrincipal,
) -> set[str] | None:
    """Return the namespaces a caller may read directly.

    ``None`` means unrestricted platform-admin or delegated-engine access.
    Ordinary users need a role assignment for the namespace in either their
    home organization or global scope. Organization filtering remains a
    separate, additional gate on the documents themselves.
    """
    if (user.engine_execution_id and not user.embed) or user.is_superuser or any(
        role in PLATFORM_ADMIN_ROLE_NAMES for role in getattr(user, "roles", [])
    ):
        return None

    organization_id: UUID | None = user.organization_id
    result = await session.execute(
        select(KnowledgeNamespaceRole.namespace)
        .join(UserRole, UserRole.role_id == KnowledgeNamespaceRole.role_id)
        .where(
            UserRole.user_id == user.user_id,
            or_(
                KnowledgeNamespaceRole.organization_id.is_(None),
                KnowledgeNamespaceRole.organization_id == organization_id,
            ),
        )
        .distinct()
    )
    return set(result.scalars().all())
