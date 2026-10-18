"""Bridge maintenance cannot grant reach outside an ordinary holder's home."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from shared.bridge_grants import BRIDGE_PERMISSIONS, validate_bridge_placement
from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID
from shared.sdk_users import (
    UserServiceError,
    set_user_base_role,
    validate_bridge_home_change,
)
from src.models.contracts.bridge_grants import RoleBridgeGrantsUpdate
from src.models.contracts.users import RoleCreate, RoleUpdate
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, User
from src.services.authorization.context import Boundary, BoundaryKind
from src.services.role_permissions import (
    RolePermissionError,
    describe_bridge_grants,
    get_role_permissions,
    identity_permissions,
    replace_bridge_grants,
    replace_identity_permissions,
    require_bridge_role_admin,
    set_role_permissions,
)
from src.services.user_role_assignments import RoleAssignmentError, insert_assignment
from tests.helpers.authorization import admin_caller


@pytest.mark.parametrize("permission", sorted(BRIDGE_PERMISSIONS))
@pytest.mark.parametrize(
    "kind", ["platform", "managed_organizations", "foreign", "absent", "null_home"]
)
def test_bridge_rejects_nonhome_placement(permission, kind):
    home = uuid4()
    boundaries = {
        "platform": [Boundary(BoundaryKind.PLATFORM)],
        "managed_organizations": [Boundary(BoundaryKind.MANAGED_ORGANIZATIONS)],
        "foreign": [Boundary(BoundaryKind.ORGANIZATION, uuid4())],
        "absent": [],
        "null_home": [Boundary(BoundaryKind.PLATFORM)],
    }[kind]
    with pytest.raises(ValueError, match="home organization"):
        validate_bridge_placement(
            frozenset({permission}), None if kind == "null_home" else home, boundaries
        )
    validate_bridge_placement(
        frozenset({permission}), home, [Boundary(BoundaryKind.ORGANIZATION, home)]
    )
    validate_bridge_placement(
        frozenset({permission}),
        None,
        [Boundary(BoundaryKind.PLATFORM)],
        holder_is_platform_admin=True,
    )


@pytest.mark.parametrize("model", [RoleCreate, RoleUpdate])
def test_legacy_role_payload_is_explicitly_rejected(model):
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        model(name="legacy", permissions={"can_manage_config": True})


def test_bridge_payload_is_exact_typed_vocabulary_and_requires_both_preconditions():
    for invalid in [
        {
            "permissions": ["roles.readwrite"],
            "expected_permissions": [],
            "expected_version": "0" * 64,
        },
        {"permissions": []},
        {
            "permissions": [],
            "expected_permissions": [],
            "expected_version": "0" * 64,
            "permissions_json": {},
        },
    ]:
        with pytest.raises(ValidationError):
            RoleBridgeGrantsUpdate.model_validate(invalid)
    assert not BRIDGE_PERMISSIONS.intersection(identity_permissions())


async def _role(session):
    role = Role(name=f"bridge-{uuid4()}", created_by="test")
    session.add(role)
    await session.flush()
    return role


async def _user(session, home=True):
    organization = Organization(name=f"bridge-org-{uuid4()}", created_by="test")
    session.add(organization)
    await session.flush()
    user = User(
        email=f"bridge-{uuid4()}@example.com",
        organization_id=organization.id if home else None,
        identity_kind=None if home else "custom",
    )
    session.add(user)
    await session.flush()
    return user


async def _admin(session):
    user = User(
        email=f"bridge-admin-{uuid4()}@example.com",
        organization_id=None,
        is_superuser=True,
    )
    session.add(user)
    await session.flush()
    await insert_assignment(
        session,
        user_id=user.id,
        role_id=PLATFORM_ADMIN_ROLE_ID,
        boundaries=[Boundary(BoundaryKind.PLATFORM)],
        assigned_by="test",
    )
    return admin_caller(user_id=user.id)


@pytest.mark.asyncio
async def test_editor_preserves_unrelated_rows_and_refuses_stale_set_or_version(
    db_session,
):
    role = await _role(db_session)
    admin = await _admin(db_session)
    await set_role_permissions(
        db_session, role_id=role.id, permissions=frozenset({"agents.read"})
    )
    before = await describe_bridge_grants(db_session, admin, role_id=role.id)
    request = RoleBridgeGrantsUpdate(
        permissions=["devices.execute"],
        expected_permissions=[],
        expected_version=before.version,
    )
    after = await replace_bridge_grants(
        db_session, admin, role_id=role.id, request=request
    )
    assert after.permissions == ["devices.execute"]
    assert after.version != before.version
    assert await get_role_permissions(db_session, role_id=role.id) == frozenset(
        {"agents.read", "devices.execute"}
    )
    for stale in [
        request,
        request.model_copy(update={"expected_version": after.version}),
    ]:
        with pytest.raises(RolePermissionError) as exc:
            await replace_bridge_grants(
                db_session, admin, role_id=role.id, request=stale
            )
        assert exc.value.status_code == 409
    with pytest.raises(RolePermissionError) as exc:
        await describe_bridge_grants(
            db_session, SimpleNamespace(ctx=None), role_id=role.id
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("permission", sorted(BRIDGE_PERMISSIONS))
async def test_editor_validates_existing_holders_before_any_grant_write(
    db_session, permission
):
    role = await _role(db_session)
    user = await _user(db_session)
    # A sharing role is initially allowed at a broader scope. Adding bridge
    # power must reject that placement, rather than silently expanding reach.
    await insert_assignment(
        db_session,
        user_id=user.id,
        role_id=role.id,
        boundaries=[Boundary(BoundaryKind.PLATFORM)],
        assigned_by="test",
    )
    admin = await _admin(db_session)
    before = await describe_bridge_grants(db_session, admin, role_id=role.id)
    with pytest.raises(RolePermissionError) as exc:
        await replace_bridge_grants(
            db_session,
            admin,
            role_id=role.id,
            request=RoleBridgeGrantsUpdate(
                permissions=[permission],
                expected_permissions=[],
                expected_version=before.version,
            ),
        )
    assert exc.value.status_code == 409
    assert await get_role_permissions(db_session, role_id=role.id) == frozenset()


@pytest.mark.asyncio
async def test_shared_role_is_home_only_for_each_holder_and_moves_fail_closed(
    db_session,
):
    role = await _role(db_session)
    await set_role_permissions(
        db_session, role_id=role.id, permissions=frozenset({"devices.execute"})
    )
    first = await _user(db_session)
    second = await _user(db_session)
    for user in (first, second):
        await insert_assignment(
            db_session,
            user_id=user.id,
            role_id=role.id,
            boundaries=[Boundary(BoundaryKind.ORGANIZATION, user.organization_id)],
            assigned_by="test",
        )
    with pytest.raises(RoleAssignmentError):
        await insert_assignment(
            db_session,
            user_id=(await _user(db_session)).id,
            role_id=role.id,
            boundaries=[Boundary(BoundaryKind.ORGANIZATION, first.organization_id)],
            assigned_by="test",
        )
    with pytest.raises(UserServiceError):
        await validate_bridge_home_change(db_session, first, second.organization_id)
    null_home = await _user(db_session, home=False)
    with pytest.raises(ValueError):
        await set_user_base_role(db_session, null_home, role.id)


@pytest.mark.asyncio
async def test_permission_cache_invalidation_covers_base_holders(db_session):
    role = await _role(db_session)
    user = await _user(db_session)
    await set_user_base_role(db_session, user, role.id)
    await db_session.flush()
    with patch(
        "shared.role_cache.invalidate_user", new_callable=AsyncMock
    ) as invalidate:
        await set_role_permissions(
            db_session, role_id=role.id, permissions=frozenset({"devices.execute"})
        )
    invalidate.assert_any_await(user.id)


@pytest.mark.asyncio
async def test_stale_superuser_bit_cannot_exempt_holder_or_authorize_editor(db_session):
    role = await _role(db_session)
    user = await _user(db_session)
    user.is_superuser = True
    await db_session.flush()
    await set_role_permissions(
        db_session, role_id=role.id, permissions=frozenset({"devices.execute"})
    )
    with pytest.raises(RoleAssignmentError):
        await insert_assignment(
            db_session,
            user_id=user.id,
            role_id=role.id,
            boundaries=[Boundary(BoundaryKind.PLATFORM)],
            assigned_by="test",
        )
    # Even a caller snapshot claiming Admin must be checked against live rows.
    stale = admin_caller(user_id=user.id)
    with pytest.raises(RolePermissionError) as exc:
        await describe_bridge_grants(db_session, stale, role_id=role.id)
    assert exc.value.status_code == 403
    with pytest.raises(RolePermissionError):
        await require_bridge_role_admin(db_session, stale, role_id=role.id)
    with pytest.raises((RolePermissionError, HTTPException)):
        await replace_identity_permissions(
            db_session, role_id=role.id, permissions=["users.read"], caller=stale
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["engine", "service", "embed", "engine_marker"])
async def test_nonperson_credentials_cannot_edit_bridge_grants_even_when_superuser(
    db_session, kind
):
    from dataclasses import replace
    from src.services.authorization.enforce import Caller

    admin = await _admin(db_session)
    role = await _role(db_session)
    changes = {
        "engine": {"engine_execution_id": uuid4()},
        "service": {"service_id": str(uuid4())},
        "embed": {"embed": True},
        "engine_marker": {"is_engine_token": True},
    }[kind]
    caller = Caller(replace(admin.principal, **changes), admin.ctx)
    with pytest.raises(RolePermissionError) as exc:
        await describe_bridge_grants(db_session, caller, role_id=role.id)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_engine_transport_cannot_assign_remove_or_support_bridge_holders(
    db_session,
):
    from dataclasses import replace

    from shared.sdk_roles import assign_users_to_role, remove_users_from_role
    from shared.sdk_users import update_user
    from src.models.orm.users import UserRole
    from src.services.authorization.enforce import Caller
    from sqlalchemy import select

    admin = await _admin(db_session)
    engine = Caller(replace(admin.principal, engine_execution_id=uuid4()), None)
    role = await _role(db_session)
    await set_role_permissions(
        db_session, role_id=role.id, permissions=frozenset({"devices.execute"})
    )
    user = await _user(db_session)
    with pytest.raises(HTTPException) as exc:
        await assign_users_to_role(
            db_session, engine, role_id=role.id, user_ids=[str(user.id)]
        )
    assert exc.value.status_code == 403
    assert (
        await db_session.scalar(
            select(UserRole.user_id).where(
                UserRole.role_id == role.id, UserRole.user_id == user.id
            )
        )
        is None
    )

    await insert_assignment(
        db_session,
        user_id=user.id,
        role_id=role.id,
        boundaries=[Boundary(BoundaryKind.ORGANIZATION, user.organization_id)],
        assigned_by="test",
    )
    with pytest.raises(HTTPException):
        await remove_users_from_role(
            db_session,
            engine,
            role_id=role.id,
            user_ids=[user.id],
            operation="roles.users.remove",
        )
    before = user.name
    with pytest.raises(HTTPException):
        await update_user(
            db_session,
            engine,
            user_id=str(user.id),
            fields={"name"},
            name="Unauthorized engine support",
        )
    assert user.name == before
    assert (
        await db_session.scalar(
            select(UserRole.user_id).where(
                UserRole.role_id == role.id, UserRole.user_id == user.id
            )
        )
        == user.id
    )


@pytest.mark.asyncio
async def test_mutation_reloads_actor_after_admin_revocation_despite_stale_snapshot(
    db_session,
):
    from sqlalchemy import delete
    from shared.sdk_users import update_user
    from src.models.orm.users import UserRole

    stale = await _admin(db_session)
    user = await _user(db_session)
    await db_session.execute(
        delete(UserRole).where(
            UserRole.user_id == stale.principal.user_id,
            UserRole.role_id == PLATFORM_ADMIN_ROLE_ID,
        )
    )
    # The old Caller and legacy boolean still claim Admin; current rows do not.
    assert stale.is_platform_admin
    with pytest.raises(HTTPException) as exc:
        await update_user(
            db_session,
            stale,
            user_id=str(user.id),
            fields={"name"},
            name="Revoked actor",
        )
    assert exc.value.status_code == 403
    assert user.name != "Revoked actor"
