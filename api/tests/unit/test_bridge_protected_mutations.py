"""Account mutations decide actors and bridge-protected targets under the writer fence."""

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.bridge_grants import lock_bridge_writes
from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID, USER_ROLE_ID
from src.core.principal import UserPrincipal
from src.models.contracts.identities import IdentityUpdate
from src.models.enums import IdentityKind
from src.models.orm.users import Role, RolePermission, User, UserRole
from src.models.orm.organizations import Organization
from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
from src.services.authorization.enforce import Caller
from src.services.identities import delete_identity, rename_identity
from src.services.user_mfa_reset import reset_user_mfa
from src.services.user_role_assignments import insert_assignment
from tests.helpers.authorization import admin_caller, persist_caller

pytestmark = pytest.mark.asyncio
MUTATIONS = ("mfa", "invite", "identity_rename", "identity_delete")


async def _setup(db, *, bridge_permission=None):
    organization = Organization(name=f"bridge-support-{uuid4()}", created_by="test")
    db.add(organization)
    await db.flush()
    target = User(
        email=f"target-{uuid4()}@example.test",
        name="Target",
        organization_id=organization.id,
        identity_kind=IdentityKind.CUSTOM,
        is_active=True,
        mfa_enabled=True,
    )
    role = Role(name=f"bridge-target-{uuid4()}", created_by="test")
    db.add_all([target, role])
    await db.flush()
    if bridge_permission:
        db.add(RolePermission(role_id=role.id, permission=bridge_permission))
        await db.flush()
    await insert_assignment(
        db, user_id=target.id, role_id=role.id,
        boundaries=[Boundary(BoundaryKind.ORGANIZATION, organization.id)], assigned_by="test",
    )
    actor_id = uuid4()
    caller = Caller(
        UserPrincipal(user_id=actor_id, email="support@example.test", organization_id=organization.id),
        AuthorizationContext(
            user_id=actor_id,
            home_organization_id=organization.id,
            base_role_id=USER_ROLE_ID,
            is_external=False,
            base_permissions=frozenset(),
            role_grants=(RoleGrant(
                uuid4(), frozenset({"users.readwrite", "userlifecycle.readwrite"}),
                (Boundary(BoundaryKind.ORGANIZATION, organization.id),),
            ),),
        ),
    )
    return target, role, await persist_caller(db, caller)


async def _mutate(kind, db, caller, target):
    if kind == "mfa":
        return await reset_user_mfa(db, caller, target.id)
    if kind == "invite":
        from src.routers.users import _require_invite_target

        return await _require_invite_target(db, caller, target.id, operation="users.invites.regenerate")
    if kind == "identity_rename":
        return await rename_identity(db, caller, target.id, IdentityUpdate(name="Taken over"))
    return await delete_identity(db, caller, target.id)


@pytest.mark.parametrize("permission", ["devices.readwrite", "devices.execute", "agents.promote"])
@pytest.mark.parametrize("kind", MUTATIONS)
async def test_each_bridge_permission_protects_account_mutations(db_session, permission, kind):
    target, _, caller = await _setup(db_session, bridge_permission=permission)
    with pytest.raises(HTTPException) as refused:
        await _mutate(kind, db_session, caller, target)
    assert refused.value.status_code == 403
    assert target.name == "Target"
    assert target.mfa_enabled is True
    assert await db_session.get(User, target.id) is target


@pytest.mark.parametrize("permission", ["devices.readwrite", "devices.execute", "agents.promote"])
@pytest.mark.parametrize("kind", MUTATIONS)
async def test_current_human_admin_may_manage_bridge_holders(db_session, permission, kind):
    target, _, _ = await _setup(db_session, bridge_permission=permission)
    admin = await persist_caller(db_session, admin_caller())
    with patch("src.routers.auth.revoke_all_user_refresh_tokens", new=AsyncMock(return_value=0)):
        await _mutate(kind, db_session, admin, target)
    if kind == "identity_delete":
        assert await db_session.get(User, target.id) is None
    elif kind == "identity_rename":
        assert target.name == "Taken over"
    elif kind == "mfa":
        assert target.mfa_enabled is False


@pytest.mark.parametrize("kind", MUTATIONS)
async def test_revoked_admin_context_cannot_mutate_a_target(db_session, kind):
    target, _, _ = await _setup(db_session)
    stale_admin = await persist_caller(db_session, admin_caller())
    await db_session.execute(delete(UserRole).where(
        UserRole.user_id == stale_admin.principal.user_id,
        UserRole.role_id == PLATFORM_ADMIN_ROLE_ID,
    ))
    await db_session.flush()
    assert stale_admin.is_platform_admin

    with pytest.raises(HTTPException) as refused:
        await _mutate(kind, db_session, stale_admin, target)
    assert refused.value.status_code == 403
    assert target.name == "Target"
    assert target.mfa_enabled is True


@pytest.mark.parametrize("kind", MUTATIONS)
async def test_cached_target_home_is_refreshed_before_mutation(db_session, kind):
    target, _, caller = await _setup(db_session)
    previous_home = target.organization_id
    foreign = Organization(name=f"foreign-support-{uuid4()}", created_by="test")
    db_session.add(foreign)
    await db_session.flush()
    await db_session.execute(update(User).where(User.id == target.id).values(
        organization_id=foreign.id,
    ).execution_options(synchronize_session=False))
    assert target.organization_id == previous_home

    with pytest.raises(HTTPException) as refused:
        await _mutate(kind, db_session, caller, target)
    assert refused.value.status_code == 403
    assert target.organization_id == foreign.id


@pytest.mark.parametrize("kind", MUTATIONS)
@pytest.mark.parametrize("credential", ["engine", "service"])
async def test_execution_credential_cannot_manage_a_bridge_holder(db_session, kind, credential):
    target, _, caller = await _setup(db_session, bridge_permission="devices.execute")
    admin = await persist_caller(db_session, admin_caller())
    execution = Caller(UserPrincipal(
        user_id=caller.principal.user_id,
        email=caller.principal.email,
        organization_id=caller.principal.organization_id,
        is_superuser=True,
        engine_execution_id=str(uuid4()) if credential == "engine" else None,
        service_id=str(uuid4()) if credential == "service" else None,
        run_user_id=admin.principal.user_id,
    ), None)
    with pytest.raises(HTTPException) as refused:
        await _mutate(kind, db_session, execution, target)
    assert refused.value.status_code == 403
    assert target.name == "Target"
    assert target.mfa_enabled is True


async def test_grant_committed_while_support_waits_is_seen_before_authorization(async_engine, monkeypatch):
    """Two transactions: granting wins the fence, then support sees a protected target."""
    async with AsyncSession(async_engine, expire_on_commit=False) as seed:
        target, role, caller = await _setup(seed)
        home = target.organization_id
        assert caller.ctx is not None
        role_ids = [role.id, *(grant.role_id for grant in caller.ctx.role_grants)]
        await seed.commit()
    try:
        async with AsyncSession(async_engine) as writer, AsyncSession(async_engine) as reader:
            await lock_bridge_writes(writer)
            writer.add(RolePermission(role_id=role.id, permission="devices.execute"))
            await writer.flush()
            waiting = asyncio.Event()
            execute = reader.execute

            async def execute_until_fence(statement, *args, **kwargs):
                if "pg_advisory_xact_lock" in str(statement):
                    waiting.set()
                return await execute(statement, *args, **kwargs)

            monkeypatch.setattr(reader, "execute", execute_until_fence)
            mutation = asyncio.create_task(_mutate("identity_rename", reader, caller, target))
            try:
                await asyncio.wait_for(waiting.wait(), timeout=5)
                assert not mutation.done()
                await writer.commit()
                with pytest.raises(HTTPException) as refused:
                    await asyncio.wait_for(mutation, timeout=5)
                assert refused.value.status_code == 403
                await reader.rollback()
                assert await reader.scalar(text("SELECT name FROM users WHERE id=:id"), {"id": target.id}) == "Target"
            finally:
                if not mutation.done():
                    mutation.cancel()
                    await asyncio.gather(mutation, return_exceptions=True)
    finally:
        async with AsyncSession(async_engine) as cleanup:
            await cleanup.execute(delete(Organization).where(Organization.id == home))
            await cleanup.execute(delete(Role).where(Role.id.in_(role_ids)))
            await cleanup.commit()
