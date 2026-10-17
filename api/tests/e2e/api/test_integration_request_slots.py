"""Exercise atomic admission through the real authenticated API and Redis."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest


@pytest.mark.e2e
def test_one_principal_cannot_monopolize_shared_admission(e2e_client, platform_admin, org1):
    headers = platform_admin.headers
    name = f"slots-{uuid4().hex}"
    created = e2e_client.post("/api/integrations", headers=headers, json={"name": name})
    assert created.status_code == 201, created.text
    integration_id = created.json()["id"]
    bodies = [{"name": name, "scope": "global" if i % 2 else str(org1["id"]), "token": str(uuid4())} for i in range(8)]
    try:
        configured = e2e_client.put(
            f"/api/integrations/{integration_id}/config", headers=headers,
            json={"config": {"request_concurrency_limit": 2}},
        )
        assert configured.status_code == 200, configured.text
        mapping = e2e_client.post(
            f"/api/integrations/{integration_id}/mappings", headers=headers,
            json={"organization_id": str(org1["id"]), "entity_id": "example", "config": {"request_concurrency_limit": 100}},
        )
        assert mapping.status_code == 201, mapping.text

        def acquire(body):
            response = e2e_client.post("/api/sdk/integrations/request-slot/acquire", headers=headers, json=body)
            assert response.status_code == 200, response.text
            return response.json()

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(acquire, bodies))
        winners = [body for body, result in zip(bodies, results) if result["acquired"]]
        assert len(winners) == 1
        assert all(result["enabled"] for result in results)
        # Retrying the winning token is idempotent and does not add a lease.
        assert acquire(winners[0])["acquired"]
        stranger = {"name": name, "scope": "global", "token": str(uuid4())}
        released = e2e_client.post("/api/sdk/integrations/request-slot/release", headers=headers, json=stranger)
        assert released.status_code == 200
        assert not acquire(stranger)["acquired"]
        released = e2e_client.post("/api/sdk/integrations/request-slot/release", headers=headers, json=winners[0])
        assert released.status_code == 200
        assert acquire(stranger)["acquired"]
        bodies.append(stranger)
        invalid = e2e_client.put(
            f"/api/integrations/{integration_id}/config", headers=headers,
            json={"config": {"request_concurrency_limit": 0}},
        )
        assert invalid.status_code == 422
    finally:
        for body in bodies:
            e2e_client.post("/api/sdk/integrations/request-slot/release", headers=headers, json=body)
        e2e_client.delete(f"/api/integrations/{integration_id}", headers=headers)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_expired_token_cannot_release_new_owner():
    from src.core.cache import get_redis
    from src.services import integration_request_slots as slots

    integration_id = uuid4()
    key = f"bifrost:integration-request-slots:{integration_id}"
    try:
        acquired, _ = await slots.acquire(integration_id, "expired-owner:token", "expired-owner", 1)
        assert acquired
        async with get_redis() as redis:
            # Expire the precise token without wall-clock sleeps.
            await redis.zadd(key, {"expired-owner:token": 1})
        acquired, _ = await slots.acquire(integration_id, "new-owner:token", "new-owner", 1)
        assert acquired
        await slots.release(integration_id, "expired-owner:token")
        acquired, _ = await slots.acquire(integration_id, "third-owner:token", "third-owner", 1)
        assert not acquired
    finally:
        async with get_redis() as redis:
            await redis.delete(key)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_admission_polling_reuses_api_redis_pool(monkeypatch):
    from unittest.mock import Mock
    from src.core.cache import redis_client
    from src.services import integration_request_slots as slots

    await redis_client.close_shared_redis()
    factory = Mock(wraps=redis_client.redis.from_url)
    monkeypatch.setattr(redis_client.redis, "from_url", factory)
    integration_id = uuid4()
    try:
        assert (await slots.acquire(integration_id, "owner:token", "owner", 1))[0]
        assert not (await slots.acquire(integration_id, "contender:token", "contender", 1))[0]
        await slots.release(integration_id, "owner:token")
        assert (await slots.acquire(integration_id, "contender:token", "contender", 1))[0]
        await slots.release(integration_id, "contender:token")
        # Repeated API polls must not create/close a TLS connection per request.
        factory.assert_called_once()
    finally:
        await redis_client.close_shared_redis()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_principal_cap_preserves_global_limit_for_distinct_callers():
    from src.core.cache import get_redis
    from src.services import integration_request_slots as slots

    integration_id = uuid4()
    key = f"bifrost:integration-request-slots:{integration_id}"
    try:
        assert (await slots.acquire(integration_id, "principal-a:one", "principal-a", 2))[0]
        assert not (await slots.acquire(integration_id, "principal-a:two", "principal-a", 2))[0]
        assert (await slots.acquire(integration_id, "principal-b:one", "principal-b", 2))[0]
        assert not (await slots.acquire(integration_id, "principal-c:one", "principal-c", 2))[0]
        assert (await slots.acquire(integration_id, "principal-a:one", "principal-a", 2))[0]
    finally:
        async with get_redis() as redis:
            await redis.delete(key)
