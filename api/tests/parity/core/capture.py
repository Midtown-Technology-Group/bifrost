"""Committed PostgreSQL and ordered Redis evidence, with separate SDK transport."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, cast

import redis.asyncio as redis
from sqlalchemy import select, text
from src.core.security import decrypt_secret
from src.models.orm.agent_runs import AgentRun, AgentRunStep
from src.models.orm.ai_usage import AIUsage
from src.models.orm.audit import AuditLog
from src.models.orm.execution_attempts import ExecutionAttempt
from src.models.orm.execution_lifecycle_events import ExecutionLifecycleEvent
from src.models.orm.executions import Execution, ExecutionLog, WorkflowExecutionAttempt
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, User, UserRole
from src.models.orm.work_deliveries import WorkDelivery
from src.models.orm.workflow_roles import WorkflowRole

from tests.parity.core.environment import ReferenceEnvironment
from tests.parity.core.sdk_observer import PREFIX
from tests.parity.harness import Observation, wire


def value_wire(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    return wire(value)


@dataclass
class TransportEvidence:
    sdk_requests: list[dict[str, Any]] = field(default_factory=list)
    model_requests: list[dict[str, Any]] = field(default_factory=list)
    vendor_requests: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CapturedStep:
    observation: Observation
    transport: TransportEvidence


class ScopedReferenceCapture:
    def __init__(self, environment: ReferenceEnvironment, redis_url: str):
        self.environment = environment
        self.redis = redis.from_url(redis_url, decode_responses=True)
        self.pubsub = self.redis.pubsub()
        self.barrier = f"parity:core:barrier:{environment.ids['org']}"
        self.cursors: dict[str, int] = {}
        self.buffered_events: list[dict[str, Any]] = []

    async def __aenter__(self):
        await self.pubsub.psubscribe("*")
        for kind in ("psubscribe",):
            message = await self.pubsub.get_message(timeout=5)
            assert message and message["type"] == kind, (
                "Reference Redis subscription unavailable"
            )
        return self

    async def __aexit__(self, *_):
        await self.pubsub.aclose()
        await self.redis.aclose()

    async def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        env = self.environment
        attempts = select(ExecutionAttempt.id).where(
            ExecutionAttempt.logical_job_id.in_(env.executions)
        )
        runs = select(AgentRun.id).where(AgentRun.org_id == env.ids["org"])
        tables = (
            (
                Organization,
                Organization.id.in_([env.ids["org"], env.ids["foreign_org"]]),
                (Organization.name,),
            ),
            (User, User.id.in_([env.ids["user"], env.ids["setup_user"]]), (User.name,)),
            (Role, Role.id == env.ids["role"], (Role.name,)),
            (UserRole, UserRole.user_id == env.ids["user"], (UserRole.assigned_at,)),
            (
                WorkflowRole,
                WorkflowRole.workflow_id == env.ids.get("workflow"),
                (WorkflowRole.assigned_at,),
            ),
            (Execution, Execution.id.in_(env.executions), (Execution.created_at,)),
            (
                WorkflowExecutionAttempt,
                WorkflowExecutionAttempt.execution_id.in_(env.executions),
                (WorkflowExecutionAttempt.attempt_number,),
            ),
            (
                ExecutionLog,
                ExecutionLog.execution_id.in_(env.executions),
                (ExecutionLog.sequence, ExecutionLog.id),
            ),
            (
                ExecutionAttempt,
                ExecutionAttempt.logical_job_id.in_(env.executions),
                (ExecutionAttempt.attempt_number,),
            ),
            (
                ExecutionLifecycleEvent,
                ExecutionLifecycleEvent.attempt_id.in_(attempts),
                (ExecutionLifecycleEvent.sequence,),
            ),
            (
                WorkDelivery,
                WorkDelivery.message_id.in_([str(value) for value in env.executions]),
                (WorkDelivery.created_at,),
            ),
            (AgentRun, AgentRun.org_id == env.ids["org"], (AgentRun.created_at,)),
            (AgentRunStep, AgentRunStep.run_id.in_(runs), (AgentRunStep.step_number,)),
            (AIUsage, AIUsage.execution_id.in_(env.executions), (AIUsage.sequence,)),
            (
                AuditLog,
                AuditLog.user_id.in_([env.ids["user"], env.ids["setup_user"]]),
                (AuditLog.created_at,),
            ),
        )
        result = {}
        async with env.sessions() as db:
            await db.execute(text("SET LOCAL lock_timeout = '5s'"))
            await db.execute(
                select(Execution.id)
                .where(Execution.id.in_(env.executions))
                .with_for_update(read=True)
            )
            for model, predicate, ordering in tables:
                rows = (
                    (
                        await db.execute(
                            select(model).where(predicate).order_by(*ordering)
                        )
                    )
                    .scalars()
                    .all()
                )
                records = []
                for row in rows:
                    record = {
                        column.name: value_wire(
                            getattr(
                                row, model.__mapper__.get_property_by_column(column).key
                            )
                        )
                        for column in model.__table__.columns
                    }
                    if isinstance(row, User):
                        assert (
                            row.hashed_password is None
                            and row.avatar_data is None
                            and row.webauthn_user_id is None
                        ), "Unexpected fixture credential material"
                    if isinstance(row, WorkDelivery):
                        # Retain semantic evidence from the authenticated
                        # decrypted synthetic envelope, never encrypted bytes.
                        encrypted = record.pop("encrypted_envelope")
                        record["encrypted_envelope_present"] = bool(encrypted)
                        record["envelope"] = json.loads(decrypt_secret(encrypted))
                        assert "engine_token" not in record["envelope"]["body"], (
                            "Unexpected queue credential; stop capture"
                        )
                    records.append(record)
                result[model.__tablename__] = records
        return result

    async def drain_events(self) -> list[dict[str, Any]]:
        await self.redis.publish(self.barrier, "barrier")
        events = self.buffered_events
        self.buffered_events = []
        deadline = time.monotonic() + 5
        owned = {str(value) for value in self.environment.executions}
        owned.add(str(self.environment.ids["user"]))
        while time.monotonic() < deadline:
            message = await self.pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1
            )
            if not message:
                continue
            if message["channel"] == self.barrier:
                return events
            try:
                payload = json.loads(message["data"])
            except (ValueError, TypeError):
                payload = {"raw_payload": message["data"]}
            channel_owned = any(
                message["channel"].endswith(f":{identity}") for identity in owned
            )
            payload_owned = isinstance(payload, dict) and any(
                payload.get(key) in owned
                for key in ("executionId", "execution_id", "executed_by", "user_id")
                if isinstance(payload.get(key), str)
            )
            if channel_owned or payload_owned:
                events.append({"channel": message["channel"], "payload": payload})
        raise AssertionError("Reference Redis capture barrier unavailable")

    async def wait_terminal_events(self) -> None:
        # The domain commit precedes terminal publication. Delivery settlement
        # alone therefore cannot establish publication presence/absence.
        expected = {str(value) for value in self.environment.executions}
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            events = await self.drain_events()
            self.buffered_events.extend(events)
            terminal = {
                event["payload"].get("execution_id")
                for event in self.buffered_events
                if event["channel"] == "bifrost:history:GLOBAL"
                and isinstance(event["payload"], dict)
                and event["payload"].get("type") == "history_update"
                and event["payload"].get("status")
                in {"Success", "Failed", "Cancelled", "Timeout"}
            }
            if expected <= terminal:
                return
            # The next FIFO barrier bounds a new observation; this yield does
            # not establish absence. Missing terminal publication fails.
            await asyncio.sleep(0.01)
        raise AssertionError("Owned terminal Redis publication not observed")

    async def drain_requests(self) -> TransportEvidence:
        records = []
        for execution in self.environment.executions:
            key = f"{PREFIX}{execution}:requests"
            cursor = self.cursors.get(key, 0)
            # Redis shares command annotations with its synchronous client;
            # this connection is explicitly redis.asyncio with text decoding.
            new = await cast(Awaitable[list[str]], self.redis.lrange(key, cursor, -1))
            self.cursors[key] = cursor + len(new)
            records.extend(json.loads(value) for value in new)
        return TransportEvidence(sdk_requests=records)

    async def settled(self) -> bool:
        snapshot = await self.snapshot()
        executions = snapshot["executions"]
        deliveries = snapshot["work_deliveries"]
        return (
            bool(executions)
            and len(deliveries) == len(self.environment.executions)
            and all(row["status"] in {"completed", "poison"} for row in deliveries)
            and all(
                row["status"] in {"Success", "Failed", "Cancelled", "Timeout"}
                for row in executions
            )
        )


def now() -> datetime:
    return datetime.now(UTC)
