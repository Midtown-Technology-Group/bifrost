"""Native adoption retains legacy callers across HTTP, SQL, storage and workers."""

import base64
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.models.orm.workflows import Workflow
from src.services.solutions.deployment_storage import SolutionDeploymentStorage, deployment_manifest_key
from src.services.solutions.repo_workflow_adoption import _digest_row
from src.services.solutions.storage import SolutionStorage
from tests.e2e.conftest import execute_workflow_sync

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_native_optional_approval_preserves_legacy_rows_and_runs_owned_table_over_http(
    e2e_client, platform_admin, async_engine,
):
    headers = platform_admin.headers
    token = uuid4().hex[:12]
    path, slug = "functions/adopt.py", f"adoption-http-{token}"
    wid, did, tid = uuid4(), uuid4(), uuid4()
    table_name = f"adoption_http_{token}"
    legacy = (
        "from bifrost import workflow, tables\n"
        "DEFAULT_LIMIT = 7\n"
        "@workflow(name='Native HTTP task', effects=[{'kind':'bifrost.read'}], "
        "enforced_bounds={'max_duration_seconds':60,'max_external_calls':10,"
        "'max_records_read':100,'max_output_bytes':4096})\n"
        "async def run(limit: int = DEFAULT_LIMIT):\n"
        f"    rows = await tables.query('{table_name}', limit=limit)\n"
        "    return {'limit':limit,'count':rows.total}\n"
    ).encode()
    source = legacy.replace(b"limit: int = DEFAULT_LIMIT", b"limit: int = DEFAULT_LIMIT, *, approved_id: int | None = None")
    source = source.replace(b"'count':rows.total", b"'count':rows.total,'approved_id':approved_id")
    response = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": "Native HTTP adoption", "organization_id": None,
    })
    assert response.status_code == 201, response.text
    sid = UUID(response.json()["id"])
    legacy_storage = SolutionStorage(sid)
    storage = SolutionDeploymentStorage(sid, did)
    try:
        # Fixture setup uses the isolated CI database and real Solution storage.
        # Commit separately so the running API and worker see the legacy install.
        async with AsyncSession(async_engine, expire_on_commit=False) as db:
            solution = await db.get(Solution, sid)
            assert solution is not None
            solution.setup_complete = True
            solution.allow_outbound_access = False
            solution.git_connected = False
            row = Workflow(id=wid, solution_id=sid, organization_id=None,
                name="Native HTTP task", function_name="run", path=path,
                parameters_schema=[{"name":"limit","type":"integer","required":False,"default_value":7}],
                description=None, category="General")
            table = Table(id=tid, name=table_name, solution_id=sid, organization_id=None)
            db.add_all([row, table])
            await db.commit()
            await db.refresh(row)
            await db.refresh(table)
            before = {"workflow": _digest_row(row), "table": _digest_row(table)}
        await legacy_storage.write(path, legacy)
        recipe = {"schema_version":"bifrost.solution-workflow-delivery/v1", "solution_id":str(sid),
            "files":{path:"solutions/adopt.py"}, "workflows":[{
                "id":str(wid),"path":path,"function_name":"run","organization_id":None,"controls":{},
                "runtime_bounds":{"max_duration_seconds":60,"max_external_calls":10,
                    "max_records_read":100,"max_output_bytes":4096},
            }]}
        route = f"/api/solutions/{sid}/deployments/{did}/repo-workflow-adoption"
        staged = e2e_client.post(route + "/candidate", headers=headers, json={
            "source_commit_sha":"c" * 40, "reviewed_recipe":recipe,
            "files":[{"path":path,"content_base64":base64.b64encode(source).decode()}],
        })
        assert staged.status_code == 200, staged.text
        checked = e2e_client.post(route + "/preflight", headers=headers, json={"reviewed_recipe":recipe})
        assert checked.status_code == 200, checked.text
        assert checked.json() == staged.json()
        assert checked.json()["accepted_execution_ids"] == []
        assert await storage.read_runtime_file(path) == source
        assert await legacy_storage.read(path) == legacy
        activated = e2e_client.post(route + "/activate", headers=headers, json={
            "reviewed_recipe":recipe,"expected_evidence_id":checked.json()["evidence_id"],
        })
        assert activated.status_code == 200, activated.text
        assert activated.json()["state"] == "active"
        async with AsyncSession(async_engine) as db:
            row, table = await db.get(Workflow, wid), await db.get(Table, tid)
            assert row is not None and table is not None
            assert {"workflow": _digest_row(row), "table": _digest_row(table)} == before
            assert await db.scalar(select(Execution.id).where(Execution.solution_deployment_id == did)) is None
        result = execute_workflow_sync(e2e_client, headers, str(wid), request_sync=True, max_wait=60)
        assert result["status"] == "Success", result
        assert result["result"] == {"limit":7,"count":0,"approved_id":None}
        async with AsyncSession(async_engine) as db:
            execution = await db.get(Execution, UUID(result["execution_id"]))
            assert execution is not None and execution.solution_deployment_id == did
            assert execution.runtime_mode == "deployment-v1" and execution.runtime_evidence is not None
            schema = execution.runtime_evidence["workflow_parameters_schema"]
            assert schema["properties"]["limit"]["default"] == 7
            assert schema["properties"]["approved_id"]["default"] is None
            attempt = await db.scalar(select(WorkflowExecutionAttempt).where(
                WorkflowExecutionAttempt.execution_id == execution.id))
            assert attempt is not None and attempt.worker_id and attempt.status == "succeeded"
            assert attempt.runtime_evidence_hash == execution.runtime_evidence_hash
    finally:
        # Delete only this test's bytes; immutable history stays in the isolated
        # test database until the Docker stack's normal reset.
        await legacy_storage.delete(path)
        async with storage._client_factory() as client:
            for key in (storage.source_artifact_key, storage.resources_artifact_key,
                        deployment_manifest_key(sid, did), f"{storage.runtime_prefix}{path}"):
                await client.delete_object(Bucket=storage._bucket, Key=key)
