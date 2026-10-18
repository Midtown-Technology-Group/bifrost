"""The promotion route must preserve ownership and exact bridge authority."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.contracts.agents import AgentAccessLevel, AgentPromoteRequest
from src.routers import agents
from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
from src.services.authorization.enforce import Caller


def _case(*, permission="agents.promote", boundary="organization", admin=False):
    user_id, home, role_id = uuid4(), uuid4(), uuid4()
    principal = UserPrincipal(user_id=user_id, email="promoter@example.test", organization_id=home)
    ctx = AuthorizationContext(
        user_id=user_id, home_organization_id=home, base_role_id=uuid4(),
        is_external=False, base_permissions=frozenset(),
        role_grants=(RoleGrant(
            role_id=role_id, permissions=frozenset({"*" if admin else permission}),
            boundaries=(Boundary(BoundaryKind.PLATFORM if admin else BoundaryKind(boundary),
                                 None if admin or boundary != "organization" else home),),
        ),),
    )
    agent = SimpleNamespace(id=uuid4(), owner_user_id=user_id, organization_id=home,
                            access_level=AgentAccessLevel.PRIVATE, solution_id=None)
    return principal, Caller(principal, ctx), agent


async def _promote(monkeypatch, principal, caller, agent):
    lookup, reload = MagicMock(), MagicMock()
    lookup.scalar_one_or_none.return_value = agent
    reload.scalar_one.return_value = agent
    db = AsyncMock()
    db.execute.side_effect = [lookup, reload]
    monkeypatch.setattr(agents, "load_caller", AsyncMock(return_value=caller))
    monkeypatch.setattr(agents, "_agent_to_public", lambda row: row)
    response = await agents.promote_agent(
        agent.id, AgentPromoteRequest(access_level=AgentAccessLevel.AUTHENTICATED), db, principal
    )
    return response


@pytest.mark.asyncio
async def test_owner_home_and_explicit_grant_promote(monkeypatch):
    principal, caller, agent = _case()
    result = await _promote(monkeypatch, principal, caller, agent)
    assert result.access_level == AgentAccessLevel.AUTHENTICATED
    assert result.owner_user_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("permission", ["agents.readwrite", "agents.readwrite.all", "devices.execute"])
async def test_other_permissions_do_not_authorize_promotion(monkeypatch, permission):
    principal, caller, agent = _case(permission=permission)
    with pytest.raises(HTTPException) as exc:
        await _promote(monkeypatch, principal, caller, agent)
    assert exc.value.status_code == 403
    assert agent.access_level == AgentAccessLevel.PRIVATE


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["platform", "managed_organizations"])
async def test_non_home_boundaries_do_not_authorize_promotion(monkeypatch, boundary):
    principal, caller, agent = _case(boundary=boundary)
    with pytest.raises(HTTPException) as exc:
        await _promote(monkeypatch, principal, caller, agent)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner", "org", "solution", "null_home"])
async def test_owner_org_and_solution_guards_survive_bridge(monkeypatch, change):
    principal, caller, agent = _case()
    if change == "owner":
        agent.owner_user_id = uuid4()
    elif change == "org":
        agent.organization_id = uuid4()
    elif change == "solution":
        agent.solution_id = uuid4()
    else:
        from dataclasses import replace
        caller = replace(caller, ctx=replace(caller.ctx, home_organization_id=None))
    with pytest.raises(HTTPException) as exc:
        await _promote(monkeypatch, principal, caller, agent)
    assert exc.value.status_code == (409 if change == "solution" else 403)
    assert agent.access_level == AgentAccessLevel.PRIVATE


@pytest.mark.asyncio
async def test_admin_wildcard_bypasses_owner_and_home_but_never_solution(monkeypatch):
    principal, caller, agent = _case(admin=True)
    agent.owner_user_id, agent.organization_id = uuid4(), uuid4()
    result = await _promote(monkeypatch, principal, caller, agent)
    assert result.access_level == AgentAccessLevel.AUTHENTICATED
    agent.access_level, agent.solution_id = AgentAccessLevel.PRIVATE, uuid4()
    with pytest.raises(HTTPException) as exc:
        await _promote(monkeypatch, principal, caller, agent)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_stale_admin_token_does_not_bypass_current_rows(monkeypatch):
    principal, caller, agent = _case(permission="agents.readwrite")
    principal.is_superuser = True
    with pytest.raises(HTTPException) as exc:
        await _promote(monkeypatch, principal, caller, agent)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_real_promotion_serializes_loaded_mcp_connections(db_session):
    """The real serializer must not issue an async lazy read after promotion."""
    from src.models.orm.agents import Agent
    from src.models.orm.organizations import Organization
    from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary

    org = Organization(name=f"Promotion serialization {uuid4()}", created_by="test")
    role = Role(name=f"Promotion serializer {uuid4()}", created_by="test")
    db_session.add_all([org, role])
    await db_session.flush()
    person = User(email=f"promotion-serializer-{uuid4()}@example.com", organization_id=org.id)
    db_session.add(person)
    await db_session.flush()
    db_session.add_all([
        RolePermission(role_id=role.id, permission="agents.promote"),
        UserRole(user_id=person.id, role_id=role.id, assigned_by="test"),
    ])
    await db_session.flush()
    db_session.add(UserRoleBoundary(user_id=person.id, role_id=role.id,
                                   kind="organization", organization_id=org.id))
    agent = Agent(name=f"Private serialization {uuid4()}", system_prompt="Synthetic fixture.",
                  access_level=AgentAccessLevel.PRIVATE, organization_id=org.id,
                  owner_user_id=person.id, created_by="test")
    db_session.add(agent)
    await db_session.flush()
    response = await agents.promote_agent(
        agent.id, AgentPromoteRequest(access_level=AgentAccessLevel.AUTHENTICATED),
        db_session, UserPrincipal(user_id=person.id, email=person.email, organization_id=org.id),
    )
    assert response.id == agent.id
    assert response.access_level == AgentAccessLevel.AUTHENTICATED
    assert response.owner_user_id is None
    assert response.mcp_connection_ids == []
