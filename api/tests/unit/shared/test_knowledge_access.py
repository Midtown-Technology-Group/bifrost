from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from shared.knowledge_access import accessible_knowledge_namespaces
from src.core.principal import UserPrincipal


def _user(
    *, is_superuser: bool = False, roles: list[str] | None = None
) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="reader@example.test",
        organization_id=uuid4(),
        is_superuser=is_superuser,
        roles=roles or [],
    )


@pytest.mark.asyncio
async def test_platform_admin_has_unrestricted_namespace_access():
    session = AsyncMock()

    assert (
        await accessible_knowledge_namespaces(session, _user(is_superuser=True)) is None
    )
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_platform_admin_role_has_unrestricted_namespace_access():
    session = AsyncMock()

    assert (
        await accessible_knowledge_namespaces(session, _user(roles=["Platform Admin"]))
        is None
    )
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_regular_user_namespaces_come_from_scoped_role_assignments():
    result = MagicMock()
    result.scalars.return_value.all.return_value = ["runbooks", "policies"]
    session = AsyncMock()
    session.execute.return_value = result
    user = _user()

    assert await accessible_knowledge_namespaces(session, user) == {
        "runbooks",
        "policies",
    }

    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "knowledge_namespace_roles" in sql
    assert "user_roles" in sql
    assert str(user.user_id) in sql
    assert str(user.organization_id) in sql
    assert "organization_id IS NULL" in sql
