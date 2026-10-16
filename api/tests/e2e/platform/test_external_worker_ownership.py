"""Process-generation fencing, with real PostgreSQL commits and fake providers."""
import base64
import secrets
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from fastapi import FastAPI
from httpx import AsyncClient as ASGIClient
import pytest
from sqlalchemy import delete

from src.config import Settings
from src.core.security import decrypt_secret
from src.models.orm.config import SystemConfig
from src.services.external_worker_ownership import acquire, grant, host_key, mutate
from src.services.external_worker_scaling import CATEGORY, enroll_replica, row

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.fixture
async def owner_fixture(async_session_factory):
    replica = "test-worker--" + uuid4().hex
    azure = SimpleNamespace(settings=Settings(
        external_worker_app_resource_id="/test/test-worker",
        external_worker_defined_network_id="network-TEST",
        external_worker_defined_role_id="role-TEST"), replicas=AsyncMock(return_value={replica}))
    calls = []
    code = secrets.token_urlsafe(32)
    host = {}

    def transport(request):
        import json
        calls.append((request.method, request.url.path))
        if request.url.path == "/v2/host-and-enrollment-code":
            host.update(id="host-TEST", name=json.loads(request.content)["name"],
                        networkID="network-TEST", roleID="role-TEST")
            return httpx.Response(200, json={"data": {"host": host, "enrollmentCode": {"code": code}}})
        if request.method == "GET" and request.url.path == "/v2/hosts/host-TEST":
            return httpx.Response(200, json={"data": host})
        if request.url.path == "/v1/hosts/host-TEST/enrollment-code":
            return httpx.Response(200, json={"data": {"code": code}})
        raise AssertionError("Unexpected provider request")

    original_client = httpx.AsyncClient
    def client(**kwargs):
        return original_client(transport=httpx.MockTransport(transport), **kwargs)

    try:
        with patch("src.services.external_worker_scaling.httpx.AsyncClient", side_effect=client), patch(
            "src.services.external_worker_scaling.defined_token", new=AsyncMock(return_value=secrets.token_urlsafe(32))
        ):
            yield azure, replica, calls, code
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(SystemConfig).where(SystemConfig.category == CATEGORY,
                                                        SystemConfig.key == host_key(replica)))
            await db.commit()


async def test_clean_completion_reuse_and_old_token_rejection(async_session_factory, owner_fixture):
    azure, replica, calls, code = owner_fixture
    attempt = secrets.token_hex(32)
    async with async_session_factory() as db:
        reply = await acquire(db, azure, replica, attempt)
        token = reply["token"]
        assert reply["leaseMilliseconds"] == 60000
        state = await row(db, host_key(replica))
        assert token not in str(state.value_json)
        with pytest.raises(ValueError):
            await mutate(db, azure, replica, token, "release")
        await db.rollback()
        await mutate(db, azure, replica, token, "begin")
        assert await grant(db, azure, replica, token) == {"code": code, "hostId": "host-TEST"}
        with pytest.raises(ValueError):
            await grant(db, azure, replica, token)
        await db.rollback()
        with pytest.raises(ValueError):
            await mutate(db, azure, replica, token, "bind", host_id="host-OTHER", network_id="network-TEST")
        await db.rollback()
        await mutate(db, azure, replica, token, "bind", host_id="host-TEST", network_id="network-TEST")
        checkpoint = secrets.token_bytes(64)
        await mutate(db, azure, replica, token, "checkpoint", checkpoint=checkpoint)
        state = await row(db, host_key(replica))
        persisted = state.value_json["vojeto_owner"]["checkpoint"]
        assert base64.b64decode(decrypt_secret(persisted)) == checkpoint
        assert base64.b64encode(checkpoint).decode() not in str(state.value_json)
        await mutate(db, azure, replica, token, "renew")
        await mutate(db, azure, replica, token, "release")
    # A new API process/session sees the durable release; the original attempt
    # and token cannot reacquire or extend the next generation.
    async with async_session_factory() as db:
        with pytest.raises(ValueError):
            await acquire(db, azure, replica, attempt)
        await db.rollback()
        with pytest.raises(ValueError, match="rate bound"):
            await acquire(db, azure, replica, secrets.token_hex(32))
        await db.rollback()
        state = await row(db, host_key(replica))
        value = dict(state.value_json)
        value["created"] -= 61
        state.value_json = value
        await db.commit()
        next_token = (await acquire(db, azure, replica, secrets.token_hex(32)))["token"]
        assert next_token != token
        with pytest.raises(ValueError):
            await mutate(db, azure, replica, token, "renew")
        await db.rollback()
        await mutate(db, azure, replica, next_token, "begin")
        await grant(db, azure, replica, next_token)
        assert calls.count(("POST", "/v2/host-and-enrollment-code")) == 1
        assert calls.count(("POST", "/v1/hosts/host-TEST/enrollment-code")) == 1


async def test_expired_or_paused_owner_cannot_be_replaced_or_use_legacy_enrollment(async_session_factory, owner_fixture):
    azure, replica, calls, _ = owner_fixture
    async with async_session_factory() as db:
        token = (await acquire(db, azure, replica, secrets.token_hex(32)))["token"]
        state = await row(db, host_key(replica))
        value = dict(state.value_json)
        owner = value["vojeto_owner"]
        assert isinstance(owner, dict)
        value["vojeto_owner"] = {**owner, "expires": 0}
        state.value_json = value
        await db.commit()
    async with async_session_factory() as db:
        for operation in ("begin", "renew", "release"):
            with pytest.raises(ValueError):
                await mutate(db, azure, replica, token, operation)
            await db.rollback()
        with pytest.raises(ValueError):
            await acquire(db, azure, replica, secrets.token_hex(32))
        await db.rollback()
        with pytest.raises(ValueError):
            await enroll_replica(db, azure, replica, uuid4())
        assert not calls


async def test_lost_grant_response_is_not_replayed_after_restart(async_session_factory, owner_fixture):
    azure, replica, calls, _ = owner_fixture
    async with async_session_factory() as db:
        token = (await acquire(db, azure, replica, secrets.token_hex(32)))["token"]
        await mutate(db, azure, replica, token, "begin")
        with patch("src.services.external_worker_ownership.enroll_replica", new=AsyncMock(side_effect=httpx.ReadTimeout("synthetic"))):
            with pytest.raises(httpx.ReadTimeout):
                await grant(db, azure, replica, token)
        await db.rollback()
    async with async_session_factory() as db:
        with pytest.raises(ValueError):
            await grant(db, azure, replica, token)
        await db.rollback()
        with pytest.raises(ValueError):
            await acquire(db, azure, replica, secrets.token_hex(32))
        assert not calls


async def test_legacy_allocation_is_not_ownership_proof(async_session_factory, owner_fixture):
    azure, replica, _, _ = owner_fixture
    async with async_session_factory() as db:
        state = await row(db, host_key(replica))
        state.value_json = {"replica": replica, "host_id": "host-TEST", "intent": "legacy"}
        await db.commit()
        with pytest.raises(ValueError):
            await acquire(db, azure, replica, secrets.token_hex(32))


async def test_concurrent_acquisition_has_one_durable_winner(async_session_factory, owner_fixture):
    import asyncio

    azure, replica, _, _ = owner_fixture
    async def contender():
        async with async_session_factory() as db:
            return await acquire(db, azure, replica, secrets.token_hex(32))
    results = await asyncio.gather(contender(), contender(), return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, (ValueError, RuntimeError)) for result in results) == 1


async def test_failed_checkpoint_cannot_authorize_release(async_session_factory, owner_fixture):
    azure, replica, _, _ = owner_fixture
    async with async_session_factory() as db:
        token = (await acquire(db, azure, replica, secrets.token_hex(32)))["token"]
        await mutate(db, azure, replica, token, "begin")
        await grant(db, azure, replica, token)
        await mutate(db, azure, replica, token, "bind", host_id="host-TEST", network_id="network-TEST")
        with patch("src.services.external_worker_ownership.encrypt_secret", side_effect=RuntimeError("synthetic")):
            with pytest.raises(RuntimeError):
                await mutate(db, azure, replica, token, "checkpoint", checkpoint=b"synthetic-state")
        await db.rollback()
    async with async_session_factory() as db:
        with pytest.raises(ValueError):
            await mutate(db, azure, replica, token, "release")
        await db.rollback()
        with pytest.raises(ValueError):
            await acquire(db, azure, replica, secrets.token_hex(32))


async def test_late_provider_response_does_not_revive_owner(async_session_factory, owner_fixture):
    from src.services.external_worker_scaling import enroll_replica as real_enroll

    azure, replica, _, _ = owner_fixture
    async def finish_after_expiry(db, azure, replica, boot, *, owner_token):
        result = await real_enroll(db, azure, replica, boot, owner_token=owner_token)
        state = await row(db, host_key(replica))
        value = dict(state.value_json)
        owner = value["vojeto_owner"]
        assert isinstance(owner, dict)
        value["vojeto_owner"] = {**owner, "expires": 0}
        state.value_json = value
        await db.commit()
        return result
    async with async_session_factory() as db:
        token = (await acquire(db, azure, replica, secrets.token_hex(32)))["token"]
        await mutate(db, azure, replica, token, "begin")
        with patch("src.services.external_worker_ownership.enroll_replica", side_effect=finish_after_expiry):
            with pytest.raises(ValueError):
                await grant(db, azure, replica, token)
        await db.rollback()
    async with async_session_factory() as db:
        with pytest.raises(ValueError):
            await mutate(db, azure, replica, token, "renew")
        await db.rollback()
        with pytest.raises(ValueError):
            await acquire(db, azure, replica, secrets.token_hex(32))


@pytest.mark.parametrize("phase,removed", [("acquired", True), ("begun", True), ("grant-started", False)])
async def test_unused_allocation_cleanup_requires_verified_absence(async_session_factory, owner_fixture, phase, removed):
    from datetime import UTC, datetime
    from sqlalchemy import select
    from src.services.external_worker_scaling import reconcile_hosts

    azure, replica, calls, _ = owner_fixture
    async with async_session_factory() as db:
        await acquire(db, azure, replica, secrets.token_hex(32))
        state = await row(db, host_key(replica))
        value = dict(state.value_json)
        owner = value["vojeto_owner"]
        assert isinstance(owner, dict)
        value["vojeto_owner"] = {**owner, "phase": phase}
        state.value_json = value
        await db.commit()
        await reconcile_hosts(db, azure, set())
        await db.commit()
        assert await db.scalar(select(SystemConfig.id).where(SystemConfig.key == host_key(replica))) is not None
        state = await row(db, host_key(replica))
        value = dict(state.value_json)
        value.update(absent_since=datetime.now(UTC).timestamp() - 121,
                     absence_observed_at=datetime.now(UTC).timestamp())
        state.value_json = value
        await db.commit()
        await reconcile_hosts(db, azure, set())
        await db.commit()
        exists = await db.scalar(select(SystemConfig.id).where(SystemConfig.key == host_key(replica))) is not None
        assert exists is not removed
        assert not calls


async def test_identity_http_protocol_commits_real_ownership(async_session_factory, owner_fixture):
    """Actual DTO/router/services/DB, with only cloud identity/provider calls faked."""
    from src.core.database import get_db
    from src.routers.platform.external_workers import router

    azure, replica, calls, code = owner_fixture
    azure.settings.external_worker_scaling_enabled = True
    azure.settings.external_worker_identity_enabled = True
    azure.close = AsyncMock()
    app = FastAPI()
    app.include_router(router)

    async def database():
        async with async_session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    # Preserve the original HTTP client class: the provider fixture replaces
    # httpx.AsyncClient to direct Defined calls into its synthetic transport.
    async with ASGIClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        async def operation(name, **fields):
            return await client.post("/api/platform/external-workers/identity",
                                     headers={"Authorization": "Bearer synthetic"},
                                     json={"operation": name, "replica": replica, **fields})

        with patch("src.routers.platform.external_workers.get_settings", return_value=azure.settings), patch(
            "src.routers.platform.external_workers.validate_settings"
        ), patch("src.routers.platform.external_workers.verify_identity", new=AsyncMock()), patch(
            "src.routers.platform.external_workers.Azure", return_value=azure
        ):
            acquired = await operation("acquire", attempt=secrets.token_hex(32))
            assert acquired.status_code == 200
            assert acquired.headers["cache-control"] == "no-store"
            token = acquired.json()["token"]
            assert acquired.json()["leaseMilliseconds"] == 60000
            assert (await operation("release", token=token)).status_code == 403
            assert (await operation("begin", token=token)).status_code == 200
            granted = await operation("grant", token=token)
            assert granted.status_code == 200
            assert granted.json() == {"ok": True, "token": token, "code": code,
                                      "hostID": "host-TEST", "networkID": "network-TEST"}
            assert (await operation("grant", token=token)).status_code == 403
            assert (await operation("bind", token=token, hostID="host-OTHER", networkID="network-TEST")).status_code == 403
            assert (await operation("bind", token=token, hostID="host-TEST", networkID="network-TEST")).status_code == 200
            checkpoint = secrets.token_bytes(64)
            assert (await operation("checkpoint", token=token,
                                    state=base64.b64encode(checkpoint).decode())).status_code == 200
            async with async_session_factory() as db:
                state = await row(db, host_key(replica))
                owner = state.value_json["vojeto_owner"]
                assert base64.b64decode(decrypt_secret(owner["checkpoint"])) == checkpoint
                assert token not in str(state.value_json)
                assert base64.b64encode(checkpoint).decode() not in str(state.value_json)
            renewed = await operation("renew", token=token)
            assert renewed.status_code == 200 and renewed.json()["leaseMilliseconds"] == 60000
            assert (await operation("release", token=token)).status_code == 200
            assert (await operation("renew", token=token)).status_code == 403
            async with async_session_factory() as db:
                state = await row(db, host_key(replica))
                assert state.value_json["vojeto_owner"]["phase"] == "released"
            assert calls.count(("POST", "/v2/host-and-enrollment-code")) == 1
            assert azure.close.await_count == 11
