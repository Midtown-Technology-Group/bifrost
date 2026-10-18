"""MTG agent mutation security at the REST boundary used by canonical MCP tools."""

from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.contracts.agents import AgentUpdate
from src.models.enums import AgentAccessLevel
from src.models.orm.agents import Agent
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.routers.agents import update_agent


async def _org(db) -> Organization:
    """Persist a uniquely named organization for agent write-scope tests."""
    row = Organization(name=f"org-{uuid4().hex[:8]}", domain=f"{uuid4().hex}.example", created_by="test")
    db.add(row)
    await db.flush()
    return row

async def _user(db, org: Organization | None, *, superuser: bool = False) -> User:
    """Persist a verified caller with the chosen organization and admin grant."""
    row = User(
        email=f"{uuid4().hex}@example.com",
        name="Test User",
        is_superuser=superuser,
        is_verified=True,
        organization_id=org.id if org else None,
    )
    db.add(row)
    await db.flush()
    return row

async def _agent(
    db,
    *,
    organization_id,
    owner_user_id=None,
    access_level=AgentAccessLevel.ROLE_BASED,
) -> Agent:
    """Persist an agent with the chosen organization, owner, and access level."""
    row = Agent(
        name=f"agent-{uuid4().hex[:8]}",
        system_prompt="You are a test agent.",
        organization_id=organization_id,
        owner_user_id=owner_user_id,
        access_level=access_level,
        created_by="test",
    )
    db.add(row)
    await db.flush()
    return row


def _principal(user_id, org_id=None, *, admin=False):
    return UserPrincipal(user_id=user_id, email="test@example.com", organization_id=org_id, is_superuser=admin)


@pytest.mark.asyncio
async def test_non_owner_org_member_denied(db_session):
    org = await _org(db_session)
    owner = await _user(db_session, org)
    agent = await _agent(db_session, organization_id=org.id, owner_user_id=owner.id)
    with pytest.raises(HTTPException) as refused:
        await update_agent(agent.id, AgentUpdate(name="hijacked"), db_session, _principal(uuid4(), org.id))
    assert refused.value.status_code == 403
    await db_session.refresh(agent)
    assert agent.name != "hijacked"


@pytest.mark.asyncio
async def test_owner_of_private_agent_allowed(db_session):
    org = await _org(db_session)
    owner = await _user(db_session, org)
    agent = await _agent(db_session, organization_id=org.id, owner_user_id=owner.id, access_level=AgentAccessLevel.PRIVATE)
    await update_agent(agent.id, AgentUpdate(name="renamed-by-owner"), db_session, _principal(owner.id, org.id))
    await db_session.refresh(agent)
    assert agent.name == "renamed-by-owner"


@pytest.mark.asyncio
async def test_caller_with_no_org_denied_for_org_owned_agent(db_session):
    org = await _org(db_session)
    owner = await _user(db_session, org)
    agent = await _agent(db_session, organization_id=org.id, owner_user_id=owner.id, access_level=AgentAccessLevel.PRIVATE)
    with pytest.raises(HTTPException) as refused:
        await update_agent(agent.id, AgentUpdate(name="hijacked-no-org"), db_session, _principal(uuid4()))
    assert refused.value.status_code == 403
    await db_session.refresh(agent)
    assert agent.name != "hijacked-no-org"


@pytest.mark.asyncio
async def test_platform_admin_allowed_for_global_agent(db_session):
    agent = await _agent(db_session, organization_id=None)
    await update_agent(agent.id, AgentUpdate(name="admin-renamed"), db_session, _principal(uuid4(), admin=True))
    await db_session.refresh(agent)
    assert agent.name == "admin-renamed"


@pytest.mark.asyncio
async def test_non_admin_budget_update_denied_without_mutation(db_session):
    org = await _org(db_session)
    owner = await _user(db_session, org)
    agent = await _agent(db_session, organization_id=org.id, owner_user_id=owner.id, access_level=AgentAccessLevel.PRIVATE)
    original = agent.llm_max_tokens
    with pytest.raises(HTTPException) as refused:
        await update_agent(agent.id, AgentUpdate(llm_max_tokens=1000), db_session, _principal(owner.id, org.id))
    assert refused.value.status_code == 403
    assert "platform administrators" in refused.value.detail
    await db_session.refresh(agent)
    assert agent.llm_max_tokens == original
