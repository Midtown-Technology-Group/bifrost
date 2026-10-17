"""A native event must execute a strict immutable Solution workflow."""

import base64
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.executions import Execution
from src.models.orm.solutions import Solution
from src.services.solutions.deployment_storage import SolutionDeploymentStorage, deployment_manifest_key
from tests.e2e.conftest import poll_until


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_event_runs_strict_solution_workflow_over_http(
    e2e_client, platform_admin, async_engine,
):
    headers = platform_admin.headers
    token = uuid4().hex
    wid, did = uuid4(), uuid4()
    path = "functions/event.py"
    source = (
        "from bifrost import workflow\n"
        "@workflow(name='Strict event fixture', effects=[])\n"
        "async def run(payload: dict, apply: bool):\n"
        "    return {'ticket_id': payload['ticket_id'], 'apply': apply}\n"
    ).encode()
    response = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": f"event-admission-{token[:12]}", "name": "Event admission fixture",
        "organization_id": None,
    })
    assert response.status_code == 201, response.text
    sid = UUID(response.json()["id"])
    storage = SolutionDeploymentStorage(sid, did)
    source_id = None
    try:
        # Commit isolated fixture readiness so the real API can install it.
        async with AsyncSession(async_engine) as db:
            solution = await db.get(Solution, sid)
            assert solution is not None
            solution.setup_complete = True
            solution.allow_outbound_access = False
            solution.git_connected = False
            await db.commit()
        recipe = {
            "schema_version": "bifrost.solution-workflow-delivery/v1",
            "solution_id": str(sid), "files": {path: "solutions/event.py"},
            "workflows": [{"id": str(wid), "path": path, "function_name": "run",
                "organization_id": None, "controls": {},
                "runtime_bounds": {"max_duration_seconds": 60, "max_external_calls": 10,
                    "max_records_read": 100, "max_output_bytes": 4096}}],
        }
        route = f"/api/solutions/{sid}/deployments/{did}/initial-workflow"
        staged = e2e_client.post(route + "/candidate", headers=headers, json={
            "source_commit_sha": "a" * 40, "reviewed_recipe": recipe,
            "files": [{"path": path, "content_base64": base64.b64encode(source).decode()}],
        })
        assert staged.status_code == 200, staged.text
        checked = e2e_client.post(route + "/preflight", headers=headers,
            json={"reviewed_recipe": recipe})
        assert checked.status_code == 200, checked.text
        activated = e2e_client.post(route + "/activate", headers=headers, json={
            "reviewed_recipe": recipe, "expected_evidence_id": checked.json()["evidence_id"],
        })
        assert activated.status_code == 200, activated.text
        assert activated.json()["state"] == "active"

        topic = f"test.solution_event.{token}"
        created = e2e_client.post("/api/events/sources", headers=headers, json={
            "name": topic, "source_type": "topic", "event_type": topic,
            "organization_id": None,
        })
        assert created.status_code == 201, created.text
        source_id = created.json()["id"]
        subscribed = e2e_client.post(f"/api/events/sources/{source_id}/subscriptions",
            headers=headers, json={"workflow_id": str(wid), "event_type": topic,
                "input_mapping": {"payload": "{{ payload }}", "apply": True}})
        assert subscribed.status_code == 201, subscribed.text
        emitted = e2e_client.post("/api/events/emit", headers=headers,
            json={"topic": topic, "data": {"ticket_id": 42}})
        assert emitted.status_code == 200, emitted.text
        event_id = emitted.json()["event_id"]

        def terminal_delivery():
            result = e2e_client.get(f"/api/events/{event_id}/deliveries", headers=headers)
            assert result.status_code == 200, result.text
            items = result.json()["items"]
            assert len(items) == 1
            return items[0] if items[0]["status"] in {"success", "failed"} else None

        delivery = poll_until(terminal_delivery, max_wait=60)
        assert delivery is not None
        assert delivery["status"] == "success", delivery
        async with AsyncSession(async_engine) as db:
            execution = await db.get(Execution, UUID(delivery["execution_id"]))
            assert execution is not None
            assert execution.solution_deployment_id == did
            assert execution.runtime_mode == "deployment-v1"
            assert execution.result == {"ticket_id": 42, "apply": True}
            assert execution.parameters["_event"]["id"] == event_id
            assert execution.parameters["_event"]["body"] == {"ticket_id": 42}
            schema = execution.runtime_evidence["workflow_parameters_schema"]
            assert schema["additionalProperties"] is False
            assert "_event" not in schema["properties"]
    finally:
        if source_id:
            deleted = e2e_client.delete(f"/api/events/sources/{source_id}", headers=headers)
            assert deleted.status_code == 204, deleted.text
        async with storage._client_factory() as client:
            for key in (storage.source_artifact_key, storage.resources_artifact_key,
                        deployment_manifest_key(sid, did), f"{storage.runtime_prefix}{path}"):
                await client.delete_object(Bucket=storage._bucket, Key=key)
