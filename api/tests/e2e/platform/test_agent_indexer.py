"""Unit tests for AgentIndexer."""

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.ai_models import AIModelProfile, AIProviderConnection
from src.models.orm.agents import Agent, AgentTool
from src.models.orm.organizations import Organization
from src.models.orm.workflows import Workflow
from src.services.file_storage.indexers.agent import AgentIndexer


@pytest_asyncio.fixture(autouse=True)
async def cleanup_agents(db_session: AsyncSession):
    """Clean up test agents after each test."""
    yield
    await db_session.execute(
        delete(Agent).where(Agent.created_by == "file_sync")
    )
    await db_session.execute(
        delete(AIModelProfile).where(AIModelProfile.name.like("Indexer Profile %"))
    )
    await db_session.execute(
        delete(AIProviderConnection).where(AIProviderConnection.name.like("Indexer Provider %"))
    )
    await db_session.commit()


@pytest.mark.e2e
@pytest.mark.asyncio
class TestAgentIndexer:
    async def test_index_agent_resolves_llm_profile_name(self, db_session: AsyncSession):
        """AgentIndexer should resolve portable profile names to profile UUIDs."""
        connection = AIProviderConnection(
            name=f"Indexer Provider {uuid4().hex[:8]}",
            provider="openrouter",
            encrypted_api_key="encrypted-test-key",
        )
        db_session.add(connection)
        await db_session.flush()
        profile = AIModelProfile(
            name=f"Indexer Profile {uuid4().hex[:8]}",
            connection_id=connection.id,
            model="anthropic/claude-3-5-sonnet-20241022",
        )
        db_session.add(profile)
        await db_session.flush()

        agent_yaml = f"""name: Agent With Profile
system_prompt: Test
llm_profile: {profile.name}
"""
        indexer = AgentIndexer(db_session)
        await indexer.index_agent("agents/profile.agent.yaml", agent_yaml.encode())
        await db_session.flush()

        agent = (await db_session.execute(
            select(Agent).where(Agent.name == "Agent With Profile")
        )).scalar_one()
        assert agent.llm_profile_id == profile.id

    async def test_index_agent_with_tools_alias(self, db_session: AsyncSession):
        """AgentIndexer should accept 'tools' as alias for 'tool_ids'."""
        wf_id = uuid4()
        db_session.add(Workflow(
            id=wf_id, name="test_wf_tools_alias", path="workflows/test_tools_alias.py",
            function_name="test_wf", is_active=True, type="tool",
        ))
        await db_session.flush()

        agent_yaml = f"""name: Agent With Tools Alias
system_prompt: Test
tools:
- {wf_id}
"""
        indexer = AgentIndexer(db_session)
        await indexer.index_agent("agents/test.agent.yaml", agent_yaml.encode())
        await db_session.flush()

        agent = (await db_session.execute(
            select(Agent).where(Agent.name == "Agent With Tools Alias")
        )).scalar_one()
        tools = (await db_session.execute(
            select(AgentTool).where(AgentTool.agent_id == agent.id)
        )).scalars().all()
        assert len(tools) == 1
        assert tools[0].workflow_id == wf_id

    async def test_index_agent_with_tool_ids(self, db_session: AsyncSession):
        """AgentIndexer should still work with 'tool_ids' (canonical form)."""
        wf_id = uuid4()
        db_session.add(Workflow(
            id=wf_id, name="test_wf_tool_ids", path="workflows/test_tool_ids.py",
            function_name="test_wf", is_active=True, type="tool",
        ))
        await db_session.flush()

        agent_yaml = f"""name: Agent With Tool IDs
system_prompt: Test
tool_ids:
- {wf_id}
"""
        indexer = AgentIndexer(db_session)
        await indexer.index_agent("agents/test2.agent.yaml", agent_yaml.encode())
        await db_session.flush()

        agent = (await db_session.execute(
            select(Agent).where(Agent.name == "Agent With Tool IDs")
        )).scalar_one()
        tools = (await db_session.execute(
            select(AgentTool).where(AgentTool.agent_id == agent.id)
        )).scalars().all()
        assert len(tools) == 1
        assert tools[0].workflow_id == wf_id

    async def test_index_agent_rejects_cross_tenant_and_non_tool_references(
        self, db_session: AsyncSession
    ):
        """Repository UUIDs cannot create out-of-scope or invalid tool grants."""
        owner_org = Organization(
            name=f"Agent owner {uuid4().hex[:8]}",
            created_by="test",
        )
        foreign_org = Organization(
            name=f"Foreign owner {uuid4().hex[:8]}",
            created_by="test",
        )
        db_session.add_all([owner_org, foreign_org])
        await db_session.flush()

        suffix = uuid4().hex[:8]
        same_org_tool = Workflow(
            id=uuid4(),
            name=f"same_org_tool_{suffix}",
            path=f"workflows/same_org_tool_{suffix}.py",
            function_name="same_org_tool",
            type="tool",
            is_active=True,
            organization_id=owner_org.id,
        )
        global_tool = Workflow(
            id=uuid4(),
            name=f"global_tool_{suffix}",
            path=f"workflows/global_tool_{suffix}.py",
            function_name="global_tool",
            type="tool",
            is_active=True,
            organization_id=None,
        )
        foreign_tool = Workflow(
            id=uuid4(),
            name=f"foreign_tool_{suffix}",
            path=f"workflows/foreign_tool_{suffix}.py",
            function_name="foreign_tool",
            type="tool",
            is_active=True,
            organization_id=foreign_org.id,
        )
        inactive_tool = Workflow(
            id=uuid4(),
            name=f"inactive_tool_{suffix}",
            path=f"workflows/inactive_tool_{suffix}.py",
            function_name="inactive_tool",
            type="tool",
            is_active=False,
            organization_id=owner_org.id,
        )
        non_tool = Workflow(
            id=uuid4(),
            name=f"ordinary_workflow_{suffix}",
            path=f"workflows/ordinary_workflow_{suffix}.py",
            function_name="ordinary_workflow",
            type="workflow",
            is_active=True,
            organization_id=owner_org.id,
        )
        db_session.add_all(
            [same_org_tool, global_tool, foreign_tool, inactive_tool, non_tool]
        )
        await db_session.flush()

        agent_id = uuid4()
        db_session.add(
            Agent(
                id=agent_id,
                name="Scoped Agent",
                system_prompt="Original",
                organization_id=owner_org.id,
                created_by="file_sync",
            )
        )
        await db_session.flush()
        requested = [
            same_org_tool.id,
            global_tool.id,
            foreign_tool.id,
            inactive_tool.id,
            non_tool.id,
        ]
        agent_yaml = (
            f"id: {agent_id}\n"
            "name: Scoped Agent\n"
            "system_prompt: Test\n"
            "tool_ids:\n"
            + "".join(f"- {tool_id}\n" for tool_id in requested)
        )

        await AgentIndexer(db_session).index_agent(
            f"agents/{agent_id}.agent.yaml",
            agent_yaml.encode(),
            # A caller-supplied scope cannot override the persisted agent scope.
            organization_id=foreign_org.id,
        )
        await db_session.flush()

        assert (
            await db_session.scalar(
                select(Agent.organization_id).where(Agent.id == agent_id)
            )
        ) == owner_org.id

        attached_ids = set(
            (
                await db_session.execute(
                    select(AgentTool.workflow_id).where(
                        AgentTool.agent_id == agent_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert attached_ids == {same_org_tool.id, global_tool.id}
