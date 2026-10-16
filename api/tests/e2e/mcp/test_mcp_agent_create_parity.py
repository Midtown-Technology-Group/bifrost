"""MCP agent creation must satisfy the fork's canonical REST access contract."""

from uuid import uuid4

import pytest

from src.services.mcp_server.server import MCPContext
from src.services.mcp_server.tools.agents import create_agent


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_regular_mcp_caller_creates_an_owned_private_agent(
    e2e_client, org1_user, bob_user, platform_admin, monkeypatch
):
    """Keep regular-user MCP agent creation aligned with REST's private ownership contract."""
    monkeypatch.setenv("BIFROST_MCP_HTTP_BRIDGE_URL", str(e2e_client.base_url))
    context = MCPContext(
        user_id=org1_user.user_id,
        org_id=org1_user.organization_id,
        user_email=org1_user.email,
    )
    result = await create_agent(
        context, name=f"mcp-private-{uuid4().hex}", system_prompt="Private agent parity test"
    )
    assert result.structured_content and result.structured_content.get("success"), result
    agent_id = result.structured_content["id"]
    try:
        own = e2e_client.get(f"/api/agents/{agent_id}", headers=org1_user.headers)
        assert own.status_code == 200, own.text
        assert own.json()["access_level"] == "private"
        assert own.json()["owner_user_id"] == str(org1_user.user_id)
        other = e2e_client.get(f"/api/agents/{agent_id}", headers=bob_user.headers)
        assert other.status_code == 404, other.text
    finally:
        deleted = e2e_client.delete(f"/api/agents/{agent_id}", headers=platform_admin.headers)
        assert deleted.status_code in (200, 204), deleted.text
