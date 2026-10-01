"""Owned synthetic arrangement, public source installation and exact cleanup."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import redis.asyncio as redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from src.config import get_settings
from src.core.module_cache_contract import (
    WORKSPACE_GENERATION_KEY,
    WORKSPACE_UPDATING_PREFIX,
)
from src.models.enums import ConfigType, ExecutionStatus
from src.models.orm.audit import AuditLog
from src.models.orm.config import Config
from src.models.orm.execution_attempts import ExecutionAttempt
from src.models.orm.execution_lifecycle_events import ExecutionLifecycleEvent
from src.models.orm.executions import Execution, ExecutionLog, WorkflowExecutionAttempt
from src.models.orm.integrations import (
    Integration,
    IntegrationConfigSchema,
    IntegrationMapping,
)
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, User, UserRole
from src.models.orm.work_deliveries import WorkDelivery
from src.models.orm.workflows import Workflow

from tests.fixtures.auth import create_test_jwt
from tests.parity.core.sdk_observer import PREFIX
from tests.parity.harness import SEED_TIME

SOURCE = Path(__file__).parent / "assets/check_integration_readiness.py.txt"
SOURCE_SHA256 = "f49b1b935ef2467f66eccf3c4b2750773f664d1f0e41f8ff6b08e3f11035a1de"
SYNTHETIC_VALUE = "core-parity-synthetic-config-value"


class ReferenceEnvironment:
    """Independent logical scope in the dedicated PostgreSQL test installation."""

    def __init__(self, engine: AsyncEngine):
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.ids = {
            name: uuid4()
            for name in (
                "org",
                "foreign_org",
                "user",
                "setup_user",
                "role",
                "integration",
                "mapping",
                "schema",
                "config",
            )
        }
        self.bindings = {str(identity): role for role, identity in self.ids.items()}
        self.name = f"core-parity-{self.ids['integration']}"
        self.email = f"core-parity-{self.ids['user']}@example.invalid"
        self.setup_email = f"core-parity-{self.ids['setup_user']}@example.invalid"
        self.path = f"parity/core/{self.ids['org']}/check_integration_readiness.py"
        self.bindings.update(
            {
                self.name: "integration-name",
                self.email: "caller-email",
                self.setup_email: "setup-email",
                self.path: "source-path",
            }
        )
        self.executions: list[UUID] = []
        self.token = create_test_jwt(
            user_id=str(self.ids["user"]),
            email=self.email,
            name="Core reference caller",
            is_superuser=False,
            organization_id=str(self.ids["org"]),
            is_provider_org=False,
        )
        setup_token = create_test_jwt(
            user_id=str(self.ids["setup_user"]),
            email=self.setup_email,
            name="Core reference setup",
            is_superuser=True,
            organization_id=str(self.ids["org"]),
            is_provider_org=False,
        )
        self.client = httpx.AsyncClient(
            base_url=os.environ["TEST_API_URL"],
            headers={"Authorization": f"Bearer {setup_token}"},
            timeout=60,
            follow_redirects=False,
        )
        self.redis = redis.from_url(
            os.environ["BIFROST_REDIS_URL"], decode_responses=True
        )
        self.installed = False
        self.before = datetime.now(UTC)

    async def seed(self, *, mapping: bool = True, configured: bool = True) -> None:
        settings = get_settings()
        assert settings.environment == "testing", (
            "Reference fixtures require a test environment"
        )
        assert settings.work_delivery_backend == "postgres", (
            "Reference runner must select PostgreSQL delivery"
        )
        assert os.environ.get("CORE_REFERENCE_ISOLATED") == "1", (
            "Root must attest dedicated test database/project"
        )
        assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == SOURCE_SHA256, (
            "Workspace source drift"
        )
        async with self.sessions() as db:
            db.add(
                Organization(
                    id=self.ids["org"],
                    name="Core reference tenant",
                    is_provider=False,
                    created_by="core-reference",
                    created_at=SEED_TIME,
                    updated_at=SEED_TIME,
                )
            )
            db.add(
                Organization(
                    id=self.ids["foreign_org"],
                    name="Core reference foreign tenant",
                    is_provider=False,
                    created_by="core-reference",
                    created_at=SEED_TIME,
                    updated_at=SEED_TIME,
                )
            )
            await db.flush()
            db.add(
                User(
                    id=self.ids["user"],
                    email=self.email,
                    name="Core reference caller",
                    organization_id=self.ids["org"],
                    is_superuser=False,
                    is_active=True,
                    created_at=SEED_TIME,
                    updated_at=SEED_TIME,
                )
            )
            db.add(
                User(
                    id=self.ids["setup_user"],
                    email=self.setup_email,
                    name="Core reference setup",
                    organization_id=self.ids["org"],
                    is_superuser=True,
                    is_active=True,
                    created_at=SEED_TIME,
                    updated_at=SEED_TIME,
                )
            )
            db.add(
                Role(
                    id=self.ids["role"],
                    name="Core reference execution role",
                    created_by="core-reference",
                    created_at=SEED_TIME,
                    updated_at=SEED_TIME,
                )
            )
            db.add(
                Integration(
                    id=self.ids["integration"],
                    name=self.name,
                    created_at=SEED_TIME,
                    updated_at=SEED_TIME,
                )
            )
            await db.flush()
            db.add(
                UserRole(
                    user_id=self.ids["user"],
                    role_id=self.ids["role"],
                    assigned_by="core-reference",
                    assigned_at=SEED_TIME,
                )
            )
            db.add(
                IntegrationConfigSchema(
                    id=self.ids["schema"],
                    integration_id=self.ids["integration"],
                    key="fixture_value",
                    type="string",
                    position=0,
                    created_at=SEED_TIME,
                    updated_at=SEED_TIME,
                )
            )
            if mapping:
                db.add(
                    IntegrationMapping(
                        id=self.ids["mapping"],
                        integration_id=self.ids["integration"],
                        organization_id=self.ids["org"],
                        entity_id="synthetic-entity",
                        entity_name="Synthetic entity",
                        created_at=SEED_TIME,
                        updated_at=SEED_TIME,
                    )
                )
            await db.flush()
            if configured:
                db.add(
                    Config(
                        id=self.ids["config"],
                        integration_id=self.ids["integration"],
                        config_schema_id=self.ids["schema"],
                        organization_id=self.ids["org"],
                        key="fixture_value",
                        value={"value": SYNTHETIC_VALUE},
                        config_type=ConfigType.STRING,
                        updated_by="core-reference",
                        created_at=SEED_TIME,
                        updated_at=SEED_TIME,
                    )
                )
            await db.commit()

    async def install(self) -> None:
        write = await self.client.put(
            "/api/files/editor/content",
            json={
                "path": self.path,
                "content": SOURCE.read_text(),
                "encoding": "utf-8",
            },
        )
        assert write.status_code in {200, 201}, (
            f"Public source installation failed: {write.status_code}"
        )
        self.installed = True
        registered = await self.client.post(
            "/api/workflows/register",
            json={
                "path": self.path,
                "function_name": "check_integration_readiness",
                "organization_id": str(self.ids["org"]),
                "role_ids": [str(self.ids["role"])],
                "access_level": "role_based",
            },
        )
        assert registered.status_code in {200, 201}, (
            f"Public workflow registration failed: {registered.status_code}"
        )
        self.ids["workflow"] = UUID(registered.json()["id"])
        self.bindings[str(self.ids["workflow"])] = "workflow"
        async with self.sessions() as db:
            row = await db.get(Workflow, self.ids["workflow"])
            assert (
                row is not None
                and row.organization_id == self.ids["org"]
                and row.path == self.path
            )
            assert (
                row.function_name == "check_integration_readiness"
                and row.type == "workflow"
            )
        retained = await self.client.get(
            "/api/files/editor/content", params={"path": self.path}
        )
        assert retained.status_code == 200
        assert (
            hashlib.sha256(retained.json()["content"].encode()).hexdigest()
            == SOURCE_SHA256
        )
        self.generation = await self.redis.get(WORKSPACE_GENERATION_KEY)
        assert self.generation and not self.generation.startswith(
            WORKSPACE_UPDATING_PREFIX
        ), "Installed workspace generation unavailable"
        self.bindings[self.generation] = "installed-source-generation"

    async def allocate_execution(
        self, role: str, *, request_name: str | None = None
    ) -> UUID:
        identity = uuid4()
        self.executions.append(identity)
        self.bindings[str(identity)] = role
        approved_name = request_name if request_name is not None else self.name
        assert approved_name in {self.name, f"{self.name}-absent", ""}, (
            "Unapproved fixture integration name"
        )
        await self.redis.set(
            f"{PREFIX}{identity}:owner",
            json.dumps(
                {
                    "user_id": str(self.ids["user"]),
                    "org_id": str(self.ids["org"]),
                    "request_name": approved_name,
                }
            ),
            ex=600,
        )
        return identity

    def parameters(self, **changes):
        return {
            "integration_name": self.name,
            "organization_id": str(self.ids["org"]),
            "required_config_keys": ["fixture_value"],
            "require_mapping": True,
            "require_oauth": False,
            **changes,
        }

    async def cleanup(self) -> None:
        # Refuse deletion while owned execution work could still be active.
        async with self.sessions() as db:
            executions = (
                (
                    await db.execute(
                        select(Execution).where(Execution.id.in_(self.executions))
                    )
                )
                .scalars()
                .all()
            )
            assert all(
                row.status
                in {
                    ExecutionStatus.SUCCESS,
                    ExecutionStatus.FAILED,
                    ExecutionStatus.CANCELLED,
                    ExecutionStatus.TIMEOUT,
                }
                and row.completed_at is not None
                for row in executions
            ), "Owned execution still active; retain fixture for diagnosis"
            active_attempt = (
                await db.execute(
                    select(WorkflowExecutionAttempt.id).where(
                        WorkflowExecutionAttempt.execution_id.in_(self.executions),
                        WorkflowExecutionAttempt.completed_at.is_(None),
                    )
                )
            ).first()
            assert active_attempt is None, (
                "Owned workflow attempt still active; retain fixture for diagnosis"
            )
            deliveries = (
                (
                    await db.execute(
                        select(WorkDelivery).where(
                            WorkDelivery.message_id.in_(
                                [str(value) for value in self.executions]
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert all(row.status in {"completed", "poison"} for row in deliveries), (
                "Owned delivery still active; retain fixture for diagnosis"
            )
        if self.installed:
            deleted = await self.client.delete(
                "/api/files/editor", params={"path": self.path}
            )
            assert deleted.status_code in {200, 204, 404}, "Owned source cleanup failed"
        async with self.sessions() as db:
            attempt_ids = select(ExecutionAttempt.id).where(
                ExecutionAttempt.logical_job_id.in_(self.executions)
            )
            await db.execute(
                delete(ExecutionLifecycleEvent).where(
                    ExecutionLifecycleEvent.attempt_id.in_(attempt_ids)
                )
            )
            await db.execute(
                delete(ExecutionAttempt).where(
                    ExecutionAttempt.logical_job_id.in_(self.executions)
                )
            )
            await db.execute(
                delete(ExecutionLog).where(
                    ExecutionLog.execution_id.in_(self.executions)
                )
            )
            await db.execute(
                delete(WorkDelivery).where(
                    WorkDelivery.message_id.in_(
                        [str(value) for value in self.executions]
                    )
                )
            )
            await db.execute(delete(Execution).where(Execution.id.in_(self.executions)))
            await db.execute(
                delete(AuditLog).where(
                    AuditLog.user_id.in_([self.ids["user"], self.ids["setup_user"]])
                )
            )
            await db.execute(delete(Workflow).where(Workflow.path == self.path))
            await db.execute(
                delete(Integration).where(Integration.id == self.ids["integration"])
            )
            await db.execute(
                delete(UserRole).where(UserRole.user_id == self.ids["user"])
            )
            await db.execute(delete(Role).where(Role.id == self.ids["role"]))
            await db.execute(
                delete(User).where(
                    User.id.in_([self.ids["user"], self.ids["setup_user"]])
                )
            )
            await db.execute(
                delete(Organization).where(
                    Organization.id.in_([self.ids["org"], self.ids["foreign_org"]])
                )
            )
            await db.commit()
        for identity in self.executions:
            await self.redis.delete(
                f"{PREFIX}{identity}:owner", f"{PREFIX}{identity}:requests"
            )
        await self.redis.aclose()
        await self.client.aclose()
