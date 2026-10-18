"""Signed engine/service credentials keep their distinct device HTTP lanes."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.core.security import mint_engine_token
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from tests.e2e.api.test_device_protocol import _enroll
from tests.e2e.fixtures.run_as import ok

pytestmark = pytest.mark.e2e


async def test_supported_engine_and_service_tokens_through_device_route_parser(
    e2e_client, platform_admin, org1, org1_service_headers, async_session_factory,
):
    execution_id, attempt_token = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    # This is the same real active-attempt setup used by test_auth's supported
    # mint_engine_token refresh regression; no auth dependency is replaced.
    async with async_session_factory() as db:
        db.add(Execution(id=execution_id, workflow_name="device-credential-test",
                         executed_by_name="test", status=ExecutionStatus.RUNNING))
        await db.flush()
        db.add(WorkflowExecutionAttempt(
            execution_id=execution_id, attempt_number=1, claim_token=attempt_token,
            status="running", phase="execution", published_at=now, claimed_at=now, started_at=now,
        ))
        await db.commit()
    try:
        token, _ = mint_engine_token(execution_id=str(execution_id), attempt_token=str(attempt_token),
                                      organization_id=org1["id"])
        engine = {"Authorization": f"Bearer {token}"}
        device_id, _ = _enroll(e2e_client, platform_admin.headers, org1["id"], "engine-target")
        # Existing execution transport authority is explicit; an ordinary
        # non-superuser service token receives none of these management powers.
        ok(e2e_client.get(f"/api/devices/{device_id}", headers=engine))
        registered = ok(e2e_client.post("/api/devices", headers=engine,
                                         json={"display_name": "engine-registry", "organization_id": org1["id"]}), 201)
        assert registered["device"]["organization_id"] == org1["id"]
        issued = ok(e2e_client.post("/api/device-control-keys", headers=engine, json={
            "name": "engine-key", "organization_id": org1["id"], "device_ids": [device_id],
        }), 201)
        key_id = issued["control_key"]["id"]
        ok(e2e_client.get(f"/api/device-control-keys/{key_id}", headers=engine))
        ok(e2e_client.post(f"/api/device-control-keys/{key_id}/rotate", headers=engine))
        job = ok(e2e_client.post(f"/api/devices/{device_id}/jobs", headers=engine,
                                  json={"script_name": "engine", "script_content": "Write-Output 'engine'"}), 201)
        ok(e2e_client.get(f"/api/devices/{device_id}/jobs/{job['id']}", headers=engine))
        service = org1_service_headers
        refused = [
            e2e_client.get(f"/api/devices/{device_id}", headers=service),
            e2e_client.post("/api/devices", headers=service,
                            json={"display_name": "service-denied", "organization_id": org1["id"]}),
            e2e_client.get(f"/api/device-control-keys/{key_id}", headers=service),
            e2e_client.post("/api/device-control-keys", headers=service,
                            json={"name": "service-denied", "device_ids": [device_id]}),
            e2e_client.post(f"/api/device-control-keys/{key_id}/rotate", headers=service),
            e2e_client.post(f"/api/device-control-keys/{key_id}/revoke", headers=service),
            e2e_client.post(f"/api/devices/{device_id}/jobs", headers=service,
                            json={"script_name": "service-denied", "script_content": "Write-Output 'denied'"}),
            e2e_client.get(f"/api/devices/{device_id}/jobs/{job['id']}", headers=service),
        ]
        for response in refused:
            assert response.status_code == 403, response.text
            assert response.json()["error"]["code"] == "permission_denied"
        cancelled = ok(e2e_client.post(f"/api/devices/{device_id}/jobs/{job['id']}/cancel", headers=engine))
        assert cancelled["status"] == "cancelled"
        revoked = ok(e2e_client.post(f"/api/device-control-keys/{key_id}/revoke", headers=engine))
        assert revoked["enabled"] is False
        async with async_session_factory() as db:
            execution = await db.get(Execution, execution_id)
            execution.status = ExecutionStatus.SUCCESS
            await db.commit()
        # The same signed engine token loses its lane when the execution ends.
        expired_lane = e2e_client.get(f"/api/devices/{device_id}", headers=engine)
        assert expired_lane.status_code == 401, expired_lane.text
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(Execution).where(Execution.id == execution_id))
            await db.commit()
