"""Frozen MTG bridge vocabulary and placement invariant.

This module contains no legacy JSON reader. Migration mappings are literals
inside their own revisions, never imports of this runtime contract.
"""

from uuid import UUID

BRIDGE_PERMISSIONS = frozenset(
    {"devices.readwrite", "devices.execute", "agents.promote"}
)
BRIDGE_HOME_MESSAGE = (
    "Bridge roles require only the holder's home organization boundary"
)


async def is_current_platform_admin(session, user_id: UUID) -> bool:
    """Read the effective wildcard from current rows; never trust is_superuser."""
    from sqlalchemy.exc import NoResultFound
    from src.services.authorization.context import build_authorization_context

    try:
        return (await build_authorization_context(session, user_id)).is_platform_admin
    except NoResultFound:
        return False


async def lock_bridge_writes(session) -> None:
    """Serialize role-grant, membership, base-role and home-org writers.

    A transaction-scoped lock closes the no-current-membership race between
    a home-org move and first assignment. Role rows are additionally locked
    by the writers; all callers take this lock before their row locks.
    """
    from sqlalchemy import text

    await session.execute(text("SELECT pg_advisory_xact_lock(1297368898)"))


async def refresh_mutation_caller(session, caller):
    """Acquire the writer fence, then rebuild a person's authorization rows.

    Execution credentials retain their explicitly defined transport policy;
    the bridge-grant editor separately refuses those credential lanes.
    """
    from sqlalchemy import select
    from src.models.orm.users import User
    from src.services.authorization.enforce import Caller, load_caller

    await lock_bridge_writes(session)
    if not caller.is_execution_credential:
        active = await session.scalar(
            select(User.is_active).where(User.id == caller.principal.user_id)
        )
        if active is not True:
            return Caller(caller.principal, None)
    return await load_caller(session, caller.principal)


async def require_bridge_management_admin(session, caller, permissions):
    """Bridge roles/targets may be changed only by a current human Admin.

    Existing transport rules remain intact for targets without bridge powers.
    An engine or service's system transport flag never authorizes management.
    """
    from fastapi import HTTPException

    if not BRIDGE_PERMISSIONS.intersection(permissions):
        return
    if (
        caller.is_execution_credential
        or caller.principal.embed
        or caller.principal.is_engine_token
    ):
        raise HTTPException(
            403,
            "Only a current Platform Admin user can manage bridge roles or their holders",
        )
    current = await refresh_mutation_caller(session, caller)
    if current.ctx is None or not current.ctx.is_platform_admin:
        raise HTTPException(
            403,
            "Only a current Platform Admin user can manage bridge roles or their holders",
        )


async def require_bridge_target_admin(session, caller, target_id: UUID):
    """Protect bridge-bearing account targets, including custom base roles."""
    from src.services.authorization.enforce import held_permissions_by_user

    permissions = (await held_permissions_by_user(session, [target_id])).get(
        target_id, frozenset()
    )
    await require_bridge_management_admin(session, caller, permissions)


def validate_bridge_placement(
    permissions: frozenset[str],
    home_organization_id: UUID | None,
    boundaries,
    *,
    holder_is_platform_admin: bool = False,
) -> None:
    """Reject null-home, platform, managed and foreign placement for ordinary holders."""
    if not BRIDGE_PERMISSIONS.intersection(permissions) or holder_is_platform_admin:
        return
    if home_organization_id is None:
        raise ValueError(BRIDGE_HOME_MESSAGE)
    actual = {
        (boundary.kind.value, boundary.organization_id) for boundary in boundaries
    }
    if actual != {("organization", home_organization_id)}:
        raise ValueError(BRIDGE_HOME_MESSAGE)
