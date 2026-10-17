"""Authorization proof runs without provider credentials or auth overrides."""

from unittest.mock import AsyncMock, create_autospec
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, select
from src.main import app
from src.models.orm.knowledge import KnowledgeStore
from src.services.embeddings.base import BaseEmbeddingClient


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_authenticated_knowledge_search_isolates_orgs_without_provider_key(
    async_session_factory,
    org1_user,
    org2_user,
    monkeypatch,
):
    namespace = f"author-auth-{uuid4().hex}"
    first_id, second_id = uuid4(), uuid4()
    embedder = create_autospec(BaseEmbeddingClient, instance=True, spec_set=True)

    async def embed(query):
        assert query == "author sentinel", (
            "unauthorized request reached embedding provider"
        )
        return [1.0, 0.0, 0.0]

    embedder.embed_single.side_effect = embed
    factory = AsyncMock(return_value=embedder)
    monkeypatch.setattr("src.services.embeddings.get_embedding_client", factory)
    try:
        async with async_session_factory() as db:
            for identity, user, content in [
                (first_id, org1_user, "first private sentinel"),
                (second_id, org2_user, "second private sentinel"),
            ]:
                db.add(
                    KnowledgeStore(
                        id=identity,
                        namespace=namespace,
                        key="sentinel",
                        organization_id=UUID(str(user.organization_id)),
                        content=content,
                        embedding=[1.0, 0.0, 0.0],
                        doc_metadata={},
                    )
                )
            await db.commit()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            path = "/api/sdk/knowledge/search"
            forbidden = {
                "query": "forbidden query",
                "namespace": [namespace],
                "scope": str(org2_user.organization_id),
            }
            assert (await client.post(path, json=forbidden)).status_code == 401
            denied = await client.post(path, headers=org1_user.headers, json=forbidden)
            assert denied.status_code == 403, denied.text
            factory.assert_not_awaited()
            for user, identity in [(org1_user, first_id), (org2_user, second_id)]:
                response = await client.post(
                    path,
                    headers=user.headers,
                    json={
                        "query": "author sentinel",
                        "namespace": [namespace],
                        "fallback": False,
                    },
                )
                assert response.status_code == 200, response.text
                assert {row["id"] for row in response.json()} == {str(identity)}, (
                    response.text
                )
                assert {row["organization_id"] for row in response.json()} == {
                    str(user.organization_id)
                }
        assert embedder.embed_single.await_count == 2
        async with async_session_factory() as db:
            rows = (
                await db.execute(
                    select(KnowledgeStore.id, KnowledgeStore.content).where(
                        KnowledgeStore.id.in_([first_id, second_id])
                    )
                )
            ).all()
            assert dict(rows) == {
                first_id: "first private sentinel",
                second_id: "second private sentinel",
            }
    finally:
        async with async_session_factory() as cleanup:
            await cleanup.execute(
                delete(KnowledgeStore).where(
                    KnowledgeStore.id.in_([first_id, second_id])
                )
            )
            await cleanup.commit()
