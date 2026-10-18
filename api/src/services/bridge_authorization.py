"""Target-aware authority for the three MTG legacy grant bridge permissions.

This is intentionally narrower than general organization reach: only current
home-organization grants apply to ordinary people, never platform/managed org
boundaries. Callers must be loaded from current database rows with load_caller.
"""

from uuid import UUID

from src.services.authorization.context import BoundaryKind
from src.services.authorization.enforce import Caller


BRIDGE_PERMISSIONS = frozenset({"devices.readwrite", "devices.execute", "agents.promote"})


def bridge_permission_allowed(
    caller: Caller, permission: str, organization_id: UUID | None
) -> bool:
    if permission not in BRIDGE_PERMISSIONS:
        raise ValueError(f"Not a bridge permission: {permission}")
    if caller.principal.embed or caller.is_execution_credential or caller.principal.is_engine_token:
        return False
    ctx = caller.ctx
    if ctx is None or not ctx.is_active:
        return False
    if ctx.is_platform_admin:
        return True
    if organization_id is None or ctx.home_organization_id != organization_id:
        return False
    return any(
        held == permission
        and boundary.kind == BoundaryKind.ORGANIZATION
        and boundary.organization_id == organization_id
        for held, boundary in ctx.effective_grants
    )
