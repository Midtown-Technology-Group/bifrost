"""Authorization callers for tests of evaluator-gated services and handlers."""

from __future__ import annotations

import importlib
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID, USER_ROLE_ID, WILDCARD_PERMISSION
from src.core.principal import UserPrincipal
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.authorization.enforce import Caller


def platform_admin_grant() -> RoleGrant:
    """The Platform Admin assignment: an additional role at the platform
    boundary, never a base role."""
    return RoleGrant(
        PLATFORM_ADMIN_ROLE_ID,
        frozenset({WILDCARD_PERMISSION}),
        (Boundary(BoundaryKind.PLATFORM),),
    )


def admin_caller(email: str = "admin@example.com", user_id: UUID | None = None) -> Caller:
    """A Platform Admin caller, its context built in memory as
    ``load_caller`` would read it for an admin."""
    principal = UserPrincipal(
        user_id=user_id or uuid4(),
        email=email,
        organization_id=None,
        name="Admin",
        is_superuser=True,
        is_verified=True,
    )
    return Caller(
        principal,
        AuthorizationContext(
            user_id=principal.user_id,
            home_organization_id=None,
            base_role_id=USER_ROLE_ID,
            is_external=False,
            base_permissions=frozenset(),
            role_grants=(platform_admin_grant(),),
        ),
    )


async def persist_caller(db, caller: Caller) -> Caller:
    """Seed a synthetic person's exact grants for services that reload actors.

    Existing actors are only read, so this helper cannot restore revoked
    authority. Builtin role rows are likewise never rewritten by a fixture.
    """
    from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary
    from src.services.authorization.enforce import load_caller

    ctx = caller.ctx
    if ctx is None:
        raise ValueError("A persisted test caller must have a human authorization context")
    if await db.get(User, caller.principal.user_id) is not None:
        return await load_caller(db, caller.principal)

    roles = {ctx.base_role_id: ctx.base_permissions}
    roles.update((grant.role_id, grant.permissions) for grant in ctx.role_grants)
    for role_id, permissions in roles.items():
        if await db.get(Role, role_id) is None:
            db.add(Role(id=role_id, name=f"test-caller-role-{role_id}", created_by="test"))
            await db.flush()
            db.add_all(RolePermission(role_id=role_id, permission=value) for value in permissions)
    await db.flush()
    db.add(User(
        id=caller.principal.user_id,
        email=f"caller-{caller.principal.user_id}@example.test",
        organization_id=ctx.home_organization_id,
        base_role_id=ctx.base_role_id,
        is_active=True,
        is_verified=True,
        is_external=ctx.is_external,
        is_superuser=ctx.is_platform_admin,
    ))
    await db.flush()
    for grant in ctx.role_grants:
        db.add(UserRole(user_id=ctx.user_id, role_id=grant.role_id, assigned_by="test"))
        await db.flush()
        db.add_all(UserRoleBoundary(
            user_id=ctx.user_id,
            role_id=grant.role_id,
            kind=boundary.kind.value,
            organization_id=boundary.organization_id,
        ) for boundary in grant.boundaries)
    await db.flush()
    return await load_caller(db, caller.principal)


@contextmanager
def handlers_as(caller: Caller, module: str):
    """Make the handlers in ``module`` load ``caller`` instead of reading
    the database, for router-boundary tests with a mocked session."""
    with ExitStack() as stack:
        for name in ("load_caller", "authorize_operation"):
            if hasattr(importlib.import_module(module), name):
                stack.enter_context(patch(f"{module}.{name}", new=AsyncMock(return_value=caller)))
        yield caller
