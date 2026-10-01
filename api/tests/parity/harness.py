"""Backend-neutral HTTP seam; real PostgreSQL/Redis evidence, never route mocks."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID, uuid4

import httpx
import redis.asyncio as redis
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from src.models.orm.device_job_logs import DeviceJobLog
from src.models.orm.device_jobs import DeviceJob
from src.models.orm.devices import Device
from src.models.orm.organizations import Organization
from src.services.device_keys import generate_device_key

SEED_TIME = datetime(2020, 1, 1, tzinfo=timezone.utc)


def wire(value: Any) -> Any:
    if isinstance(value, (UUID, datetime)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    if isinstance(value, dict):
        return {key: wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(item) for item in value]
    return value


@dataclass
class Observation:
    step: str
    status: int
    body: Any
    database: dict[str, Any]
    events: list[dict[str, Any]]
    before: datetime
    after: datetime


class EvidenceCapture(Protocol):
    async def snapshot(self) -> dict[str, Any]: ...

    async def drain_events(self) -> list[dict[str, Any]]: ...


class BackendUnavailable(RuntimeError):
    """A requested backend has no implemented device protocol."""


class DeviceAdapter:
    def __init__(
        self, name: str, base_url: str, capture: EvidenceCapture,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.name = name
        self.capture = capture
        self.client = httpx.AsyncClient(base_url=base_url, transport=transport, timeout=30)

    async def close(self) -> None:
        await self.client.aclose()

    async def request(
        self, step: str, route: str, body: dict[str, Any], key: str | None,
    ) -> Observation:
        before = datetime.now(timezone.utc)
        response = await self.client.post(
            route, json=body, headers={"X-Bifrost-Key": key} if key else {},
        )
        try:
            response_body = response.json() if response.content else None
        except ValueError:
            response_body = {"raw_body": response.text}
        # A new DB connection observes committed effects, independently of HTTP.
        snapshot = await self.capture.snapshot()
        events = await self.capture.drain_events()
        after = datetime.now(timezone.utc)
        return Observation(
            step, response.status_code,
            response_body, snapshot, events, before, after,
        )


def rust_adapter(*args: Any, **kwargs: Any) -> DeviceAdapter:
    raise BackendUnavailable("Rust device routes are unavailable in W0; integration starts in W2")


class SeededEnvironment:
    """Own two synthetic tenants; share this object only for mixed-writer mode.

    Separate instances are disjoint scopes even on one test PostgreSQL instance.
    Separate engines/Redis URLs also work without changing the scenarios.
    """

    def __init__(self, engine: AsyncEngine):
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.ids = {name: uuid4() for name in ("org", "foreign_org", "device", "foreign_device", "job", "session", "other_session", "wrong_token", "unknown_job")}
        self.keys: dict[str, str] = {}
        self.seed_hashes: dict[UUID, str] = {}

    @property
    def bindings(self) -> dict[str, str]:
        return {str(value): name for name, value in self.ids.items()}

    async def seed(self, status: str = "pending", with_job: bool = True) -> None:
        async with self.sessions() as db:
            for prefix in ("", "foreign_"):
                db.add(Organization(
                    id=self.ids[prefix + "org"], name="synthetic-parity",
                    created_by="parity-harness", created_at=SEED_TIME, updated_at=SEED_TIME,
                ))
            await db.flush()
            for prefix in ("", "foreign_"):
                raw, hashed = generate_device_key(self.ids[prefix + "device"])
                self.keys[prefix + "device"] = raw
                self.seed_hashes[self.ids[prefix + "device"]] = hashed
                db.add(Device(
                    id=self.ids[prefix + "device"], organization_id=self.ids[prefix + "org"],
                    display_name="synthetic-parity", status="active", api_key_enabled=True,
                    api_key_hash=hashed, created_at=SEED_TIME, updated_at=SEED_TIME,
                ))
            await db.flush()
            if with_job:
                db.add(DeviceJob(
                    id=self.ids["job"], organization_id=self.ids["org"], device_id=self.ids["device"],
                    status=status, script_name="parity-probe", script_content="Write-Output parity",
                    params={"probe": True}, timeout_seconds=60, max_output_bytes=1024,
                    log_sequence=0, created_at=SEED_TIME, updated_at=SEED_TIME,
                ))
            await db.commit()

    async def update(self, model: type, identity: str, **changes: Any) -> None:
        async with self.sessions() as db:
            row = await db.get(model, self.ids[identity])
            assert row is not None
            for field, value in changes.items():
                setattr(row, field, value)
            # Test arrangements have explicit time, separate from backend writes.
            row.updated_at = SEED_TIME
            await db.commit()

    async def cleanup(self) -> None:
        async with self.sessions() as db:
            await db.execute(delete(Organization).where(Organization.id.in_([self.ids["org"], self.ids["foreign_org"]])))
            await db.commit()


class PostgresRedisCapture:
    def __init__(self, environment: SeededEnvironment, redis_url: str):
        self.environment = environment
        self.redis = redis.from_url(redis_url, decode_responses=True)
        self.pubsub = self.redis.pubsub()
        self.barrier = f"parity-barrier:{uuid4()}"
        self.channels = [
            f"bifrost:device:{environment.ids['device']}",
            f"bifrost:device:{environment.ids['foreign_device']}",
            f"bifrost:device_job:{environment.ids['job']}", self.barrier,
        ]

    async def __aenter__(self) -> PostgresRedisCapture:
        await self.pubsub.subscribe(self.barrier)
        # Observe misrouted publications too, including wrong channel prefixes.
        await self.pubsub.psubscribe("bifrost:*")
        for kind in ("subscribe", "psubscribe"):
            ack = await self.pubsub.get_message(timeout=5)
            assert ack and ack["type"] == kind, "Redis capture subscription unavailable"
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.pubsub.aclose()
        await self.redis.aclose()

    async def snapshot(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        async with self.environment.sessions() as db:
            await db.execute(text("SET LOCAL lock_timeout = '5s'"))
            # FastAPI's request-scoped get_db commits on dependency teardown,
            # which may follow HTTP response delivery. Domain writers lock the
            # job FOR UPDATE: this SHARE lock waits for their commit/rollback
            # before any evidence reads, including handled-error partial writes.
            await db.execute(
                select(DeviceJob.id).where(DeviceJob.device_id.in_([
                    self.environment.ids["device"], self.environment.ids["foreign_device"],
                ])).with_for_update(read=True)
            )
            for model, label, predicate, ordering in (
                (Device, "devices", Device.id.in_([self.environment.ids["device"], self.environment.ids["foreign_device"]]), Device.display_name),
                (DeviceJob, "jobs", DeviceJob.device_id.in_([self.environment.ids["device"], self.environment.ids["foreign_device"]]), DeviceJob.created_at),
                (DeviceJobLog, "logs", DeviceJobLog.job_id == self.environment.ids["job"], DeviceJobLog.seq),
            ):
                rows = (await db.execute(select(model).where(predicate).order_by(ordering))).scalars().all()
                values = []
                for row in rows:
                    fields = {column.name: wire(getattr(row, column.name)) for column in model.__table__.columns if not column.name.endswith("hash")}
                    if isinstance(row, Device):
                        fields["api_key_hash_present"] = row.api_key_hash is not None
                        fields["api_key_hash_unchanged"] = row.api_key_hash == self.environment.seed_hashes[row.id]
                        fields["enrollment_token_hash_present"] = row.enrollment_token_hash is not None
                    values.append(fields)
                # Device rows use semantic fixture role, never random UUID order.
                if label == "devices":
                    values.sort(key=lambda item: self.environment.bindings[item["id"]])
                result[label] = values
        return result

    async def drain_events(self) -> list[dict[str, Any]]:
        # Redis FIFO barrier proves absence without a sleep/quiet-period guess.
        await self.redis.publish(self.barrier, "barrier")
        result = []
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            message = await self.pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
            if message:
                if message["channel"] == self.barrier:
                    return result
                payload = json.loads(message["data"])
                scoped_ids = {str(self.environment.ids[name]) for name in ("device", "foreign_device", "job")}
                relevant = message["channel"] in self.channels or (
                    isinstance(payload, dict) and any(
                        isinstance(payload.get(field), str) and payload[field] in scoped_ids
                        for field in ("job_id", "device_id")
                    )
                )
                if relevant:
                    result.append({"channel": message["channel"], "payload": payload})
        raise AssertionError("Redis evidence barrier unavailable")
