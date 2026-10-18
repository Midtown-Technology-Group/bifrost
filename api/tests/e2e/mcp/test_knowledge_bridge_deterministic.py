"""Real MCP services and persisted knowledge, with only query embedding fixed.

This proves the resource-bound token verifier, current role lookup, agent
access, namespace allowlist, gateway dispatch and PostgreSQL retrieval. It
does not perform an OAuth/JSON-RPC network handshake or receipt replay; the
existing transport suites retain those responsibilities and their own gates.
"""

from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.core.principal import UserPrincipal
from src.models.enums import AgentAccessLevel
from src.models.orm.agents import Agent, AgentRole
from src.models.orm.knowledge import KnowledgeStore
from src.models.orm.knowledge_sources import KnowledgeNamespaceRole
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, User, UserRole, UserRoleBoundary
from src.services import embeddings
from src.services.authorization.enforce import load_caller
from src.services.knowledge_namespace_access import accessible_knowledge_namespaces
from src.services.mcp_server.auth import BifrostAuthProvider
from src.services.mcp_server.config_service import invalidate_mcp_config_cache
from src.services.mcp_server.gateway import GatewayError, MCPAgentGatewayService
from src.services.mcp_server.server import MCPContext
from src.services.mcp_server.tool_access import MCPToolAccessService
from src.services.mcp_server.tool_result import tool_result_wire_payload
from src.services.mcp_server.tools.knowledge import (
    SEARCH_KNOWLEDGE_NAMESPACE_DENIED_CODE,
    search_knowledge,
)
from tests.e2e.api.test_mcp_knowledge_scoping import (
    _result_namespaces,
    _structured_tool_result,
)
from tests.e2e.conftest import E2E_API_URL
from tests.fixtures.auth import create_test_jwt

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_mcp_namespace_and_content_scope_survive_junction_edits_and_revocation(
    async_session_factory, monkeypatch,
):
    tag = uuid4().hex[:8]
    home_id, foreign_id, role_id, user_id = (uuid4() for _ in range(4))
    home_agent, global_agent, foreign_agent = (uuid4() for _ in range(3))
    alpha, beta, gamma = (f"mcp-bridge-{label}-{tag}" for label in ("alpha", "beta", "gamma"))
    role_name, email = f"MCP sharing {tag}", f"mcp-bridge-{tag}@example.test"
    doc_ids = {label: uuid4() for label in ("alpha-home", "alpha-global", "alpha-foreign",
                                          "beta-home", "beta-global", "gamma-foreign")}
    embedding_calls = []

    class FixedQueryEmbedder:
        async def embed_single(self, query):
            embedding_calls.append(query)
            return [1.0, 0.0, 0.0]

    async def fixed_embedding_client(_db):
        return FixedQueryEmbedder()

    # This is the only substituted boundary. Auth, role/boundary evaluation,
    # agent repositories, the MCP tool and pgvector/full-text retrieval run.
    monkeypatch.setattr(embeddings, "get_embedding_client", fixed_embedding_client)
    provider = BifrostAuthProvider(base_url=E2E_API_URL)
    token = create_test_jwt(user_id=str(user_id), email=email,
                            organization_id=str(home_id), mcp_resource=provider.resource)
    principal = UserPrincipal(user_id=user_id, email=email, organization_id=home_id)

    async def verified_context():
        verified = await provider.verify_token(token)
        assert verified is not None
        claims = verified.claims
        return MCPContext(user_id=claims["user_id"], org_id=claims["org_id"],
                          is_platform_admin=claims["is_superuser"],
                          is_provider_org=claims["is_provider_org"],
                          is_external=claims["is_external"], user_email=claims["email"]), claims["roles"]

    async def scoped_result(agent_id, *, namespace=None):
        context, roles = await verified_context()
        async with async_session_factory() as db:
            scoped = await MCPToolAccessService(db).get_tools_for_agent(
                agent_id=agent_id, user_roles=roles, is_superuser=False,
                user_id=user_id, org_id=home_id,
            )
            if scoped is None:
                return None
            assert "search_knowledge" in {tool.id for tool in scoped.tools}
            context.session = db
            context.accessible_namespaces = scoped.accessible_namespaces
            result = await search_knowledge(context, query="synthetic bridge knowledge",
                                             namespace=namespace, include_full_content=True)
            return {"result": tool_result_wire_payload(result)}

    async def gateway_result(agent_id, *, namespace=None):
        context, _ = await verified_context()
        gateway = MCPAgentGatewayService(context)
        capabilities = await gateway.search_capabilities(agent_id=str(agent_id), query="search knowledge")
        selected = next(tool for tool in capabilities["agents"][0]["matching_tools"]
                        if tool["name"] == "search_knowledge")
        snapshot = await gateway.get_agent_snapshot(str(agent_id))
        tool = gateway.find_tool(snapshot, selected["tool_ref"])
        arguments = {"query": "synthetic bridge knowledge", "include_full_content": True}
        if namespace is not None:
            arguments["namespace"] = namespace
        # Exercise the same selected-agent dispatch used by the unscoped
        # gateway without starting durable work or testing receipt replay.
        response = await gateway.execute_tool(snapshot, tool, arguments,
                                               operation_id=f"knowledge-{uuid4()}", lineage=None)
        return {"result": {"structuredContent": response["result"]}}

    async def selector():
        async with async_session_factory() as db:
            return await accessible_knowledge_namespaces(db, await load_caller(db, principal))

    def assert_documents(payload, expected_labels, namespace):
        assert _result_namespaces(payload) == {namespace}
        results = _structured_tool_result(payload)["results"]
        assert {entry["id"] for entry in results} == {str(doc_ids[label]) for label in expected_labels}
        assert {entry["content"] for entry in results} == {f"synthetic {label} knowledge" for label in expected_labels}

    invalidate_mcp_config_cache()
    try:
        async with async_session_factory() as db:
            db.add_all([Organization(id=home_id, name=f"MCP home {tag}", created_by="test"),
                        Organization(id=foreign_id, name=f"MCP foreign {tag}", created_by="test"),
                        Role(id=role_id, name=role_name, created_by="test")])
            await db.flush()
            db.add(User(id=user_id, email=email, name=f"MCP person {tag}", organization_id=home_id))
            await db.flush()
            db.add(UserRole(user_id=user_id, role_id=role_id, assigned_by="test"))
            await db.flush()
            db.add(UserRoleBoundary(user_id=user_id, role_id=role_id,
                                    kind="organization", organization_id=home_id))
            db.add(KnowledgeNamespaceRole(namespace=alpha, organization_id=home_id, role_id=role_id))
            for agent_id, namespace, org, access in [
                (home_agent, alpha, home_id, AgentAccessLevel.ROLE_BASED),
                (global_agent, beta, None, AgentAccessLevel.AUTHENTICATED),
                (foreign_agent, gamma, foreign_id, AgentAccessLevel.AUTHENTICATED),
            ]:
                db.add(Agent(id=agent_id, name=f"MCP {namespace}", organization_id=org,
                             system_prompt="Synthetic MCP knowledge fixture.", access_level=access,
                             knowledge_sources=[namespace], system_tools=[], created_by="test"))
            await db.flush()
            db.add(AgentRole(agent_id=home_agent, role_id=role_id, assigned_by="test"))
            for label, namespace, org in [
                ("alpha-home", alpha, home_id), ("alpha-global", alpha, None),
                ("alpha-foreign", alpha, foreign_id), ("beta-home", beta, home_id),
                ("beta-global", beta, None), ("gamma-foreign", gamma, foreign_id),
            ]:
                db.add(KnowledgeStore(id=doc_ids[label], namespace=namespace, organization_id=org,
                                      key=label, content=f"synthetic {label} knowledge", doc_metadata={},
                                      embedding=[1.0, 0.0, 0.0], chunk_index=0, chunk_count=1))
            await db.commit()

        assert await selector() == [alpha]
        for get_result in (scoped_result, gateway_result):
            assert_documents(await get_result(home_agent), {"alpha-home", "alpha-global"}, alpha)
            assert_documents(await get_result(global_agent), {"beta-home", "beta-global"}, beta)
        assert await scoped_result(foreign_agent) is None
        with pytest.raises(GatewayError) as forbidden:
            await gateway_result(foreign_agent)
        assert forbidden.value.code == "AGENT_NOT_FOUND_OR_FORBIDDEN"

        before_denial = len(embedding_calls)
        denied = await scoped_result(home_agent, namespace=beta)
        assert _structured_tool_result(denied)["code"] == SEARCH_KNOWLEDGE_NAMESPACE_DENIED_CODE
        with pytest.raises(GatewayError) as denied_gateway:
            await gateway_result(home_agent, namespace=gamma)
        assert denied_gateway.value.details["underlying_result"]["code"] == SEARCH_KNOWLEDGE_NAMESPACE_DENIED_CODE
        assert len(embedding_calls) == before_denial

        # Revoking only namespace selection does not become a content ACL.
        async with async_session_factory() as db:
            await db.execute(delete(KnowledgeNamespaceRole).where(KnowledgeNamespaceRole.role_id == role_id))
            await db.commit()
        assert await selector() == []
        for get_result in (scoped_result, gateway_result):
            assert_documents(await get_result(home_agent), {"alpha-home", "alpha-global"}, alpha)

        async with async_session_factory() as db:
            db.add(KnowledgeNamespaceRole(namespace=alpha, organization_id=home_id, role_id=role_id))
            await db.commit()
        assert await selector() == [alpha]
        async with async_session_factory() as db:
            await db.execute(delete(UserRole).where(UserRole.user_id == user_id))
            await db.commit()
        assert await selector() == []
        # Reverify the exact same signed token: roles are fetched now, and
        # both MCP selection paths refuse the revoked role-gated agent.
        _, roles = await verified_context()
        assert role_name not in roles
        assert await scoped_result(home_agent) is None
        with pytest.raises(GatewayError) as revoked:
            await gateway_result(home_agent)
        assert revoked.value.code == "AGENT_NOT_FOUND_OR_FORBIDDEN"
        assert_documents(await gateway_result(global_agent), {"beta-home", "beta-global"}, beta)
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(Agent).where(Agent.id.in_([home_agent, global_agent, foreign_agent])))
            await db.execute(delete(KnowledgeStore).where(KnowledgeStore.id.in_(doc_ids.values())))
            await db.execute(delete(KnowledgeNamespaceRole).where(KnowledgeNamespaceRole.role_id == role_id))
            await db.execute(delete(User).where(User.id == user_id))
            await db.execute(delete(Role).where(Role.id == role_id))
            await db.execute(delete(Organization).where(Organization.id.in_([home_id, foreign_id])))
            await db.commit()
        invalidate_mcp_config_cache()
