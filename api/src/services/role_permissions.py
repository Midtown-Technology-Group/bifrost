"""Get/set a role's permission set, with vocabulary validation.

Builtin roles (Platform Admin, User, Platform Operator, Secrets Reader —
see ``shared.builtin_roles``) cannot be modified through this service: their
permission sets are fixed by migration/seed data. Platform Admin's access is
the wildcard permission, stored as its one ``role_permissions`` row like any
other role's permissions; no custom role can be given it. Custom roles' identity permissions are edited through
``replace_identity_permissions``; their other permissions are not editable
from the API yet.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.builtin_roles import BUILTIN_ROLE_IDS, WILDCARD_PERMISSION
from shared.bridge_grants import (
    BRIDGE_PERMISSIONS,
    is_current_platform_admin,
    lock_bridge_writes,
    refresh_mutation_caller,
    validate_bridge_placement,
)
from src.models.contracts.permissions import parse_permission
from src.models.contracts.bridge_grants import (
    RoleBridgeGrantsResponse,
    RoleBridgeGrantsUpdate,
)

if TYPE_CHECKING:
    from src.models.orm.users import Role
    from src.services.authorization.enforce import Caller


class RolePermissionError(Exception):
    """Permission-set operation failure with an HTTP-style status."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def validate_permission(permission: str) -> None:
    """Raise ``RolePermissionError(422, ...)`` unless `permission` is a
    well-formed ``<resource>.<action>[.all]`` string in the closed
    ``PERMISSION_DOMAINS`` vocabulary (see ``parse_permission``)."""
    try:
        parse_permission(permission)
    except ValueError as exc:
        raise RolePermissionError(422, str(exc)) from exc


async def get_role_permissions(
    session: AsyncSession, *, role_id: UUID
) -> frozenset[str]:
    """The role's permission set."""
    from src.models import RolePermission as RolePermissionORM

    result = await session.execute(
        select(RolePermissionORM.permission).where(RolePermissionORM.role_id == role_id)
    )
    return frozenset(result.scalars().all())


async def set_role_permissions(
    session: AsyncSession, *, role_id: UUID, permissions: frozenset[str]
) -> None:
    """Replace a role's permission set wholesale. Refuses builtin roles (409)."""
    if role_id in BUILTIN_ROLE_IDS:
        raise RolePermissionError(409, "Builtin roles cannot be modified")
    for permission in permissions:
        validate_permission(permission)

    from src.models import RolePermission as RolePermissionORM
    from src.models import Role as RoleORM

    await lock_bridge_writes(session)
    role = await session.scalar(
        select(RoleORM).where(RoleORM.id == role_id).with_for_update()
    )
    if role is None:
        raise RolePermissionError(404, "Role not found")
    if role.is_builtin:
        raise RolePermissionError(409, "Builtin roles cannot be modified")
    if permissions & BRIDGE_PERMISSIONS:
        await _validate_bridge_holders(
            session, role_id=role_id, permissions=permissions
        )

    await session.execute(
        delete(RolePermissionORM).where(RolePermissionORM.role_id == role_id)
    )
    for permission in permissions:
        session.add(RolePermissionORM(role_id=role_id, permission=permission))
    role.updated_at = datetime.now(timezone.utc)
    await session.flush()
    await _invalidate_permissions(session, role_id=role_id)


async def role_has_permission(
    session: AsyncSession, *, role_ids: list[UUID], permission: str
) -> bool:
    """Whether any of `role_ids` grants `permission`: it holds the permission,
    or the wildcard, which satisfies everything but
    ``WILDCARD_EXCLUDED_PERMISSIONS``."""
    if not role_ids:
        return False

    from src.models import RolePermission as RolePermissionORM
    from src.models.contracts.permissions import WILDCARD_EXCLUDED_PERMISSIONS

    satisfying = {permission}
    if permission not in WILDCARD_EXCLUDED_PERMISSIONS:
        satisfying.add(WILDCARD_PERMISSION)
    result = await session.execute(
        select(RolePermissionORM.role_id)
        .where(
            RolePermissionORM.role_id.in_(role_ids),
            RolePermissionORM.permission.in_(satisfying),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


# The permission domains a role's permission set can be edited for through
# ``PUT /api/roles/{role_id}/permissions`` (R3a). Other domains a role holds
# (for example ``agents.readwrite``, which agent promotion reads) are kept
# as they are.
IDENTITY_PERMISSION_DOMAINS = frozenset(
    {"users", "userlifecycle", "organizations", "roleassignments", "roles"}
)


def identity_permissions() -> tuple[str, ...]:
    """The identity permissions an editor offers, sorted: every permission
    the permission catalog lists for an identity domain (what the routes are
    decided by, and the privileged ones no route names, such as Impersonate
    Users) and the narrower per-field permissions."""
    from src.services.authorization.enforce import NARROWER_PERMISSIONS
    from src.services.permission_catalog import build_catalog

    found = {
        f"{entry.domain}.{action}"
        for entry in build_catalog()
        for action in entry.actions
    }
    found |= {p for permissions in NARROWER_PERMISSIONS.values() for p in permissions}
    return tuple(
        sorted(
            p
            for p in found
            if parse_permission(p).domain in IDENTITY_PERMISSION_DOMAINS
        )
    )


def _item(permission: str, *, editable: bool):
    from src.models.contracts.permissions import PRIVILEGED_PERMISSIONS
    from src.models.contracts.role_assignments import RolePermissionItem

    return RolePermissionItem(
        permission=permission,
        editable=editable,
        privileged=permission == WILDCARD_PERMISSION
        or permission in PRIVILEGED_PERMISSIONS,
    )


async def describe_role_permissions(session: AsyncSession, *, role_id: UUID):
    """Every permission the role holds, and the identity permissions an
    editor may choose from. Builtin roles are listed read-only."""
    from src.models import Role as RoleORM
    from src.models.contracts.role_assignments import RolePermissionsResponse

    role = await session.get(RoleORM, role_id)
    if role is None:
        raise RolePermissionError(404, "Role not found")
    editable_vocabulary = set(identity_permissions()) if not role.is_builtin else set()
    held = await get_role_permissions(session, role_id=role_id)
    return RolePermissionsResponse(
        role_id=role_id,
        is_builtin=role.is_builtin,
        permissions=[_item(p, editable=p in editable_vocabulary) for p in sorted(held)],
        identity_permissions=[
            _item(p, editable=not role.is_builtin) for p in identity_permissions()
        ],
    )


async def replace_identity_permissions(
    session: AsyncSession, *, role_id: UUID, permissions: list[str], caller=None
):
    """Replace the role's identity permissions, keeping every other
    permission it holds. 422 for anything outside the identity set, 409 for
    a builtin role, 404 for a missing one."""
    from src.models import Role as RoleORM
    from src.services.audit import emit_audit

    if caller is not None:
        from src.services.authorization.enforce import GLOBAL, require_operation

        caller = await refresh_mutation_caller(session, caller)
        require_operation(caller, "PUT /api/roles/{role_id}/permissions", GLOBAL)
    else:
        await lock_bridge_writes(session)
    role = await session.scalar(
        select(RoleORM).where(RoleORM.id == role_id).with_for_update()
    )
    if role is None:
        raise RolePermissionError(404, "Role not found")
    if role_id in BUILTIN_ROLE_IDS or role.is_builtin:
        raise RolePermissionError(409, "Builtin roles cannot be modified")
    vocabulary = set(identity_permissions())
    for permission in permissions:
        validate_permission(permission)
        if permission not in vocabulary:
            raise RolePermissionError(
                422, f"{permission!r} is not an identity permission editable here"
            )
    held = await get_role_permissions(session, role_id=role_id)
    if held & BRIDGE_PERMISSIONS:
        if caller is None:
            raise RolePermissionError(
                403, "Bridge-bearing roles require a Platform Admin actor"
            )
        await _require_bridge_admin(session, caller)
    kept = {
        p for p in held if parse_permission(p).domain not in IDENTITY_PERMISSION_DOMAINS
    }
    before = sorted(held)
    await set_role_permissions(
        session, role_id=role_id, permissions=frozenset(kept | set(permissions))
    )
    await emit_audit(
        session,
        "role.permissions_updated",
        resource_type="role",
        resource_id=role_id,
        details={"before": before, "after": sorted(kept | set(permissions))},
    )
    return await describe_role_permissions(session, role_id=role_id)


async def require_bridge_role_admin(session: AsyncSession, caller, *, role_id: UUID):
    """Deleting or changing a bridge-bearing role is an Admin-only mutation."""
    from src.models.orm.users import Role

    caller = await refresh_mutation_caller(session, caller)
    await session.scalar(select(Role.id).where(Role.id == role_id).with_for_update())
    if await get_role_permissions(session, role_id=role_id) & BRIDGE_PERMISSIONS:
        await _require_bridge_admin(session, caller)
    return caller


async def _invalidate_permissions(session: AsyncSession, *, role_id: UUID) -> None:
    from shared.role_cache import invalidate_role as invalidate_role_cache
    from shared.role_cache import invalidate_user
    from src.core.cache import invalidate_role, invalidate_role_users
    from src.models.orm.users import User

    await invalidate_role(None, str(role_id))
    await invalidate_role_users(None, str(role_id))
    await invalidate_role_cache(role_id)
    # The legacy per-user cache tracks additional role ids; custom base-role
    # holders must also lose their entries when their grants change.
    for user_id in (
        await session.execute(select(User.id).where(User.base_role_id == role_id))
    ).scalars():
        await invalidate_user(user_id)


async def _validate_bridge_holders(
    session: AsyncSession, *, role_id: UUID, permissions: frozenset[str]
) -> None:
    from src.models.orm.users import User, UserRole, UserRoleBoundary
    from src.services.authorization.context import Boundary, BoundaryKind

    additional = (
        (
            await session.execute(
                select(User)
                .join(UserRole, UserRole.user_id == User.id)
                .where(UserRole.role_id == role_id)
            )
        )
        .scalars()
        .all()
    )
    bases = (
        (await session.execute(select(User).where(User.base_role_id == role_id)))
        .scalars()
        .all()
    )
    for user in bases:
        boundaries = (
            []
            if user.organization_id is None
            else [Boundary(BoundaryKind.ORGANIZATION, user.organization_id)]
        )
        try:
            validate_bridge_placement(
                permissions,
                user.organization_id,
                boundaries,
                holder_is_platform_admin=await is_current_platform_admin(
                    session, user.id
                ),
            )
        except ValueError as exc:
            raise RolePermissionError(409, str(exc)) from exc
    for user in additional:
        rows = (
            (
                await session.execute(
                    select(UserRoleBoundary).where(
                        UserRoleBoundary.user_id == user.id,
                        UserRoleBoundary.role_id == role_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        boundaries = [
            Boundary(BoundaryKind(row.kind), row.organization_id) for row in rows
        ]
        try:
            validate_bridge_placement(
                permissions,
                user.organization_id,
                boundaries,
                holder_is_platform_admin=await is_current_platform_admin(
                    session, user.id
                ),
            )
        except ValueError as exc:
            raise RolePermissionError(409, str(exc)) from exc


def _bridge_version(role: Role, permissions: frozenset[str]) -> str:
    value = "\n".join([str(role.id), role.updated_at.isoformat(), *sorted(permissions)])
    return sha256(value.encode()).hexdigest()


async def _require_bridge_admin(session: AsyncSession, caller: Caller) -> Caller:
    if (
        caller.ctx is None
        or caller.is_execution_credential
        or caller.principal.embed
        or caller.principal.is_engine_token
    ):
        raise RolePermissionError(403, "Only a Platform Admin can manage bridge grants")
    caller = await refresh_mutation_caller(session, caller)
    if caller.ctx is None or not caller.ctx.is_platform_admin:
        raise RolePermissionError(403, "Only a Platform Admin can manage bridge grants")
    return caller


async def describe_bridge_grants(
    session: AsyncSession, caller: Caller, *, role_id: UUID
) -> RoleBridgeGrantsResponse:
    from src.models import Role as RoleORM

    caller = await _require_bridge_admin(session, caller)
    role = await session.get(RoleORM, role_id, populate_existing=True)
    if role is None:
        raise RolePermissionError(404, "Role not found")
    held = await get_role_permissions(session, role_id=role_id)
    return RoleBridgeGrantsResponse.model_validate(
        {
            "role_id": role_id,
            "is_builtin": role.is_builtin,
            "permissions": sorted(held & BRIDGE_PERMISSIONS),
            "version": _bridge_version(role, held),
        }
    )


async def replace_bridge_grants(
    session: AsyncSession,
    caller: Caller,
    *,
    role_id: UUID,
    request: RoleBridgeGrantsUpdate,
) -> RoleBridgeGrantsResponse:
    """Replace just the approved bridge rows with a locked current-set/version CAS."""
    from src.models import Role as RoleORM
    from src.services.audit import emit_audit
    from src.services.audit_context import ActorContext, current_actor

    caller = await _require_bridge_admin(session, caller)
    role = await session.scalar(
        select(RoleORM).where(RoleORM.id == role_id).with_for_update()
    )
    if role is None:
        raise RolePermissionError(404, "Role not found")
    if role.is_builtin or role_id in BUILTIN_ROLE_IDS:
        raise RolePermissionError(409, "Builtin roles cannot be modified")
    held = await get_role_permissions(session, role_id=role_id)
    before = held & BRIDGE_PERMISSIONS
    requested = frozenset(request.permissions)
    if not requested <= BRIDGE_PERMISSIONS:
        raise RolePermissionError(
            422, "Only the three bridge permissions are editable here"
        )
    if frozenset(
        request.expected_permissions
    ) != before or request.expected_version != _bridge_version(role, held):
        raise RolePermissionError(409, "Bridge grants changed; reload before saving")
    await set_role_permissions(
        session, role_id=role_id, permissions=(held - BRIDGE_PERMISSIONS) | requested
    )
    await emit_audit(
        session,
        "role.bridge_grants_updated",
        resource_type="role",
        resource_id=role_id,
        details={
            "before": sorted(before),
            "after": sorted(requested),
            "expected_version": request.expected_version,
        },
        actor_override=current_actor()
        or ActorContext(
            user_id=caller.principal.user_id,
            organization_id=caller.ctx.home_organization_id if caller.ctx else None,
            email=caller.principal.email,
        ),
        strict=True,
    )
    return await describe_bridge_grants(session, caller, role_id=role_id)
