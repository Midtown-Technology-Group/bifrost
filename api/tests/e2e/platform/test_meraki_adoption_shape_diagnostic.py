"""HTTP regression: retained Meraki source and public registration shape.

The database/storage baseline is synthesized, not a production replica.
Candidate-only: never activate or execute vendor code.
"""

import base64
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import inspect as orm_inspect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import EventSourceType
from src.models.orm.events import EventSource, EventSubscription, WebhookSource
from src.models.orm.solution_deployments import SolutionDeployment
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.models.orm.users import Role
from src.models.orm.workflow_roles import WorkflowRole
from src.models.orm.workflows import Workflow
from src.services.file_storage.indexers.workflow import WorkflowIndexer
from src.services.solutions.deployment_storage import (
    SolutionDeploymentStorage,
    deployment_manifest_key,
)
from src.services.solutions.repo_workflow_adoption import _digest_row
from src.services.solutions.storage import SolutionStorage
from src.services.solutions.workflow_revision_recipe import (
    ReviewedWorkflowRecipe,
    compile_workflow_registrations,
)

pytestmark = pytest.mark.e2e
_FIXTURE_ROOT = Path(__file__).parents[2] / "fixtures" / "meraki_adoption_shape"


def _row_key(row):
    return (type(row).__name__, tuple(
        str(getattr(row, column.key)) for column in orm_inspect(type(row)).primary_key
    ))


@pytest.mark.parametrize(
    ("with_root_webhook", "with_role"), [(False, False), (True, False), (True, True)],
    ids=["no-trigger", "root-webhook-subscription", "root-webhook-with-role"],
)
@pytest.mark.asyncio
async def test_meraki_public_recipe_candidate_preserves_legacy_rows_over_http(
    e2e_client, platform_admin, async_engine, with_root_webhook, with_role,
):
    """Stage the exact 4-file/3-workflow public DTO; never activate or execute it."""
    baseline = json.loads((_FIXTURE_ROOT / "public_baseline.json").read_text())
    source_commit = baseline["source_commit_sha"]
    recipe_data = baseline["reviewed_recipe"]
    legacy_workflows = baseline["workflow_baseline"]
    table_public = baseline["table"]

    sid, did = uuid4(), uuid4()
    role_id = uuid4() if with_role else None
    workflow_id_map = {UUID(item["id"]): uuid4() for item in recipe_data["workflows"]}
    recipe_data["solution_id"] = str(sid)
    for item in recipe_data["workflows"]:
        old_id = UUID(item["id"])
        item["id"] = str(workflow_id_map[old_id])
        item["organization_id"] = None
        if role_id is not None:
            item["controls"]["role_ids"] = [str(role_id)]
    recipe = ReviewedWorkflowRecipe.model_validate(recipe_data)

    files = {
        path: (_FIXTURE_ROOT / path).read_bytes()
        for path in recipe.files
    }
    body_files = [
        {"path": path, "content_base64": base64.b64encode(content).decode("ascii")}
        for path, content in sorted(files.items())
    ]

    created = e2e_client.post("/api/solutions", headers=platform_admin.headers, json={
        "slug": f"meraki-adoption-shape-{sid.hex[:12]}",
        "name": "Meraki Report Customer Routing fixture",
        "organization_id": None,
    })
    assert created.status_code == 201, created.text
    sid = UUID(created.json()["id"])
    # Bind the reviewed recipe to the actual isolated Solution just created.
    recipe_data["solution_id"] = str(sid)
    recipe = ReviewedWorkflowRecipe.model_validate(recipe_data)
    recipe_payload = recipe.model_dump(mode="json")
    for wf in recipe_payload["workflows"]:
        wf["organization_id"] = None

    workflow_ids = {item.id for item in recipe.workflows}
    legacy = SolutionStorage(sid)
    candidate = SolutionDeploymentStorage(sid, did)
    root_source_id = uuid4() if with_root_webhook else None
    root_subscription_id = uuid4() if with_root_webhook else None
    webhook_id = uuid4() if with_root_webhook else None
    table_id = uuid4()

    try:
        async with AsyncSession(async_engine, expire_on_commit=False) as db:
            solution = await db.get(Solution, sid)
            assert solution is not None
            solution.setup_complete = True
            solution.status = "active"
            solution.execution_runtime_mode = "repo-v1"
            solution.repo_subpath = "solutions/meraki-report-routing"
            solution.allow_outbound_access = False
            solution.git_connected = False
            solution.allow_inbound_access = True
            assert solution.organization_id is None

            if role_id is not None:
                db.add(Role(id=role_id, name=f"test-adoption-{sid.hex[:12]}",
                    created_by=str(platform_admin.user_id)))

            # The retained public Table DTO had a null schema and this policy.
            table = Table(
                id=table_id,
                name=table_public["name"],
                solution_id=sid,
                organization_id=None,
                schema=table_public.get("schema"),
                access=table_public.get("policies"),
                description=table_public.get("description"),
                created_by=table_public.get("created_by"),
            )
            db.add(table)
            await db.flush()

            # Check the public compiler without using it to manufacture the
            # legacy registrations: the observed legacy parameter lists are
            # empty and descriptors differ from the authored declarations.
            compile_workflow_registrations(recipe, files, WorkflowIndexer(db))
            old_by_id = {UUID(row["id"]): row for row in legacy_workflows}
            for old_id, new_id in workflow_id_map.items():
                public = old_by_id[old_id]
                # Preserve public installed registration/control values while
                # keeping the authored recipe's runtime bounds unchanged.
                values = {key: public[key] for key in (
                    "name", "function_name", "type", "organization_id",
                    "execution_mode", "timeout_seconds", "cache_ttl_seconds",
                    "time_saved", "value", "display_name", "description",
                    "category", "tags", "access_level", "endpoint_enabled",
                    "public_endpoint", "allowed_methods", "disable_global_key",
                    "retry_policy", "tool_description",
                )}
                db.add(Workflow(
                    id=new_id, solution_id=sid, path=public["source_file_path"],
                    parameters_schema=public["parameters"], **values,
                ))
                if role_id is not None:
                    db.add(WorkflowRole(workflow_id=new_id, role_id=role_id,
                        assigned_by=str(platform_admin.user_id)))

            if with_root_webhook:
                # Root-owned read-only trigger metadata targeting an installed
                # workflow; empty config/state means no vendor credential or call.
                event_source = EventSource(
                    id=root_source_id,
                    name=f"test-root-webhook-{sid.hex[:8]}",
                    source_type=EventSourceType.WEBHOOK,
                    organization_id=None,
                    solution_id=None,
                    created_by=str(platform_admin.user_id),
                )
                db.add(event_source)
                db.add(WebhookSource(
                    id=webhook_id,
                    event_source_id=root_source_id,
                    adapter_name=None,
                    config={},
                    state={},
                ))
                db.add(EventSubscription(
                    id=root_subscription_id,
                    event_source_id=root_source_id,
                    workflow_id=next(iter(workflow_ids)),
                    target_type="workflow",
                    solution_id=None,
                    created_by=str(platform_admin.user_id),
                ))
            await db.commit()

            rows = list((await db.scalars(
                select(Workflow).where(Workflow.solution_id == sid).order_by(Workflow.id)
            )).all())
            trigger_rows = []
            if with_root_webhook:
                trigger_rows = [
                    await db.get(EventSource, root_source_id),
                    await db.get(WebhookSource, webhook_id),
                    await db.get(EventSubscription, root_subscription_id),
                ]
            tracked = [*rows, table, *(x for x in trigger_rows if x is not None)]
            if role_id is not None:
                tracked.extend((await db.scalars(select(WorkflowRole).where(
                    WorkflowRole.role_id == role_id).order_by(WorkflowRole.workflow_id))).all())
                tracked.append(await db.get(Role, role_id))
            for row in tracked:
                await db.refresh(row)
            before = {_row_key(row): _digest_row(row) for row in tracked}

        # Ordinary prior baseline only: the production artifact does not include
        # Meraki's former object-store bytes. Keep the public source as the
        # synthesized legacy tree and send the same exact four current blobs.
        for path, content in files.items():
            await legacy.write(path, content)

        payload = {
            "source_commit_sha": source_commit,
            "reviewed_recipe": recipe_payload,
            "files": body_files,
            "resources": [],
        }
        route = f"/api/solutions/{sid}/deployments/{did}/repo-workflow-adoption/candidate"
        staged = e2e_client.post(route, headers=platform_admin.headers, json=payload)
        assert staged.status_code == 200, staged.text
        assert staged.json()["state"] == "ready"
        assert set(staged.json()["source_hashes"]) == set(files)

        async with AsyncSession(async_engine, expire_on_commit=False) as db:
            solution = await db.get(Solution, sid, populate_existing=True)
            candidate_row = await db.get(SolutionDeployment, did, populate_existing=True)
            assert solution is not None and solution.active_deployment_id is None
            assert solution.execution_runtime_mode == "repo-v1"
            assert candidate_row is not None and candidate_row.state == "ready"
            rows = list((await db.scalars(
                select(Workflow).where(Workflow.solution_id == sid).order_by(Workflow.id)
            )).all())
            table_after = await db.get(Table, table_id, populate_existing=True)
            trigger_after = []
            if with_root_webhook:
                trigger_after = [
                    await db.get(EventSource, root_source_id, populate_existing=True),
                    await db.get(WebhookSource, webhook_id, populate_existing=True),
                    await db.get(EventSubscription, root_subscription_id, populate_existing=True),
                ]
            tracked_after = [*rows, table_after, *(x for x in trigger_after if x is not None)]
            if role_id is not None:
                tracked_after.extend((await db.scalars(select(WorkflowRole).where(
                    WorkflowRole.role_id == role_id).order_by(WorkflowRole.workflow_id))).all())
                tracked_after.append(await db.get(Role, role_id))
            after = {_row_key(row): _digest_row(row) for row in tracked_after}
            assert after == before

    finally:
        for path in files:
            await legacy.delete(path)
        async with candidate._client_factory() as client:
            keys = [
                candidate.source_artifact_key,
                candidate.resources_artifact_key,
                deployment_manifest_key(sid, did),
                *(f"{candidate.runtime_prefix}{path}" for path in files),
            ]
            for key in keys:
                await client.delete_object(Bucket=candidate._bucket, Key=key)
        # Immutable deployment history forbids deleting the candidate or its
        # owner. Retain this fixture's UUID-isolated database rows; canonical
        # test.sh stack teardown destroys the disposable database and volumes.
