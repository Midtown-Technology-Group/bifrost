"""Optional bounded infrastructure controller for external PostgreSQL workers.

Domain execution and delivery remain native. Only demand and network identity
bookkeeping live here, in the existing global system configuration store.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import jwt
from sqlalchemy import String, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings, get_settings
from src.core.database import get_db_context
from src.core.security import decrypt_secret, encrypt_secret
from src.models.enums import ExecutionStatus
from src.models.orm.config import SystemConfig
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from src.models.orm.work_deliveries import WorkDelivery
from src.repositories.integrations import IntegrationsRepository

logger = logging.getLogger(__name__)
CATEGORY = "external_worker_scaling"
ARM_SCOPE = "https://management.azure.com/.default"
STORAGE_SCOPE = "https://storage.azure.com/.default"
_identity_keys: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_identity_keys_lock = asyncio.Lock()


def validate_settings(settings: Settings) -> None:
    resource = settings.external_worker_app_resource_id
    if not re.fullmatch(r"/subscriptions/[0-9a-f-]{36}/resourceGroups/[A-Za-z0-9_.-]+/providers/Microsoft.App/containerApps/[a-z0-9-]+", resource):
        raise ValueError("Invalid external worker app resource")
    if not re.fullmatch(r"[a-z0-9]{3,24}", settings.external_worker_queue_account):
        raise ValueError("Invalid scaling storage account")
    if not re.fullmatch(r"bifrost-worker-demand-[a-z0-9-]+", settings.external_worker_queue_name):
        raise ValueError("Invalid scaling queue")
    for value in (settings.external_worker_tenant_id, settings.external_worker_client_id,
                  settings.external_worker_principal_id):
        UUID(value)
    if not settings.external_worker_defined_network_id.startswith("network-") or not settings.external_worker_defined_role_id.startswith("role-"):
        raise ValueError("Missing fixed Defined network/role")
    if not re.fullmatch(r"api://[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", settings.external_worker_enrollment_audience):
        raise ValueError("Missing dedicated enrollment audience")
    if settings.work_delivery_backend != "postgres":
        raise ValueError("External workers require PostgreSQL delivery")


def capacity_decision(*, pending: int, active: int, now: float,
                      idle_since: float | None, maximum: int, idle_seconds: int) -> tuple[int, float | None]:
    """Never partially scale in while domain work is active; errors are not zero."""
    if min(pending, active) < 0 or maximum < 1:
        raise ValueError("Invalid scaling observation")
    if pending or active:
        return maximum, None
    idle_since = now if idle_since is None else idle_since
    if now < idle_since:
        return maximum, now
    return (0 if now - idle_since >= idle_seconds else maximum), idle_since


async def row(db: AsyncSession, key: str) -> SystemConfig:
    rows = list((await db.scalars(select(SystemConfig).where(
        SystemConfig.category == CATEGORY, SystemConfig.key == key,
        SystemConfig.organization_id.is_(None)))).all())
    if len(rows) > 1:
        raise RuntimeError("Duplicate external worker state")
    if rows:
        return rows[0]
    value = SystemConfig(category=CATEGORY, key=key, organization_id=None,
                         value_json={}, created_by="external-worker-controller")
    db.add(value)
    await db.flush()
    return value


async def lock(db: AsyncSession) -> bool:
    return bool(await db.scalar(text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                               {"key": CATEGORY}))


async def demand_counts(db: AsyncSession, queue_name: str = "workflow-executions") -> tuple[int, int]:
    # Count this workflow lane's deliveries, including unexpired claims.
    # Domain rows/attempts outlive completed deliveries and must protect scale-in.
    pending = int(await db.scalar(select(func.count()).select_from(WorkDelivery).where(
        WorkDelivery.queue_name == queue_name,
        WorkDelivery.status.in_(("queued", "claimed", "interrupted")))) or 0)
    active = 0
    predicates = (
        (Execution, Execution.status.in_((ExecutionStatus.PENDING, ExecutionStatus.RUNNING, ExecutionStatus.CANCELLING))),
        (WorkflowExecutionAttempt, WorkflowExecutionAttempt.completed_at.is_(None)),
    )
    for model, predicate in predicates:
        # The workflow ID is retained on completed deliveries, so domain work
        # remains visible after settlement. Match only this lane's queue.
        identity = model.id if model is Execution else model.execution_id
        active += int(await db.scalar(select(func.count()).select_from(model).where(
            predicate, select(WorkDelivery.id).where(WorkDelivery.queue_name == queue_name,
                WorkDelivery.message_id == cast(identity, String)).exists())) or 0)
    return pending, active


class Azure:
    """Only fixed configured Azure destinations; credentials never enter logs."""
    def __init__(self, settings: Settings):
        from azure.identity.aio import ManagedIdentityCredential
        import os
        self.settings = settings
        self.workflow_queue = ""
        self.credential = ManagedIdentityCredential(client_id=os.environ.get("AZURE_CLIENT_ID"))
        self.client = httpx.AsyncClient(timeout=20, follow_redirects=False)

    async def request(self, method: str, url: str, *, storage: bool = False, **kwargs: Any) -> httpx.Response:
        token = await self.credential.get_token(STORAGE_SCOPE if storage else ARM_SCOPE)
        headers = {"Authorization": "Bearer " + token.token}
        if storage:
            headers.update({"x-ms-version": "2023-11-03", "x-ms-date": datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")})
        response = await self.client.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()
        return response

    async def replicas(self) -> set[str]:
        base = "https://management.azure.com" + self.settings.external_worker_app_resource_id
        application = (await self.request("GET", base, params={"api-version": "2025-01-01"})).json()["properties"]
        scaling = application["template"]["scale"]
        if application.get("workloadProfileName") != "Consumption" or application["configuration"].get("ingress") or scaling["minReplicas"] != 0 or scaling["maxReplicas"] != self.settings.external_worker_max_replicas:
            raise RuntimeError("External worker app escaped its declared resource bounds")
        workers = [container for container in application["template"]["containers"] if container["name"] == "worker"]
        if len(workers) != 1:
            raise RuntimeError("External workflow worker is ambiguous")
        environment = {entry["name"]: entry.get("value") for entry in workers[0]["env"]}
        queue = environment.get("BIFROST_WORKFLOW_QUEUE_NAME", "")
        if queue != "workflow-executions" and not re.fullmatch(r"workflow-executions-[a-z0-9-]+-canary", queue):
            raise RuntimeError("External workflow queue is outside the declared lane")
        if environment.get("BIFROST_WORKER_CONSUMERS") != "workflow" or environment.get("BIFROST_SERVICE_CLAIM_ENABLED") != "false":
            raise RuntimeError("External worker must retain the workflow-only boundary")
        self.workflow_queue = queue
        revisions = (await self.request("GET", base + "/revisions", params={"api-version": "2025-01-01"})).json()
        if revisions.get("nextLink"):
            raise RuntimeError("Unexpected replica pagination")
        live: set[str] = set()
        for item in revisions["value"]:
            if not item["properties"]["active"]:
                continue
            revision = item["name"]
            if not isinstance(revision, str) or "/" in revision:
                raise RuntimeError("Invalid worker revision")
            result = (await self.request("GET", base + "/revisions/" + revision + "/replicas",
                                        params={"api-version": "2025-01-01"})).json()
            if result.get("nextLink"):
                raise RuntimeError("Unexpected replica pagination")
            live.update(item["name"] for item in result["value"])
        return live

    async def publish(self, desired: int) -> None:
        url = ("https://" + self.settings.external_worker_queue_account + ".queue.core.windows.net/"
               + self.settings.external_worker_queue_name + "/messages")
        # Never receive/hide/refresh demand while busy. Permanent opaque markers
        # retain capacity through controller failure. Clear only after durable idle.
        from defusedxml.ElementTree import fromstring
        messages = await self.request("GET", url, storage=True,
                                      params={"peekonly": "true", "numofmessages": 32})
        document = fromstring(messages.content)
        if document.tag != "QueueMessagesList":
            raise RuntimeError("Invalid scaling queue response")
        items = document.findall("QueueMessage")
        if any(item.findtext("MessageText") != "bifrost-worker-demand-v1" for item in items):
            raise RuntimeError("Scaling queue contains foreign contents")
        count = len(items)
        if count > self.settings.external_worker_max_replicas:
            raise RuntimeError("Scaling queue has unexpected contents")
        if desired == 0:
            if count:
                await self.request("DELETE", url, storage=True)
            return
        for _ in range(desired - count):
            await self.request("POST", url, storage=True, params={"messagettl": -1},
                               content="<QueueMessage><MessageText>bifrost-worker-demand-v1</MessageText></QueueMessage>")

    async def close(self) -> None:
        await self.client.aclose()
        await self.credential.close()


async def defined_token(db: AsyncSession) -> str:
    repository = IntegrationsRepository(db)
    integration = await repository.get_integration_by_name("Defined Networking")
    if integration is None:
        raise RuntimeError("Defined integration is missing")
    config = await repository.get_integration_defaults(integration.id, external=False)
    value = config.get("api_token")
    if not isinstance(value, str) or not value:
        raise RuntimeError("Defined integration credential is missing")
    return value


async def verify_identity(token: str, settings: Settings) -> None:
    """Verify Entra signature and exact managed service principal, not user JWTs."""
    audience = settings.external_worker_enrollment_audience
    if not re.fullmatch(r"api://[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", audience):
        raise ValueError("Missing dedicated enrollment audience")
    tenant = str(UUID(settings.external_worker_tenant_id))
    if len(token) > 16384:
        raise ValueError("Oversized identity")
    header = jwt.get_unverified_header(token)
    if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
        raise ValueError("Invalid identity algorithm")
    unsigned = jwt.decode(token, options={"verify_signature": False})
    # Early rejection saves external JWKS requests. Authorization still requires
    # the verified signature and claims below; this is not authentication.
    if unsigned.get("tid") != tenant or unsigned.get("appid") != settings.external_worker_client_id or unsigned.get("oid") != settings.external_worker_principal_id:
        raise ValueError("Identity is outside the external worker lane")
    async with _identity_keys_lock:
        cached = _identity_keys.get(tenant)
        now = datetime.now(UTC).timestamp()
        if cached is None or now - cached[0] >= 600:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.get("https://login.microsoftonline.com/" + tenant + "/discovery/keys")
                response.raise_for_status()
                cached = (now, response.json()["keys"])
                _identity_keys[tenant] = cached
    keys = [key for key in cached[1] if key.get("kid") == header.get("kid")]
    if len(keys) != 1:
        raise ValueError("Invalid identity signing key")
    claims = jwt.decode(token, jwt.PyJWK.from_dict(keys[0]).key, algorithms=["RS256"],
                        audience=audience, issuer="https://sts.windows.net/" + tenant + "/",
                        options={"require": ["exp", "nbf", "iat", "oid", "tid", "appid"]})
    if claims["tid"] != tenant or claims["oid"] != settings.external_worker_principal_id or claims["appid"] != settings.external_worker_client_id or claims.get("idtyp", "app") != "app":
        raise ValueError("Identity is outside the external worker lane")


async def find_intended_host(client: httpx.AsyncClient, token: str, settings: Settings, name: str) -> str | None:
    matches: list[str] = []
    cursor: str | None = None
    for _ in range(100):
        parameters = {"pageSize": "100", "networkID": settings.external_worker_defined_network_id}
        if cursor:
            parameters["cursor"] = cursor
        response = await client.get("https://api.defined.net/v2/hosts", params=parameters,
                                    headers={"Authorization": "Bearer " + token})
        response.raise_for_status()
        payload = response.json()
        for host in payload["data"]:
            if host["name"] == name:
                if host["networkID"] != settings.external_worker_defined_network_id or host["roleID"] != settings.external_worker_defined_role_id:
                    raise RuntimeError("Enrollment intent has conflicting ownership")
                matches.append(host["id"])
        metadata = payload["metadata"]
        if not metadata["hasNextPage"]:
            if len(matches) > 1:
                raise RuntimeError("Duplicate intended Defined host")
            return matches[0] if matches else None
        next_cursor = metadata["nextCursor"]
        if not next_cursor or next_cursor == cursor:
            raise RuntimeError("Defined pagination did not advance")
        cursor = next_cursor
    raise RuntimeError("Defined host listing bound exceeded")


async def verify_owned_host(client: httpx.AsyncClient, token: str, settings: Settings,
                            host_id: str, name: str) -> bool:
    if not re.fullmatch(r"host-[A-Z0-9]+", host_id):
        raise RuntimeError("Invalid recorded host identity")
    response = await client.get("https://api.defined.net/v2/hosts/" + host_id,
                                headers={"Authorization": "Bearer " + token})
    if response.status_code == 404:
        return False
    response.raise_for_status()
    host = response.json()["data"]
    if host["name"] != name or host["networkID"] != settings.external_worker_defined_network_id or host["roleID"] != settings.external_worker_defined_role_id or host.get("isLighthouse") or host.get("isRelay"):
        raise RuntimeError("Recorded host is outside the external worker lane")
    return True


async def enroll_replica(db: AsyncSession, azure: Azure, replica: str, boot: UUID) -> dict[str, str]:
    settings = azure.settings
    app_name = settings.external_worker_app_resource_id.rsplit("/", 1)[-1]
    if not re.fullmatch(re.escape(app_name) + r"--[a-z0-9-]+", replica) or replica not in await azure.replicas():
        raise ValueError("Replica is outside the external worker app")
    if not await lock(db):
        raise RuntimeError("Enrollment controller is busy")
    key = "host-" + hashlib.sha256(replica.encode()).hexdigest()
    state = await row(db, key)
    value = dict(state.value_json or {})
    if value.get("code") and value.get("boot") == str(boot):
        if datetime.now(UTC).timestamp() - value["created"] > 300:
            raise ValueError("Startup enrollment expired")
        return {"code": decrypt_secret(value["code"]), "hostId": value["host_id"]}
    peers = list((await db.scalars(select(SystemConfig).where(SystemConfig.category == CATEGORY,
                  SystemConfig.key.like("host-%"), SystemConfig.organization_id.is_(None)))).all())
    if len(peers) > settings.external_worker_max_replicas:
        raise RuntimeError("External host enrollment bound exceeded")
    # Commit an intent before crossing Defined's API boundary. Ambiguous creates
    # remain durable for name-based reconciliation; never blindly create again.
    name = "bifrost-aca-prod-" + key[5:29]
    if value.get("intent") and datetime.now(UTC).timestamp() - value["created"] < 60:
        raise RuntimeError("Enrollment rate bound reached")
    previous_intent = bool(value.get("intent"))
    value.pop("code", None)  # A new boot must never receive the previous one-use code.
    state.value_json = {**value, "replica": replica, "boot": str(boot), "intent": name,
                       "created": datetime.now(UTC).timestamp()}
    await db.commit()
    if not await lock(db):
        raise RuntimeError("Enrollment controller is busy")
    token = await defined_token(db)
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        host_id = value.get("host_id")
        confirmed_missing = False
        if host_id and not await verify_owned_host(client, token, settings, host_id, name):
            # Only authoritative 404 permits recovery; ownership/errors still fail closed.
            host_id = None
            confirmed_missing = True
            cleared = dict(state.value_json or {})
            cleared.pop("host_id", None)
            state.value_json = cleared
            await db.commit()
            if not await lock(db):
                raise RuntimeError("Enrollment controller is busy")
        if previous_intent and not host_id:
            host_id = await find_intended_host(client, token, settings, name)
            if not host_id and not confirmed_missing:
                # A negative listing cannot prove an ambiguous create never succeeded.
                raise RuntimeError("External host creation intent remains unresolved")
        if host_id:
            if not await verify_owned_host(client, token, settings, host_id, name):
                raise RuntimeError("Recorded external host is missing")
            response = await client.post("https://api.defined.net/v1/hosts/" + host_id + "/enrollment-code",
                                         headers={"Authorization": "Bearer " + token}, json={})
            response.raise_for_status()
            code = response.json()["data"]["code"]
        else:
            response = await client.post("https://api.defined.net/v2/host-and-enrollment-code",
                headers={"Authorization": "Bearer " + token}, json={"name": name,
                "networkID": settings.external_worker_defined_network_id,
                "roleID": settings.external_worker_defined_role_id,
                "ipAddresses": ["100.100.0.0/22"], "tags": ["needs:infra-routes"]})
            response.raise_for_status()
            result = response.json()["data"]
            host_id, code = result["host"]["id"], result["enrollmentCode"]["code"]
    value = dict(state.value_json or {})
    value.update({"host_id": host_id, "code": encrypt_secret(code)})
    state.value_json = value
    await db.commit()
    return {"code": code, "hostId": host_id}


async def reconcile_hosts(db: AsyncSession, azure: Azure, live: set[str]) -> None:
    states = list((await db.scalars(select(SystemConfig).where(SystemConfig.category == CATEGORY,
                  SystemConfig.key.like("host-%"), SystemConfig.organization_id.is_(None)))).all())
    if not states:
        return
    now = datetime.now(UTC).timestamp()
    token = await defined_token(db)
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        for state in states:
            value = dict(state.value_json or {})
            if value.get("replica") in live:
                value.pop("absent_since", None)
                value.pop("absence_observed_at", None)
                if value.get("code") and datetime.now(UTC).timestamp() - value["created"] > 300:
                    value.pop("code")
                    state.value_json = value
                state.value_json = value
                continue
            observed_at = value.get("absence_observed_at")
            absent_since = value.get("absent_since", now)
            # Missing/stale observations (including outages) cannot prove absence.
            if observed_at is None or not 0 <= now - observed_at <= 30 or now < absent_since:
                absent_since = now
            value["absent_since"] = absent_since
            value["absence_observed_at"] = now
            state.value_json = value
            if now - absent_since < 120:
                continue
            host_id = value.get("host_id")
            if not host_id:
                host_id = await find_intended_host(client, token, azure.settings, value["intent"])
                if not host_id:
                    # Retain unresolved intent until a host can be authoritatively reconciled.
                    continue
            if not await verify_owned_host(client, token, azure.settings, host_id, value["intent"]):
                await db.delete(state)
                continue
            response = await client.delete("https://api.defined.net/v1/hosts/" + host_id,
                                           headers={"Authorization": "Bearer " + token})
            if response.status_code != 404:
                response.raise_for_status()
            response = await client.get("https://api.defined.net/v2/hosts/" + host_id,
                                        headers={"Authorization": "Bearer " + token})
            if response.status_code != 404:
                raise RuntimeError("External host deletion not confirmed")
            await db.delete(state)


async def controller_loop(stop: asyncio.Event) -> None:
    settings = get_settings()
    validate_settings(settings)
    azure = Azure(settings)
    try:
        while not stop.is_set():
            try:
                async with get_db_context() as db:
                    if await lock(db):
                        state = await row(db, "demand")
                        previous = dict(state.value_json or {})
                        live = await azure.replicas()
                        if previous.get("queue_name") != azure.workflow_queue:
                            previous.pop("idle_since", None)
                        pending, active = await demand_counts(db, azure.workflow_queue)
                        now = datetime.now(UTC).timestamp()
                        if now - previous.get("observed_at", 0) > 30:
                            previous.pop("idle_since", None)
                        desired, idle = capacity_decision(pending=pending, active=active, now=now,
                            idle_since=previous.get("idle_since"), maximum=settings.external_worker_max_replicas,
                            idle_seconds=settings.external_worker_idle_seconds)
                        await azure.publish(desired)
                        state.value_json = {"observed_at": now, "pending": pending, "active": active,
                                            "desired": desired, "idle_since": idle, "replicas": len(live),
                                            "queue_name": azure.workflow_queue}
                        await db.commit()
                        # Provider cleanup cannot roll back successful demand observation.
                        if await lock(db):
                            try:
                                await reconcile_hosts(db, azure, live)
                                await db.commit()
                            except asyncio.CancelledError:
                                raise
                            except Exception as error:
                                await db.rollback()
                                logger.error("External host reconciliation failed: %s", type(error).__name__)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Raw HTTP/SQL/provider errors can include credentials; only type.
                logger.error("External worker observation failed: %s", type(error).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=10)
            except TimeoutError:
                # The polling interval elapsed normally; observe demand again.
                pass
    finally:
        await azure.close()


async def controller_status(db: AsyncSession) -> dict:
    """Global infrastructure bookkeeping; authorized by the superuser router."""
    rows = list((await db.scalars(select(SystemConfig).where(
        SystemConfig.category == CATEGORY, SystemConfig.organization_id.is_(None)))).all())
    demand = [entry.value_json for entry in rows if entry.key == "demand"]
    if len(demand) > 1:
        raise RuntimeError("External worker state is ambiguous")
    value = demand[0] or {} if demand else {}
    observed = value.get("observed_at")
    age = datetime.now(UTC).timestamp() - observed if observed else None
    return {"enabled": get_settings().external_worker_scaling_enabled,
            "healthy": age is not None and 0 <= age <= 30,
            "observation_age_seconds": age, "demand": value,
            "tracked_hosts": sum(entry.key.startswith("host-") for entry in rows)}
