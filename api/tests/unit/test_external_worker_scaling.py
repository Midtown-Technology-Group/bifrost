"""Scale-in and identity boundaries for external worker infrastructure."""
import secrets
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from src.config import Settings
from src.services.external_worker_scaling import Azure, capacity_decision, verify_identity


def settings():
    from uuid import uuid4
    return Settings(external_worker_tenant_id=str(uuid4()),
                    external_worker_client_id=str(uuid4()),
                    external_worker_principal_id=str(uuid4()),
                    external_worker_queue_account="scalingtestaccount",
                    external_worker_queue_name="bifrost-worker-demand-test",
                    external_worker_max_replicas=2)


@pytest.mark.parametrize("pending,active", [(1, 0), (0, 1), (5, 9)])
def test_domain_work_protects_full_capacity_even_after_delivery_completion(pending, active):
    assert capacity_decision(pending=pending, active=active, now=1000,
                            idle_since=1, maximum=2, idle_seconds=120) == (2, None)


def test_zero_requires_continuous_idle_and_clock_regression_is_safe():
    def decide(now, idle):
        return capacity_decision(pending=0, active=0, now=now,
                                 idle_since=idle, maximum=2, idle_seconds=120)
    assert decide(100, None) == (2, 100)
    assert decide(219, 100) == (2, 100)
    assert decide(220, 100) == (0, 100)
    assert decide(90, 100) == (2, 90)
    with pytest.raises(ValueError):
        capacity_decision(pending=-1, active=0, now=100, idle_since=None, maximum=2, idle_seconds=120)


@pytest.mark.asyncio
async def test_publisher_never_hides_or_deletes_markers_while_active():
    azure = object.__new__(Azure)
    azure.settings = settings()
    response = httpx.Response(200, content=b"<QueueMessagesList><QueueMessage><MessageText>bifrost-worker-demand-v1</MessageText></QueueMessage></QueueMessagesList>")
    azure.request = AsyncMock(return_value=response)
    await azure.publish(2)
    calls = azure.request.call_args_list
    assert [call.args[0] for call in calls] == ["GET", "POST"]
    assert calls[0].kwargs["params"]["peekonly"] == "true"
    assert calls[1].kwargs["params"]["messagettl"] == -1
    azure.request.reset_mock()
    azure.request.return_value = httpx.Response(200, content=b"<QueueMessagesList><QueueMessage><MessageText>foreign</MessageText></QueueMessage></QueueMessagesList>")
    with pytest.raises(RuntimeError, match="foreign"):
        await azure.publish(2)
    assert len(azure.request.call_args_list) == 1


@pytest.mark.asyncio
async def test_identity_requires_signed_exact_tenant_application_and_principal():
    configuration = settings()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    public["kid"] = "test-signing-key"
    now = datetime.now(UTC)
    claims = {"iss": "https://sts.windows.net/" + configuration.external_worker_tenant_id + "/",
              "aud": "https://management.azure.com/", "exp": now + timedelta(minutes=5),
              "nbf": now - timedelta(seconds=1), "iat": now, "idtyp": "app",
              "tid": configuration.external_worker_tenant_id,
              "appid": configuration.external_worker_client_id,
              "oid": configuration.external_worker_principal_id}
    def transport(request):
        assert request.url.host == "login.microsoftonline.com"
        return httpx.Response(200, json={"keys": [public]})
    original_client = httpx.AsyncClient
    def client(**kwargs):
        return original_client(transport=httpx.MockTransport(transport), **kwargs)
    with patch("src.services.external_worker_scaling.httpx.AsyncClient", side_effect=client):
        def token(payload, signing=key):
            return jwt.encode(payload, signing, algorithm="RS256", headers={"kid": "test-signing-key"})
        await verify_identity(token(claims), configuration)
        for field in ("tid", "appid", "oid", "aud", "iss", "idtyp"):
            with pytest.raises((ValueError, jwt.PyJWTError)):
                await verify_identity(token({**claims, field: secrets.token_hex(16)}), configuration)
        with pytest.raises(jwt.PyJWTError):
            await verify_identity(token({**claims, "exp": now - timedelta(minutes=1)}), configuration)
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with pytest.raises(jwt.PyJWTError):
            await verify_identity(token(claims, other), configuration)


@pytest.mark.asyncio
async def test_unready_and_previous_active_revisions_are_included():
    azure = object.__new__(Azure)
    azure.settings = settings()
    azure.settings.external_worker_app_resource_id = "/subscriptions/test/resourceGroups/test/providers/Microsoft.App/containerApps/test"
    responses = [httpx.Response(200, json={"properties": {"workloadProfileName": "Consumption", "configuration": {}, "template": {"scale": {"minReplicas": 0, "maxReplicas": 2}, "containers": [{"name": "worker", "env": [
        {"name": "BIFROST_WORKFLOW_QUEUE_NAME", "value": "workflow-executions"},
        {"name": "BIFROST_WORKER_CONSUMERS", "value": "workflow"},
        {"name": "BIFROST_SERVICE_CLAIM_ENABLED", "value": "false"}]}]}}}), httpx.Response(200, json={"value": [
        {"name": "test--old", "properties": {"active": True}},
        {"name": "test--new", "properties": {"active": True}},
        {"name": "test--inactive", "properties": {"active": False}}]}),
        httpx.Response(200, json={"value": [{"name": "test--old-pod"}]}),
        httpx.Response(200, json={"value": [{"name": "test--new-pod"}]})]
    azure.request = AsyncMock(side_effect=responses)
    assert await azure.replicas() == {"test--old-pod", "test--new-pod"}
