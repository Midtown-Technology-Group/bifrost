"""Unit tests for AutonomousAgentExecutor MCP-tool dispatch.

Phase 3 contract for autonomous runs:

- ``run()`` reads ``_caller["user_id"]`` (if present) and stores it on
  the executor so MCP dispatch can pick it up.
- ``_execute_tool`` routes ``mcp__<connection_id>__<tool>`` names to
  ``_execute_mcp_tool`` BEFORE the workflow-tool fallback.
- Verified external actors mapped to a Bifrost user get user-token resolution.
- Fully autonomous runs (no ``_caller`` or no ``user_id``) get
  ``caller_user_id=None`` so dispatch routes to the service token.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from tests.unit.services.agent_runtime_fakes import LegacyMockModel
from src.services.execution.agent_helpers import MCP_TOOL_PREFIX
from src.services.events.external_actors import ExternalActorResolutionError, actor_record
from src.services.execution.autonomous_agent_executor import (
    AutonomousAgentExecutor,
    ToolError,
)
from src.services.llm.base import LLMConfig, LLMResponse, ToolCallRequest
from src.services.mcp_client.errors import (
    MisconfigError,
    NeedsReauthError,
    ToolDispatchError,
)
from src.services.webhooks.protocol import AuthenticatedExternalActor


@pytest.fixture
def mock_session_factory():
    """Mock async session factory yielding a session whose ``execute()``
    returns a fake MCPConnection."""
    fake_connection = MagicMock()
    fake_connection.id = uuid4()

    fake_result = MagicMock()
    fake_result.scalar_one_or_none = MagicMock(return_value=fake_connection)

    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock(return_value=fake_result)
    session.get = AsyncMock(return_value=None)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=ctx)
    factory._fake_connection = fake_connection
    factory._fake_session = session
    return factory


@pytest.fixture(autouse=True)
def mock_runtime_config():
    with (
        patch(
            "src.services.execution.autonomous_agent_executor.get_llm_config",
            new_callable=AsyncMock,
            return_value=LLMConfig(provider="openai", model="test-model", api_key="test-key"),
        ),
        patch(
            "src.services.execution.autonomous_agent_executor.agent_mcp_granted",
            new=AsyncMock(return_value=True),
        ),
    ):
        yield


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.id = uuid4()
    agent.name = "Test Agent"
    agent.system_prompt = "You are a test agent."
    agent.tools = []
    agent.system_tools = []
    agent.knowledge_sources = []
    agent.delegated_agents = []
    agent.max_iterations = 5
    agent.max_token_budget = 50000
    agent.max_run_timeout = 60
    agent.llm_profile_id = None
    agent.llm_max_tokens = None
    agent.is_active = True
    agent.organization_id = uuid4()
    return agent


@pytest.mark.asyncio
async def test_execute_mcp_tool_uses_threaded_caller(mock_session_factory):
    """When ``_caller_user_id`` is set on the executor (by ``run()``),
    ``_execute_mcp_tool`` forwards it to dispatch."""
    executor = AutonomousAgentExecutor(mock_session_factory)
    caller_user_id = uuid4()
    executor._caller_user_id = caller_user_id

    connection_id = uuid4()
    fake_envelope = {
        "content": [{"type": "text", "text": "ok"}],
        "structured_content": {"a": 1},
        "is_error": False,
    }

    with patch(
        "src.services.execution.autonomous_agent_executor.mcp_dispatch.invoke",
        new=AsyncMock(return_value=fake_envelope),
    ) as mock_invoke:
        result = await executor._execute_mcp_tool(
            ToolCallRequest(
                id="tc-1",
                name=f"{MCP_TOOL_PREFIX}{connection_id}__t",
                arguments={"q": "hi"},
            ),
            agent_id=uuid4(),
            connection_id=connection_id,
            remote_tool_name="t",
        )

    assert mock_invoke.await_count == 1
    assert mock_invoke.await_args is not None
    assert mock_invoke.await_args.kwargs["caller_user_id"] == caller_user_id
    assert mock_invoke.await_args.kwargs["tool_name"] == "t"
    # The autonomous path returns a JSON-string envelope (the loop
    # serializes tool results into the conversation as text).
    assert isinstance(result, str)
    assert "structured_content" in result


@pytest.mark.asyncio
async def test_execute_mcp_tool_with_no_caller(mock_session_factory):
    """A fully autonomous run has ``_caller_user_id=None``; dispatch
    sees ``None`` and routes to service-token resolution."""
    executor = AutonomousAgentExecutor(mock_session_factory)
    # Intentionally not setting _caller_user_id — should default to None
    assert executor._caller_user_id is None

    connection_id = uuid4()
    fake_envelope = {"content": [], "is_error": False}

    with patch(
        "src.services.execution.autonomous_agent_executor.mcp_dispatch.invoke",
        new=AsyncMock(return_value=fake_envelope),
    ) as mock_invoke:
        await executor._execute_mcp_tool(
            ToolCallRequest(
                id="tc-2",
                name=f"{MCP_TOOL_PREFIX}{connection_id}__t",
                arguments={},
            ),
            agent_id=uuid4(),
            connection_id=connection_id,
            remote_tool_name="t",
        )

    assert mock_invoke.await_args is not None
    assert mock_invoke.await_args.kwargs["caller_user_id"] is None


@pytest.mark.asyncio
async def test_execute_mcp_tool_raises_tool_error_on_needs_reauth(
    mock_session_factory,
):
    """An autonomous run can't prompt a user to reconnect, so
    ``NeedsReauthError`` becomes ``ToolError`` — the loop records it
    in the run's step log and proceeds (or terminates if the LLM gives
    up)."""
    executor = AutonomousAgentExecutor(mock_session_factory)
    connection_id = uuid4()

    with patch(
        "src.services.execution.autonomous_agent_executor.mcp_dispatch.invoke",
        new=AsyncMock(
            side_effect=NeedsReauthError(
                reauth_url=f"/me/connections/{connection_id}/connect",
                connection_id=connection_id,
            )
        ),
    ):
        with pytest.raises(ToolError) as excinfo:
            await executor._execute_mcp_tool(
                ToolCallRequest(
                    id="tc-3",
                    name=f"{MCP_TOOL_PREFIX}{connection_id}__t",
                    arguments={},
                ),
                agent_id=uuid4(),
                connection_id=connection_id,
                remote_tool_name="t",
            )

    assert "needs reauth" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_execute_mcp_tool_raises_tool_error_on_misconfig(
    mock_session_factory,
):
    executor = AutonomousAgentExecutor(mock_session_factory)
    connection_id = uuid4()

    with patch(
        "src.services.execution.autonomous_agent_executor.mcp_dispatch.invoke",
        new=AsyncMock(
            side_effect=MisconfigError(
                connection_id=connection_id,
                reason="autonomous flag off",
            )
        ),
    ):
        with pytest.raises(ToolError) as excinfo:
            await executor._execute_mcp_tool(
                ToolCallRequest(
                    id="tc-4",
                    name=f"{MCP_TOOL_PREFIX}{connection_id}__t",
                    arguments={},
                ),
                agent_id=uuid4(),
                connection_id=connection_id,
                remote_tool_name="t",
            )

    assert "misconfigured" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_execute_mcp_tool_raises_tool_error_on_dispatch_error(
    mock_session_factory,
):
    executor = AutonomousAgentExecutor(mock_session_factory)
    connection_id = uuid4()

    with patch(
        "src.services.execution.autonomous_agent_executor.mcp_dispatch.invoke",
        new=AsyncMock(
            side_effect=ToolDispatchError(
                "remote returned 500",
                connection_id=connection_id,
                tool_name="t",
            )
        ),
    ):
        with pytest.raises(ToolError):
            await executor._execute_mcp_tool(
                ToolCallRequest(
                    id="tc-5",
                    name=f"{MCP_TOOL_PREFIX}{connection_id}__t",
                    arguments={},
                ),
                agent_id=uuid4(),
                connection_id=connection_id,
                remote_tool_name="t",
            )


@pytest.mark.asyncio
async def test_ungranted_mcp_connection_never_dispatches(mock_session_factory):
    executor = AutonomousAgentExecutor(mock_session_factory)
    connection_id = uuid4()
    with (
        patch("src.services.execution.autonomous_agent_executor.agent_mcp_granted", new=AsyncMock(return_value=False)),
        patch("src.services.execution.autonomous_agent_executor.mcp_dispatch.invoke", new=AsyncMock()) as invoke,
    ):
        with pytest.raises(ToolError, match="not granted"):
            await executor._execute_mcp_tool(
                ToolCallRequest(id="ungranted", name=f"{MCP_TOOL_PREFIX}{connection_id}__t", arguments={}),
                agent_id=uuid4(), connection_id=connection_id, remote_tool_name="t",
            )
    invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_tool_routes_mcp_prefix(mock_session_factory):
    """The tool dispatcher in _execute_tool routes ``mcp__<uuid>__<tool>``
    to _execute_mcp_tool BEFORE workflow-id lookup."""
    executor = AutonomousAgentExecutor(mock_session_factory)
    executor._caller_user_id = uuid4()

    connection_id = uuid4()
    fake_envelope = {"content": [], "is_error": False}

    agent = MagicMock()
    agent.id = uuid4()
    agent.system_tools = []
    agent.knowledge_sources = []
    agent.organization_id = uuid4()
    tool_name = f"{MCP_TOOL_PREFIX}{connection_id}__t"
    executor._tool_workflow_id_map[tool_name] = connection_id

    with patch(
        "src.services.execution.autonomous_agent_executor.mcp_dispatch.invoke",
        new=AsyncMock(return_value=fake_envelope),
    ) as mock_invoke:
        await executor._execute_tool(
            ToolCallRequest(
                id="tc-6",
                name=tool_name,
                arguments={},
            ),
            agent,
        )

    assert mock_invoke.await_count == 1
    assert mock_invoke.await_args is not None
    assert (
        mock_invoke.await_args.kwargs["caller_user_id"]
        == executor._caller_user_id
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["search_knowledge", "read_status"])
async def test_external_grant_is_rechecked_before_non_workflow_tool(
    mock_session_factory, mock_agent, tool_name,
):
    executor = AutonomousAgentExecutor(mock_session_factory)
    executor._external_actor = ({}, uuid4(), mock_agent.organization_id, mock_agent.id)
    mock_agent.knowledge_sources = [uuid4()]
    mock_agent.system_tools = ["read_status"]
    with (
        patch.object(
            executor, "_external_caller_access", new=AsyncMock(
                side_effect=ExternalActorResolutionError("External actor grant changed during run")
            ),
        ),
        patch.object(executor, "_execute_knowledge_search", new=AsyncMock()) as knowledge,
        patch.object(executor, "_execute_system_tool", new=AsyncMock()) as system,
    ):
        with pytest.raises(ExternalActorResolutionError, match="grant changed"):
            await executor._execute_tool(
                ToolCallRequest(id="revoked", name=tool_name, arguments={}), mock_agent,
            )
    knowledge.assert_not_awaited()
    system.assert_not_awaited()


# ---------------------------------------------------------------------------
# run() — _caller threading
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch(
    "src.services.execution.autonomous_agent_executor.create_agent_model"
)
@patch(
    "src.services.execution.autonomous_agent_executor.resolve_agent_tools"
)
async def test_run_threads_user_id_from_caller(
    mock_resolve_tools, mock_get_llm, mock_session_factory, mock_agent
):
    """``run(_caller={'user_id': '...'})`` parses to UUID and stores on
    ``self._caller_user_id`` so dispatch can pick it up."""
    mock_resolve_tools.return_value = ([], {})
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(
        return_value=LLMResponse(
            content="done",
            tool_calls=None,
            finish_reason="end_turn",
            input_tokens=10,
            output_tokens=5,
        )
    )
    mock_llm.provider_name = "openai"
    mock_get_llm.return_value = LegacyMockModel(mock_llm)

    user_id = uuid4()
    executor = AutonomousAgentExecutor(mock_session_factory)
    await executor.run(
        agent=mock_agent,
        input_data={"task": "x"},
        run_id=str(uuid4()),
        _caller={"user_id": str(user_id), "email": "a@b"},
    )

    # The executor stored the parsed UUID; resolve_agent_tools saw it
    assert executor._caller_user_id == user_id
    assert mock_resolve_tools.await_count == 1
    assert mock_resolve_tools.await_args is not None
    assert (
        mock_resolve_tools.await_args.kwargs["caller_user_id"] == user_id
    )


@pytest.mark.asyncio
async def test_verified_provider_actor_uses_customer_scope_for_tools(
    mock_session_factory, mock_agent,
):
    user_id, customer_id, identity_id = uuid4(), uuid4(), uuid4()
    principal = SimpleNamespace(
        user_id=user_id, organization_id=customer_id,
        email="jane@mtg.example", name="Jane", is_superuser=False,
        is_external=False, is_provider_org=False, roles=["Tier 2"],
    )
    record = actor_record(AuthenticatedExternalActor(
        provider="microsoft_teams", external_scope_id="tenant-A",
        external_user_id="jane-object-id", integration_id=uuid4(),
    ))
    mock_agent.organization_id = customer_id
    mock_session_factory._fake_session.get = AsyncMock(
        return_value=SimpleNamespace(is_superuser=True)
    )
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(return_value=LLMResponse(
        content="done", tool_calls=None, finish_reason="end_turn",
        input_tokens=1, output_tokens=1,
    ))
    mock_llm.provider_name = "openai"
    executor = AutonomousAgentExecutor(mock_session_factory)
    with (
        patch("src.services.execution.autonomous_agent_executor.resolve_run_external_actor", new=AsyncMock(return_value=(principal, identity_id, record))),
        patch("src.services.execution.autonomous_agent_executor.resolve_external_actor", new=AsyncMock(return_value=(principal, identity_id))),
        patch("src.services.agent_run_access.load_agent_for_user", new=AsyncMock(return_value=mock_agent)),
        patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools", new=AsyncMock(return_value=([], {}))) as tools,
        patch("src.services.execution.autonomous_agent_executor.create_agent_model", return_value=LegacyMockModel(mock_llm)),
    ):
        await executor.run(
            agent=mock_agent, input_data={"task": "PC123"}, run_id=str(uuid4()),
            _caller={"user_id": str(user_id), "organization_id": str(customer_id)},
        )
    assert tools.await_args.kwargs["caller_access"] == (user_id, customer_id, False)
    assert executor._caller_is_platform_admin is False
    assert not executor._caller["is_provider_org"]
    assert executor._caller["organization_id"] == str(customer_id)
    connection_id = uuid4()
    executor._caller_is_platform_admin = True
    with (
        patch("src.services.execution.autonomous_agent_executor.resolve_external_actor", new=AsyncMock(return_value=(principal, identity_id))),
        patch("src.services.agent_run_access.load_agent_for_user", new=AsyncMock(return_value=mock_agent)),
        patch("src.services.execution.autonomous_agent_executor.mcp_dispatch.invoke", new=AsyncMock(return_value={"content": []})) as invoke,
    ):
        await executor._execute_mcp_tool(
            ToolCallRequest(id="mcp", name=f"{MCP_TOOL_PREFIX}{connection_id}__read", arguments={}),
            agent_id=mock_agent.id, connection_id=connection_id,
            remote_tool_name="read",
        )
    assert invoke.await_args.kwargs["caller_user_id"] == user_id
    assert executor._caller_is_platform_admin is False
    with (
        patch("src.services.execution.autonomous_agent_executor.resolve_external_actor", new=AsyncMock(return_value=(principal, identity_id))),
        patch("src.services.agent_run_access.load_agent_for_user", new=AsyncMock(return_value=None)),
        patch("src.services.execution.autonomous_agent_executor.mcp_dispatch.invoke", new=AsyncMock()) as blocked_invoke,
    ):
        with pytest.raises(ExternalActorResolutionError, match="grant changed"):
            await executor._execute_mcp_tool(
                ToolCallRequest(id="blocked", name=f"{MCP_TOOL_PREFIX}{connection_id}__read", arguments={}),
                agent_id=mock_agent.id, connection_id=connection_id,
                remote_tool_name="read",
            )
    blocked_invoke.assert_not_awaited()


@pytest.mark.asyncio
@patch(
    "src.services.execution.autonomous_agent_executor.create_agent_model"
)
@patch(
    "src.services.execution.autonomous_agent_executor.resolve_agent_tools"
)
async def test_run_treats_missing_caller_as_autonomous(
    mock_resolve_tools, mock_get_llm, mock_session_factory, mock_agent
):
    """``run(_caller=None)`` → ``self._caller_user_id`` is None and
    resolve_agent_tools sees None (autonomous-only filtering applies)."""
    mock_resolve_tools.return_value = ([], {})
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(
        return_value=LLMResponse(
            content="done",
            tool_calls=None,
            finish_reason="end_turn",
            input_tokens=1,
            output_tokens=1,
        )
    )
    mock_llm.provider_name = "openai"
    mock_get_llm.return_value = LegacyMockModel(mock_llm)

    executor = AutonomousAgentExecutor(mock_session_factory)
    await executor.run(
        agent=mock_agent,
        input_data={"task": "x"},
        run_id=str(uuid4()),
        _caller=None,
    )

    assert executor._caller_user_id is None
    assert mock_resolve_tools.await_args is not None
    assert mock_resolve_tools.await_args.kwargs["caller_user_id"] is None


@pytest.mark.asyncio
@patch(
    "src.services.execution.autonomous_agent_executor.create_agent_model"
)
@patch(
    "src.services.execution.autonomous_agent_executor.resolve_agent_tools"
)
async def test_run_treats_caller_without_user_id_as_autonomous(
    mock_resolve_tools, mock_get_llm, mock_session_factory, mock_agent
):
    """``_caller={'email': '...'}`` (no user_id key) is autonomous —
    e.g. an unauthenticated webhook trigger that still records its
    source in the audit log."""
    mock_resolve_tools.return_value = ([], {})
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(
        return_value=LLMResponse(
            content="done",
            tool_calls=None,
            finish_reason="end_turn",
            input_tokens=1,
            output_tokens=1,
        )
    )
    mock_llm.provider_name = "openai"
    mock_get_llm.return_value = LegacyMockModel(mock_llm)

    executor = AutonomousAgentExecutor(mock_session_factory)
    await executor.run(
        agent=mock_agent,
        input_data={"task": "x"},
        run_id=str(uuid4()),
        _caller={"email": "webhook@source.example", "name": "webhook"},
    )

    assert executor._caller_user_id is None
    assert mock_resolve_tools.await_args is not None
    assert mock_resolve_tools.await_args.kwargs["caller_user_id"] is None


@pytest.mark.asyncio
@patch(
    "src.services.execution.autonomous_agent_executor.create_agent_model"
)
@patch(
    "src.services.execution.autonomous_agent_executor.resolve_agent_tools"
)
async def test_run_invalid_user_id_falls_back_to_autonomous(
    mock_resolve_tools, mock_get_llm, mock_session_factory, mock_agent
):
    """A malformed user_id (non-UUID string) is logged as a warning and
    treated as autonomous rather than raising — the run should not crash
    on a malformed audit-log entry."""
    mock_resolve_tools.return_value = ([], {})
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(
        return_value=LLMResponse(
            content="done",
            tool_calls=None,
            finish_reason="end_turn",
            input_tokens=1,
            output_tokens=1,
        )
    )
    mock_llm.provider_name = "openai"
    mock_get_llm.return_value = LegacyMockModel(mock_llm)

    executor = AutonomousAgentExecutor(mock_session_factory)
    result = await executor.run(
        agent=mock_agent,
        input_data={"task": "x"},
        run_id=str(uuid4()),
        _caller={"user_id": "not-a-uuid"},
    )

    assert result["status"] == "completed"
    assert executor._caller_user_id is None
    assert mock_resolve_tools.await_args is not None
    assert mock_resolve_tools.await_args.kwargs["caller_user_id"] is None
