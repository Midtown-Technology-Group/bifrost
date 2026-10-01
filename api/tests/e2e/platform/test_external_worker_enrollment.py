"""Enrollment restart, ambiguity and cleanup using real durable state."""
import hashlib
import secrets
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select

from src.config import Settings
from src.models.orm.config import SystemConfig
from src.services.external_worker_scaling import CATEGORY, enroll_replica, reconcile_hosts, row

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_enrollment_survives_lost_response_and_restart_without_duplicate_host(async_session_factory):
    replica = "test-worker--" + uuid4().hex
    key = "host-" + hashlib.sha256(replica.encode()).hexdigest()
    configuration = Settings(external_worker_app_resource_id="/test/test-worker",
                             external_worker_defined_network_id="network-TEST",
                             external_worker_defined_role_id="role-TEST")
    azure = SimpleNamespace(settings=configuration, replicas=AsyncMock(return_value={replica}))
    hosts = []
    created, reenrolled, deleted = [], [], []
    code = secrets.token_urlsafe(32)
    fail_first = True
    fail_reenroll = False
    def transport(request):
        nonlocal fail_first, fail_reenroll
        assert request.url.host == "api.defined.net"
        if request.method == "POST" and request.url.path == "/v2/host-and-enrollment-code":
            import json
            value = json.loads(request.content)
            host = {"id": "host-TEST", "name": value["name"], "networkID": "network-TEST", "roleID": "role-TEST"}
            hosts.append(host)
            created.append(host)
            if fail_first:
                fail_first = False
                raise httpx.ReadTimeout("ambiguous response", request=request)
            return httpx.Response(200, json={"data": {"host": host, "enrollmentCode": {"code": code}}})
        if request.method == "GET" and request.url.path == "/v2/hosts":
            return httpx.Response(200, json={"data": hosts, "metadata": {"hasNextPage": False}})
        if request.method == "POST" and request.url.path.endswith("/enrollment-code"):
            reenrolled.append(request.url.path)
            if fail_reenroll:
                fail_reenroll = False
                raise httpx.ReadTimeout("ambiguous re-enrollment", request=request)
            return httpx.Response(200, json={"data": {"code": code}})
        if request.method == "DELETE":
            deleted.append(request.url.path)
            hosts.clear()
            return httpx.Response(200)
        if request.method == "GET" and request.url.path == "/v2/hosts/host-TEST":
            return httpx.Response(200, json={"data": hosts[0]}) if hosts else httpx.Response(404)
        raise AssertionError("Unexpected provider request")
    original_client = httpx.AsyncClient
    def client(**kwargs):
        return original_client(transport=httpx.MockTransport(transport), **kwargs)
    try:
        with patch("src.services.external_worker_scaling.httpx.AsyncClient", side_effect=client), patch(
            "src.services.external_worker_scaling.defined_token", new=AsyncMock(return_value=secrets.token_urlsafe(32))
        ):
            boot = uuid4()
            async with async_session_factory() as db:
                with pytest.raises(httpx.ReadTimeout):
                    await enroll_replica(db, azure, replica, boot)
                await db.rollback()
            async with async_session_factory() as db:
                state = await row(db, key)
                assert state.value_json["intent"] == hosts[0]["name"]
                value = dict(state.value_json)
                value["created"] -= 61
                state.value_json = value
                await db.commit()
            async with async_session_factory() as db:
                result = await enroll_replica(db, azure, replica, boot)
                assert result == {"code": code, "hostId": "host-TEST"}
                assert len(created) == 1 and len(reenrolled) == 1
            async with async_session_factory() as db:
                assert await enroll_replica(db, azure, replica, boot) == result
                assert len(reenrolled) == 1
                state = await row(db, key)
                assert state.value_json["code"] != code
                value = dict(state.value_json)
                value["created"] -= 61
                state.value_json = value
                await db.commit()
            async with async_session_factory() as db:
                # A restarted sidecar re-enrolls the same host, not a new peer.
                assert await enroll_replica(db, azure, replica, uuid4()) == result
                assert len(created) == 1 and len(reenrolled) == 2
                state = await row(db, key)
                value = dict(state.value_json)
                value["created"] -= 61
                state.value_json = value
                await db.commit()
                failed_boot = uuid4()
                fail_reenroll = True
                with pytest.raises(httpx.ReadTimeout):
                    await enroll_replica(db, azure, replica, failed_boot)
                await db.rollback()
                state = await row(db, key)
                assert "code" not in state.value_json
                with pytest.raises(RuntimeError, match="rate bound"):
                    await enroll_replica(db, azure, replica, failed_boot)
                await db.rollback()
                await reconcile_hosts(db, azure, {replica})
                await db.commit()
                assert not deleted
            async with async_session_factory() as db:
                await reconcile_hosts(db, azure, set())
                await db.commit()
                assert not deleted
                state = await row(db, key)
                value = dict(state.value_json)
                value["absent_since"] = datetime.now(UTC).timestamp() - 121
                state.value_json = value
                await db.commit()
            async with async_session_factory() as db:
                await reconcile_hosts(db, azure, set())
                await db.commit()
                assert deleted == ["/v1/hosts/host-TEST"]
                assert await db.scalar(select(SystemConfig.id).where(SystemConfig.category == CATEGORY, SystemConfig.key == key)) is None
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(SystemConfig).where(SystemConfig.category == CATEGORY, SystemConfig.key == key))
            await db.commit()
