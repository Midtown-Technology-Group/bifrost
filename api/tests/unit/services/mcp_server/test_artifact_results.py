from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from mcp.types import ImageContent, ResourceLink

from src.services.mcp_server import server
from src.services.artifacts import ArtifactAccessError


@pytest.mark.asyncio
async def test_workflow_artifact_results_become_mcp_media_and_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid4()
    context = server.MCPContext(user_id=uuid4(), org_id=org_id)
    monkeypatch.setattr(server, "_get_context_from_token", lambda: context)
    monkeypatch.setattr(
        server,
        "_execute_workflow_tool_impl",
        AsyncMock(
            return_value={
                "artifacts": [
                    {
                        "type": "bifrost_artifact",
                        "id": "00000000-0000-0000-0000-000000000001",
                        "filename": "chart.png",
                        "content_type": "image/png",
                        "size_bytes": 8,
                    },
                    {
                        "type": "bifrost_artifact",
                        "id": "00000000-0000-0000-0000-000000000002",
                        "filename": "brief.pdf",
                        "content_type": "application/pdf",
                        "size_bytes": 12,
                    },
                ]
            }
        ),
    )

    @asynccontextmanager
    async def fake_db_context():
        yield object()

    class FakeArtifactService:
        def __init__(self, db) -> None:
            pass

        async def get_authorized(self, artifact_id, **kwargs):
            return type(
                "StoredArtifact",
                (),
                {"id": artifact_id, "s3_key": f"_artifacts/{artifact_id}"},
            )()

        async def read(self, artifact):
            return b"png-data"

        async def generate_download_url(self, artifact):
            return "https://files.example.test/brief.pdf"

    monkeypatch.setattr("src.core.database.get_db_context", fake_db_context)
    monkeypatch.setattr(
        "src.services.artifacts.ArtifactService",
        FakeArtifactService,
    )
    tool = server.WorkflowTool(
        name="create_brief",
        description="Create files",
        workflow_id=str(uuid4()),
        workflow_name="Create Brief",
        parameters={"type": "object", "properties": {}},
    )
    result = await tool.run({})

    assert result.structured_content is not None
    assert result.structured_content["artifacts"][0]["filename"] == "chart.png"
    assert any(isinstance(block, ImageContent) for block in result.content)
    resource = next(
        block for block in result.content if isinstance(block, ResourceLink)
    )
    assert resource.name == "brief.pdf"
    assert str(resource.uri) == "https://files.example.test/brief.pdf"


@pytest.mark.asyncio
@pytest.mark.parametrize("is_platform_admin", [False, True])
async def test_provider_workflow_cannot_bypass_artifact_ownership_without_admin(
    monkeypatch: pytest.MonkeyPatch, is_platform_admin: bool
) -> None:
    context = server.MCPContext(
        user_id=uuid4(), org_id=None, is_platform_admin=is_platform_admin,
        is_provider_org=True,
    )
    monkeypatch.setattr(server, "_get_context_from_token", lambda: context)
    monkeypatch.setattr(
        server,
        "_execute_workflow_tool_impl",
        AsyncMock(return_value={
            "type": "bifrost_artifact",
            "id": str(uuid4()),
            "filename": "other-users-chart.png",
            "content_type": "image/png",
            "size_bytes": 8,
        }),
    )

    @asynccontextmanager
    async def fake_db_context():
        yield object()

    read_artifact = AsyncMock(return_value=b"png-data")

    class FakeArtifactService:
        def __init__(self, db) -> None:
            pass

        async def get_authorized(self, artifact_id, *, user_id, bypass):
            assert user_id == context.user_id
            if not bypass:
                raise ArtifactAccessError("Artifact belongs to another user")
            return object()

        read = read_artifact

    monkeypatch.setattr("src.core.database.get_db_context", fake_db_context)
    monkeypatch.setattr("src.services.artifacts.ArtifactService", FakeArtifactService)
    tool = server.WorkflowTool(
        name="foreign_artifact",
        description="Return another user's artifact",
        workflow_id=str(uuid4()),
        workflow_name="Foreign Artifact",
        parameters={"type": "object", "properties": {}},
    )
    result = await tool.run({})

    if is_platform_admin:
        assert any(isinstance(block, ImageContent) for block in result.content)
        read_artifact.assert_awaited_once()
    else:
        assert not any(isinstance(block, ImageContent) for block in result.content)
        assert "outside this MCP scope" in result.content[0].text
        read_artifact.assert_not_awaited()
