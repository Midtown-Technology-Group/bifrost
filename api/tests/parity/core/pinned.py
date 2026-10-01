"""Real reviewed Solution installation and separately attributed source transport."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Awaitable
from dataclasses import dataclass, field
from io import BytesIO
from typing import cast
from uuid import UUID, uuid4
from zipfile import ZipFile

from bifrost.solution_delivery_review import (
    WORKFLOW_RECIPE_SCHEMA,
    ReviewedWorkflowRecipe,
)
from sqlalchemy import select, text
from src.core.module_cache_contract import (
    MODULE_INDEX_KEY,
    MODULE_KEY_PREFIX,
    WORKSPACE_GENERATION_KEY,
    WORKSPACE_UPDATING_PREFIX,
)
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from src.models.orm.solution_deployments import (
    SolutionDeployment,
    SolutionDeploymentDependency,
)
from src.models.orm.solutions import Solution
from src.models.orm.work_deliveries import WorkDelivery
from src.models.orm.workflows import Workflow
from src.services.solutions.deployment_storage import SolutionDeploymentStorage

from tests.parity.core.capture import (
    CapturedStep,
    ScopedReferenceCapture,
    TransportEvidence,
    now,
    value_wire,
)
from tests.parity.core.environment import SOURCE, SOURCE_SHA256, ReferenceEnvironment
from tests.parity.core.sdk_observer import PREFIX
from tests.parity.harness import Observation

SOURCE_COMMIT = "83c1cb034dbbcfa29eb1723506735b13b4788536"
SOURCE_PATH = "features/utilities/workflows/check_integration_readiness.py"
FUNCTION = "check_integration_readiness"
PORTABLE_REF = f"{SOURCE_PATH}::{FUNCTION}"
SOURCE_HASH = f"sha256:{SOURCE_SHA256}"


def generation_read(value, started, completed):
    """Record an independent Redis read; never initialize a cold generation."""
    assert (
        type(value) is str and value and not value.startswith(WORKSPACE_UPDATING_PREFIX)
    ), "Pinned workspace generation cold or updating; source witness unavailable"
    assert started.tzinfo is not None and completed.tzinfo is not None
    assert started <= completed, "Pinned generation read clock reversed"
    return {
        "value": value,
        "started_at": started.isoformat(),
        "completed_at": completed.isoformat(),
    }


def assert_source_request(
    method: str, path: str, query: bytes, raw: bytes, registry: dict
) -> None:
    source = registry.get("source")
    assert isinstance(source, dict) and set(source) == {"path", "sha256"}, (
        "Unapproved source registry"
    )
    assert source["sha256"] == SOURCE_SHA256, "Unapproved source pin"
    assert method == "GET" and path == f"api/sdk/modules/{source['path']}", (
        "Unapproved source request"
    )
    assert query == b"" and raw == b"", "Unapproved source request bytes"
    solution = str(UUID(registry["solution_id"]))
    deployment = str(UUID(registry["deployment_id"]))
    assert source["path"] == f"_solutions/{solution}/{deployment}/{SOURCE_PATH}", (
        "Unapproved source authority"
    )


def assert_source_response(status: int, body, registry: dict) -> None:
    assert status == 200 and isinstance(body, dict), "Unapproved source response"
    assert (
        {"content", "path", "hash"}
        <= set(body)
        <= {"content", "path", "hash", "storage_path", "generation"}
    ), "Unapproved CachedModule fields"
    assert body["path"] == registry["source"]["path"], "Source response path differs"
    assert body.get("storage_path", body["path"]) == registry["source"]["path"], (
        "Source storage authority differs"
    )
    assert "generation" not in body, "Immutable source acquired workspace generation"
    assert (
        isinstance(body["content"], str)
        and body["content"].encode() == SOURCE.read_bytes()
    ), "Source bytes differ from retained workspace blob"
    assert (
        body["hash"]
        == SOURCE_SHA256
        == hashlib.sha256(body["content"].encode()).hexdigest()
    ), "Source hash differs from pin"


@dataclass
class PinnedTransportEvidence(TransportEvidence):
    source_requests: list[dict] = field(default_factory=list)


class PinnedEnvironment(ReferenceEnvironment):
    """Installation authority is separate from the unchanged normal execution caller."""

    def __init__(self, engine):
        super().__init__(engine)
        self.path = SOURCE_PATH
        self.ids.update(workflow=uuid4(), deployment=uuid4())
        self.bindings.update(
            {
                str(self.ids["workflow"]): "workflow",
                str(self.ids["deployment"]): "deployment",
            }
        )
        self.receipts: list[CapturedStep] = []
        self.recipe = None
        self.capture: PinnedCapture | None = None
        self.storage = None
        self.generation_witnesses: dict[str, dict] = {}

    @property
    def base(self):
        return f"/api/solutions/{self.ids['solution']}/deployments/{self.ids['deployment']}/initial-workflow"

    def reviewed_recipe(self):
        return ReviewedWorkflowRecipe.model_validate(
            {
                "schema_version": WORKFLOW_RECIPE_SCHEMA,
                "solution_id": str(self.ids["solution"]),
                "files": {SOURCE_PATH: SOURCE_PATH},
                "workflows": [
                    {
                        "id": str(self.ids["workflow"]),
                        "path": SOURCE_PATH,
                        "function_name": FUNCTION,
                        "organization_id": str(self.ids["org"]),
                        "runtime_bounds": {
                            "max_duration_seconds": 60,
                            "max_external_calls": 10,
                            "max_records_read": 100,
                            "max_output_bytes": 4096,
                        },
                        "controls": {
                            "timeout_seconds": 60,
                            "role_ids": [str(self.ids["role"])],
                            "endpoint_enabled": False,
                            "public_endpoint": False,
                        },
                    }
                ],
            }
        )

    async def installation_request(self, step, path, body, *, before=None):
        response = await self.client.post(path, json=body)
        payload = response.json()
        if step == "create-solution" and response.status_code == 201:
            self.ids["solution"] = UUID(payload["id"])
            self.bindings[str(self.ids["solution"])] = "solution"
            self.storage = SolutionDeploymentStorage(
                self.ids["solution"], self.ids["deployment"]
            )
            prefix = self.storage.runtime_prefix
            self.bindings.update(
                {
                    prefix: "runtime-prefix",
                    f"{prefix}{SOURCE_PATH}": "source-storage-path",
                    self.storage.source_artifact_key: "source-artifact-key",
                    self.storage.manifest_key: "manifest-key",
                }
            )
        assert self.capture is not None
        captured = CapturedStep(
            Observation(
                step,
                response.status_code,
                payload,
                await self.capture.snapshot(),
                await self.capture.drain_events(),
                before or self.before,
                now(),
            ),
            await self.capture.drain_requests(),
        )
        self.receipts.append(captured)
        return captured

    async def stage(self):
        created = await self.installation_request(
            "create-solution",
            "/api/solutions",
            {
                "slug": "core-pinned-reference",
                "name": "Core pinned reference",
                "organization_id": str(self.ids["org"]),
            },
        )
        assert created.observation.status == 201, (
            "Public owned Solution creation failed"
        )
        self.recipe = self.reviewed_recipe().model_dump(mode="json")
        candidate = await self.installation_request(
            "candidate",
            f"{self.base}/candidate",
            {
                "source_commit_sha": SOURCE_COMMIT,
                "reviewed_recipe": self.recipe,
                "files": [
                    {
                        "path": SOURCE_PATH,
                        "content_base64": base64.b64encode(
                            SOURCE.read_bytes()
                        ).decode(),
                    }
                ],
            },
        )
        assert candidate.observation.status == 200, (
            "Public pinned candidate rejected unchanged source"
        )
        self.assert_receipt(candidate, "ready")
        self.evidence_id = candidate.observation.body["evidence_id"]
        inspected = await self.installation_request(
            "preflight", f"{self.base}/preflight", {"reviewed_recipe": self.recipe}
        )
        assert inspected.observation.status == 200, "Public pinned preflight failed"
        self.assert_receipt(inspected, "ready")
        assert inspected.observation.body["evidence_id"] == self.evidence_id
        await self.verify_storage()
        await self.assert_installation_state(active=False)

    def assert_receipt(self, captured, state):
        body = captured.observation.body
        assert body["solution_id"] == str(self.ids["solution"])
        assert body["deployment_id"] == str(self.ids["deployment"])
        assert body["organization_id"] == str(self.ids["org"])
        assert body["workflow_ids"] == [str(self.ids["workflow"])]
        assert body["source_commit_sha"] == SOURCE_COMMIT and body["source_hashes"] == {
            SOURCE_PATH: SOURCE_HASH
        }
        assert body["state"] == state

    async def activate(self, evidence_id=None):
        return await self.installation_request(
            "activate",
            f"{self.base}/activate",
            {
                "reviewed_recipe": self.recipe,
                "expected_evidence_id": evidence_id or self.evidence_id,
            },
        )

    async def install(self):
        await self.stage()
        activated = await self.activate()
        assert activated.observation.status == 200, "Public pinned activation failed"
        self.assert_receipt(activated, "active")
        assert activated.observation.body["evidence_id"] == self.evidence_id
        await self.assert_installation_state(active=True)
        # A deployment does not write the mutable workspace or its generation.
        self.generation = None

    async def verify_storage(self):
        assert self.storage is not None
        runtime = await self.storage.read_runtime_file(SOURCE_PATH)
        assert runtime == SOURCE.read_bytes()
        with ZipFile(BytesIO(await self.storage.read_source_artifact())) as archive:
            assert archive.namelist() == [SOURCE_PATH]
            assert archive.read(SOURCE_PATH) == runtime
        async with self.sessions() as db:
            row = await db.get(SolutionDeployment, self.ids["deployment"])
            assert row is not None
            from src.services.solutions.deployment_manifest import canonical_json

            assert await self.storage.read_compiled_manifest() == canonical_json(
                row.compiled_manifest
            )

    async def assert_installation_state(self, *, active):
        async with self.sessions() as db:
            solution = await db.get(Solution, self.ids["solution"])
            deployment = await db.get(SolutionDeployment, self.ids["deployment"])
            assert solution is not None and deployment is not None
            assert solution.organization_id == self.ids["org"]
            assert not solution.allow_outbound_access and not solution.git_connected
            assert solution.active_deployment_id == (
                self.ids["deployment"] if active else None
            )
            assert solution.execution_runtime_mode == (
                "deployment-v1" if active else "repo-v1"
            )
            assert deployment.state == ("active" if active else "ready")
            assert (
                deployment.created_by == self.ids["setup_user"]
                and deployment.git_commit_sha == SOURCE_COMMIT
            )
            assert not await db.scalar(
                select(Execution.id).where(
                    Execution.solution_deployment_id == self.ids["deployment"]
                )
            )
            rows = (
                await db.scalars(
                    select(Workflow).where(Workflow.solution_id == self.ids["solution"])
                )
            ).all()
            assert len(rows) == int(active)
            if active:
                row = rows[0]
                assert (
                    row.id == self.ids["workflow"]
                    and row.path == SOURCE_PATH
                    and row.function_name == FUNCTION
                )
                assert (
                    row.organization_id == self.ids["org"]
                    and row.access_level == "role_based"
                )
                assert not row.endpoint_enabled and not row.public_endpoint

    async def allocate_execution(self, role, *, request_name=None):
        before = await self.read_generation()
        existing = [
            raw
            for raw, owner in self.bindings.items()
            if owner == "observed-workspace-generation"
        ]
        assert not existing or existing == [before["value"]], (
            "Pinned workspace generation changed between owned admissions"
        )
        self.generation = before["value"]
        self.bindings[self.generation] = "observed-workspace-generation"
        identity = await super().allocate_execution(role, request_name=request_name)
        self.generation_witnesses[str(identity)] = {
            "source": WORKSPACE_GENERATION_KEY,
            "execution_id": str(identity),
            "before": before,
            "after": None,
        }
        key = f"{PREFIX}{identity}:owner"
        owner = json.loads(await self.redis.get(key))
        assert self.storage is not None
        owner.update(
            solution_id=str(self.ids["solution"]),
            deployment_id=str(self.ids["deployment"]),
            source={
                "path": f"{self.storage.runtime_prefix}{SOURCE_PATH}",
                "sha256": SOURCE_SHA256,
            },
        )
        await self.redis.set(key, json.dumps(owner), ex=600)
        return identity

    async def read_generation(self):
        started = now()
        value = await self.redis.get(WORKSPACE_GENERATION_KEY)
        return generation_read(value, started, now())

    async def witness_successful_generations(self, executions):
        for execution in executions:
            if execution["status"] != "Success":
                continue
            witness = self.generation_witnesses[execution["id"]]
            if witness["after"] is None:
                after = await self.read_generation()
                assert after["value"] == witness["before"]["value"], (
                    "Pinned workspace generation changed during admission"
                )
                witness["after"] = after

    async def mutate_runtime_source(self):
        """Explicit owned immutable-object corruption, never a source behavior fix."""
        assert self.storage is not None
        async with self.storage._client_factory() as client:
            await client.put_object(
                Bucket=self.storage._bucket,
                Key=f"{self.storage.runtime_prefix}{SOURCE_PATH}",
                Body=SOURCE.read_bytes() + b"\n# synthetic owned corruption\n",
                ContentType="application/octet-stream",
            )
        # Invalidate only this owned module so an existing API cache cannot hide drift.
        key = f"{self.storage.runtime_prefix}{SOURCE_PATH}"
        await self.redis.delete(f"{MODULE_KEY_PREFIX}{key}")
        await self.redis.srem(MODULE_INDEX_KEY, key)

    async def cleanup(self):
        # Deployment history is retained by the platform. Keep the whole fixture,
        # including source and tenant/principal/config dependencies, until the
        # supported isolated installation is disposed. Only retire observer keys
        # after proving that owned execution work has settled.
        try:
            async with self.sessions() as db:
                await db.execute(text("SET LOCAL lock_timeout = '5s'"))
                executions = (
                    await db.scalars(
                        select(Execution)
                        .where(Execution.id.in_(self.executions))
                        .with_for_update()
                    )
                ).all()
                assert {row.id for row in executions} == set(self.executions), (
                    "Owned execution authority missing; retain pinned fixture"
                )
                assert all(
                    row.status in {"Success", "Failed", "Cancelled", "Timeout"}
                    and row.completed_at is not None
                    for row in executions
                ), "Owned execution nonterminal; retain pinned fixture"
                active_attempts = (
                    await db.scalars(
                        select(WorkflowExecutionAttempt).where(
                            WorkflowExecutionAttempt.execution_id.in_(self.executions),
                            WorkflowExecutionAttempt.completed_at.is_(None),
                        )
                    )
                ).all()
                assert not active_attempts, (
                    "Owned workflow attempt active; retain pinned fixture"
                )
                deliveries = (
                    await db.scalars(
                        select(WorkDelivery).where(
                            WorkDelivery.message_id.in_(
                                [str(value) for value in self.executions]
                            )
                        )
                    )
                ).all()
                assert len(deliveries) == len(self.executions) and all(
                    row.status in {"completed", "poison"} for row in deliveries
                ), "Owned delivery active; retain pinned fixture"
                if "solution" in self.ids:
                    dependencies = (
                        await db.scalars(
                            select(SolutionDeploymentDependency).where(
                                SolutionDeploymentDependency.dependency_deployment_id
                                == self.ids["deployment"]
                            )
                        )
                    ).all()
                    assert not dependencies, (
                        "Unexpected external pin dependency; retain fixture"
                    )
            for execution in self.executions:
                await self.redis.delete(
                    f"{PREFIX}{execution}:owner",
                    f"{PREFIX}{execution}:requests",
                    f"{PREFIX}{execution}:sources",
                    f"{PREFIX}{execution}:observer-failures",
                )
        finally:
            # A failed settlement check retains all evidence, but must not leak
            # clients into the next test's event loop.
            try:
                await self.redis.aclose()
            finally:
                await self.client.aclose()


class PinnedCapture(ScopedReferenceCapture):
    async def snapshot(self):
        result = await super().snapshot()
        env = self.environment
        await env.witness_successful_generations(result["executions"])
        async with env.sessions() as db:
            for model, predicate in (
                (Solution, Solution.id == env.ids.get("solution")),
                (SolutionDeployment, SolutionDeployment.id == env.ids["deployment"]),
                (
                    SolutionDeploymentDependency,
                    SolutionDeploymentDependency.deployment_id == env.ids["deployment"],
                ),
                (Workflow, Workflow.id == env.ids["workflow"]),
            ):
                rows = (await db.scalars(select(model).where(predicate))).all()
                result[model.__tablename__] = [
                    {
                        column.name: value_wire(
                            getattr(
                                row, model.__mapper__.get_property_by_column(column).key
                            )
                        )
                        for column in model.__table__.columns
                    }
                    for row in rows
                ]
        return result

    async def drain_requests(self):
        ordinary = await super().drain_requests()
        sources = []
        for execution in self.environment.executions:
            key = f"{PREFIX}{execution}:sources"
            cursor = self.cursors.get(key, 0)
            # The inherited Redis command annotation also permits sync results;
            # this client is explicitly redis.asyncio with decode_responses=True.
            records = await cast(
                Awaitable[list[str]], self.redis.lrange(key, cursor, -1)
            )
            self.cursors[key] = cursor + len(records)
            sources.extend(json.loads(record) for record in records)
        return PinnedTransportEvidence(
            sdk_requests=ordinary.sdk_requests, source_requests=sources
        )
