"""Service unit tests + router thin-boundary tests for user operations.

The business behavior behind the five ``api/bifrost/users.py`` SDK
methods (``list``, ``create``, ``get``, ``update``, ``delete``) lives in
``shared.sdk_users``; the HTTP handlers in ``api/src/routers/users.py``
are thin delegates. These tests pin:

- the service against a real DB: success paths, error precedence
  (self-delete 400 before 404/403, 404 before 403), system-user
  protection, provider-org promotion on superuser grant, invite
  creation on create, and list filtering/pagination;
- the router boundary with the service mocked: delegation arguments,
  ``UserServiceError`` -> ``HTTPException`` mapping, and the
  ``X-Total-Count`` header.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response

from shared.sdk_users import UserServiceError


def _principal(email: str = "admin@example.com", user_id=None):
    from src.core.principal import UserPrincipal

    return UserPrincipal(
        user_id=user_id or uuid4(),
        email=email,
        organization_id=None,
        name="Admin",
        is_superuser=True,
        is_verified=True,
    )


def _stub_actor(email: str = "admin@example.com", user_id=None):
    return SimpleNamespace(user_id=user_id or uuid4(), email=email)


async def _seed_org(db_session):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=f"sdk-users-org-{uuid4().hex[:8]}",
        is_active=True,
        created_by="sdk-users-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.mark.asyncio
class TestUserService:
    async def test_create_get_round_trip(self, db_session):
        from shared.sdk_users import create_user, get_user

        org = await _seed_org(db_session)
        actor_id = uuid4()
        created = await create_user(
            db_session,
            email=f"round-{uuid4().hex[:8]}@example.com",
            name="Round Trip",
            is_active=True,
            is_superuser=False,
            is_external=False,
            organization_id=org.id,
            actor_user_id=actor_id,
        )
        assert created.email.startswith("round-")
        assert created.is_verified is True
        assert created.is_registered is False
        assert created.invite_status == "pending"
        assert created.registration_url is not None
        assert "/accept-invite?token=" in created.registration_url

        fetched = await get_user(db_session, user_id=str(created.id))
        assert fetched.id == created.id
        assert fetched.email == created.email

        by_email = await get_user(db_session, user_id=created.email)
        assert by_email.id == created.id

    async def test_get_missing_raises_404(self, db_session):
        from shared.sdk_users import get_user

        with pytest.raises(UserServiceError) as exc_info:
            await get_user(db_session, user_id=str(uuid4()))
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "User not found"

    async def test_list_excludes_system_and_inactive_by_default(self, db_session):
        from src.models import User as UserORM
        from shared.sdk_users import create_user, list_users

        org = await _seed_org(db_session)
        active = await create_user(
            db_session,
            email=f"active-{uuid4().hex[:8]}@example.com",
            name="Active",
            organization_id=org.id,
            actor_user_id=uuid4(),
        )
        inactive = await create_user(
            db_session,
            email=f"inactive-{uuid4().hex[:8]}@example.com",
            name="Inactive",
            is_active=False,
            organization_id=org.id,
            actor_user_id=uuid4(),
        )
        sys_user = UserORM(
            email=f"sys-{uuid4().hex[:8]}@example.com",
            name="Sys",
            is_system=True,
            is_superuser=True,
        )
        db_session.add(sys_user)
        await db_session.flush()

        items, _total = await list_users(
            db_session, _principal(), search=active.email[:12]
        )
        assert [u.id for u in items] == [active.id]

        items, _total = await list_users(
            db_session,
            _principal(),
            search="@example.com",
            include_inactive=True,
        )
        ids = {u.id for u in items}
        assert active.id in ids
        assert inactive.id in ids
        assert sys_user.id not in ids

    async def test_list_type_search_pagination_and_scope(self, db_session):
        from shared.sdk_users import create_user, list_users

        org = await _seed_org(db_session)
        tag = uuid4().hex[:8]
        admin = await create_user(
            db_session,
            email=f"plat-{tag}@example.com",
            name=f"Tag {tag} Zulu",
            is_superuser=True,
            actor_user_id=uuid4(),
        )
        member = await create_user(
            db_session,
            email=f"member-{tag}@example.com",
            name=f"Tag {tag} Alpha",
            organization_id=org.id,
            actor_user_id=uuid4(),
        )

        items, total = await list_users(
            db_session, _principal(), type="platform", search=tag
        )
        assert total == 1
        assert [u.id for u in items] == [admin.id]

        items, total = await list_users(
            db_session, _principal(), type="org", search=tag
        )
        assert total == 1
        assert [u.id for u in items] == [member.id]

        items, total = await list_users(
            db_session,
            _principal(),
            search=tag,
            sort_by="name",
            sort_direction="asc",
            limit=1,
            offset=0,
        )
        assert total == 2
        assert [u.name for u in items] == [f"Tag {tag} Alpha"]

        items, _ = await list_users(
            db_session,
            _principal(),
            search=tag,
            sort_by="name",
            sort_direction="asc",
            limit=1,
            offset=1,
        )
        assert [u.name for u in items] == [f"Tag {tag} Zulu"]

        items, total = await list_users(
            db_session, _principal(), scope=str(org.id), search=tag
        )
        assert total == 1
        assert items[0].id == member.id

    async def test_list_invalid_scope_422(self, db_session):
        from shared.sdk_users import list_users

        with pytest.raises(UserServiceError) as exc_info:
            await list_users(db_session, _principal(), scope="not-a-scope")
        assert exc_info.value.status_code == 422

    async def test_update_partial_promote_and_protection(self, db_session):
        from src.core.constants import PROVIDER_ORG_ID
        from src.models import User as UserORM
        from shared.sdk_users import create_user, update_user

        org = await _seed_org(db_session)
        created = await create_user(
            db_session,
            email=f"upd-{uuid4().hex[:8]}@example.com",
            name="Before",
            organization_id=org.id,
            actor_user_id=uuid4(),
        )
        updated = await update_user(
            db_session, user_id=str(created.id), name="After"
        )
        assert updated.name == "After"
        assert updated.organization_id == org.id

        promoted = await update_user(
            db_session, user_id=str(created.id), is_superuser=True
        )
        assert promoted.is_superuser is True
        assert promoted.organization_id == PROVIDER_ORG_ID

        with pytest.raises(UserServiceError) as exc_info:
            await update_user(db_session, user_id=str(uuid4()), name="x")
        assert exc_info.value.status_code == 404

        sys_user = UserORM(
            email=f"sysupd-{uuid4().hex[:8]}@example.com", is_system=True,
            is_superuser=True,
        )
        db_session.add(sys_user)
        await db_session.flush()
        with pytest.raises(UserServiceError) as exc_info:
            await update_user(db_session, user_id=str(sys_user.id), name="x")
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "System user cannot be modified"

    async def test_delete_self_400_before_404(self, db_session):
        from shared.sdk_users import delete_user

        actor_id = uuid4()
        with pytest.raises(UserServiceError) as exc_info:
            await delete_user(
                db_session,
                user_id=str(actor_id),
                actor_user_id=actor_id,
                actor_email="admin@example.com",
            )
        assert exc_info.value.status_code == 400

        with pytest.raises(UserServiceError) as exc_info:
            await delete_user(
                db_session,
                user_id="admin@example.com",
                actor_user_id=uuid4(),
                actor_email="admin@example.com",
            )
        assert exc_info.value.status_code == 400

    async def test_delete_missing_system_and_success(self, db_session):
        from src.models import User as UserORM
        from shared.sdk_users import create_user, delete_user, get_user

        with pytest.raises(UserServiceError) as exc_info:
            await delete_user(
                db_session,
                user_id=str(uuid4()),
                actor_user_id=uuid4(),
                actor_email="admin@example.com",
            )
        assert exc_info.value.status_code == 404

        sys_user = UserORM(
            email=f"sysdel-{uuid4().hex[:8]}@example.com", is_system=True,
            is_superuser=True,
        )
        db_session.add(sys_user)
        await db_session.flush()
        with pytest.raises(UserServiceError) as exc_info:
            await delete_user(
                db_session,
                user_id=str(sys_user.id),
                actor_user_id=uuid4(),
                actor_email="admin@example.com",
            )
        assert exc_info.value.status_code == 403

        created = await create_user(
            db_session,
            email=f"del-{uuid4().hex[:8]}@example.com",
            name="Gone",
            is_superuser=True,
            actor_user_id=uuid4(),
        )
        deleted_id = await delete_user(
            db_session,
            user_id=str(created.id),
            actor_user_id=uuid4(),
            actor_email="admin@example.com",
        )
        assert deleted_id == created.id
        with pytest.raises(UserServiceError):
            await get_user(db_session, user_id=str(created.id))


@pytest.mark.asyncio
class TestUsersRouterBoundary:
    """The fork's canonical handlers retain user contracts and error precedence."""

    async def _seed(self, db_session, **kwargs):
        from src.models.orm.users import User
        row = User(email=f"boundary-{uuid4().hex}@example.com", name="Boundary", **kwargs)
        db_session.add(row)
        await db_session.flush()
        return row

    async def test_list_sets_total_header(self, db_session):
        from src.routers.users import list_users
        row = await self._seed(db_session, is_active=True)
        response = Response()
        result = await list_users(_principal(), db_session, response,
            type=None, scope=None, include_inactive=False, search=row.email,
            sort_by=None, sort_direction="asc", limit=None, offset=0)
        assert [u.id for u in result] == [row.id]
        assert response.headers["X-Total-Count"] == "1"

    async def test_list_maps_422(self, db_session):
        from src.routers.users import list_users
        with pytest.raises(HTTPException) as exc_info:
            await list_users(_principal(), db_session, Response(),
                type=None, scope="bad", include_inactive=False, search=None,
                sort_by=None, sort_direction="asc", limit=None, offset=0)
        assert exc_info.value.status_code == 422

    async def test_create_delegates(self, db_session):
        from src.models import UserCreate
        from src.routers.users import create_user
        request = UserCreate(email=f"new-{uuid4().hex}@example.com", name="N")
        result = await create_user(request, _principal(), db_session)
        assert result.email == request.email
        assert result.name == "N"
        assert result.invite_status == "pending"
        assert result.registration_url is not None

    async def test_get_maps_404(self, db_session):
        from src.routers.users import get_user
        row = await self._seed(db_session)
        assert (await get_user(str(row.id), _principal(), db_session)).id == row.id
        with pytest.raises(HTTPException) as exc_info:
            await get_user(str(uuid4()), _principal(), db_session)
        assert exc_info.value.status_code == 404

    async def test_update_delegates_and_maps_403(self, db_session):
        from src.models import UserUpdate
        from src.routers.users import update_user
        row = await self._seed(db_session)
        result = await update_user(str(row.id), UserUpdate(name="new"), _principal(), db_session)
        assert result.name == "new"
        row.is_system = True
        await db_session.flush()
        with pytest.raises(HTTPException) as exc_info:
            await update_user(str(row.id), UserUpdate(name="blocked"), _principal(), db_session)
        assert exc_info.value.status_code == 403
        assert row.name == "new"

    async def test_delete_maps_400_404_403(self, db_session):
        from src.routers.users import delete_user
        actor = _principal()
        system = await self._seed(db_session, is_system=True)
        cases = [(str(actor.user_id), 400), (str(uuid4()), 404), (str(system.id), 403)]
        for target, code in cases:
            with pytest.raises(HTTPException) as exc_info:
                await delete_user(target, actor, db_session)
            assert exc_info.value.status_code == code
        row = await self._seed(db_session, is_active=False)
        assert await delete_user(str(row.id), actor, db_session) is None
