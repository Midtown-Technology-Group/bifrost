"""Persisted boundary changes cannot widen device bridge authority via stale JWTs."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, update

from shared.builtin_roles import USER_ROLE_ID

from src.models.orm.device_control_keys import DeviceControlKey
from src.models.orm.users import User, UserRoleBoundary
from src.services.authorization.context import build_authorization_context
from tests.e2e.api.test_device_control_keys import _assert_job_power_denied, _claim_and_log
from tests.e2e.api.test_device_protocol import _enroll
from tests.e2e.fixtures.device_bridge import set_bridge_grants
from tests.e2e.fixtures.run_as import create_person, ok

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("invalid_boundary", ["split_org", "missing", "platform", "managed_organizations", "null_home"])
async def test_persisted_boundary_change_refuses_same_session_execution_and_key_secrets(
    e2e_client, platform_admin, org1, org2, async_session_factory, invalid_boundary,
):
    admin, tag = platform_admin.headers, uuid4().hex[:8]
    roles = []
    for permission in ("devices.readwrite", "devices.execute"):
        role = ok(e2e_client.post("/api/roles", headers=admin,
                                  json={"name": f"boundary-{tag}-{permission}"}), 201)
        roles.append(role["id"])
        set_bridge_grants(e2e_client, admin, role["id"], [permission])
    person = create_person(e2e_client, admin, org1, tag, "boundary", additional=[{
        "role_id": role_id,
        "boundaries": [{"kind": "organization", "organization_id": org1["id"]}],
    } for role_id in roles])
    try:
        headers = dict(person.headers)
        device_id, device_key = _enroll(e2e_client, admin, org1["id"], f"boundary-{tag}")
        job = ok(e2e_client.post(f"/api/devices/{device_id}/jobs", headers=headers,
                                  json={"script_name": "boundary", "script_content": "Write-Output 'private-boundary'"}), 201)
        job_id = job["id"]
        _claim_and_log(e2e_client, device_key, job_id, "private-boundary-log")
        issued = ok(e2e_client.post("/api/device-control-keys", headers=headers,
                                     json={"name": "before-boundary-change", "device_ids": [device_id]}), 201)
        key_id = UUID(issued["control_key"]["id"])
        async with async_session_factory() as db:
            original_hash = await db.scalar(select(DeviceControlKey.key_hash).where(DeviceControlKey.id == key_id))
            execution_assignment = (UserRoleBoundary.user_id == person.user_id) & (UserRoleBoundary.role_id == UUID(roles[1]))
            # The public assignment writer correctly refuses these states. Seed
            # them only in this isolated test DB to prove runtime defense against
            # stale/invalid stored boundaries without invalidating the old session.
            if invalid_boundary == "missing":
                await db.execute(delete(UserRoleBoundary).where(execution_assignment))
            elif invalid_boundary == "null_home":
                # Retained ck_users_org_requires_superuser permits NULL home for
                # a legacy superuser flag or typed identity only. Set that inert
                # physical flag in this synthetic fixture, preserving the User
                # base role and no wildcard: current RBAC must still deny the
                # same pre-change ordinary JWT instead of inventing platform reach.
                await db.execute(update(User).where(User.id == person.user_id).values(
                    organization_id=None, is_superuser=True,
                ))
                current = await build_authorization_context(db, person.user_id)
                assert current.base_role_id == USER_ROLE_ID
                assert current.home_organization_id is None
                assert not current.is_platform_admin
                assert "*" not in current.held_permissions
                assert await db.scalar(select(User.is_superuser).where(User.id == person.user_id)) is True
            else:
                await db.execute(update(UserRoleBoundary).where(execution_assignment).values(
                    kind="organization" if invalid_boundary == "split_org" else invalid_boundary,
                    organization_id=UUID(org2["id"]) if invalid_boundary == "split_org" else None,
                ))
            await db.commit()
        _assert_job_power_denied(e2e_client, headers, device_id, job_id)
        refused = e2e_client.post("/api/device-control-keys", headers=headers,
                                   json={"name": "after-boundary-change", "device_ids": [device_id]})
        assert refused.status_code == 403, refused.text
        rotated = e2e_client.post(f"/api/device-control-keys/{key_id}/rotate", headers=headers)
        assert rotated.status_code == 403, rotated.text
        cross_issue = e2e_client.post("/api/device-control-keys", headers=headers,
                                       json={"name": "foreign", "organization_id": org2["id"], "device_ids": [device_id]})
        assert cross_issue.status_code == 403, cross_issue.text
        metadata = e2e_client.get(f"/api/devices/{device_id}", headers=headers)
        assert metadata.status_code == (403 if invalid_boundary == "null_home" else 200), metadata.text
        async with async_session_factory() as db:
            unchanged = await db.get(DeviceControlKey, key_id)
            assert unchanged.key_hash == original_hash
            assert unchanged.enabled is True
        # The current Admin wildcard keeps its independent exception at the same
        # actual target even when the ordinary holder's persisted state is bad.
        detail = ok(e2e_client.get(f"/api/devices/{device_id}/jobs/{job_id}", headers=admin))
        assert detail["script_content"] == "Write-Output 'private-boundary'"
        logs = ok(e2e_client.get(f"/api/devices/{device_id}/jobs/{job_id}/logs", headers=admin))
        assert [entry["text"] for entry in logs] == ["private-boundary-log"]
        ok(e2e_client.post(f"/api/device-control-keys/{key_id}/rotate", headers=admin))
        cancelled = ok(e2e_client.post(f"/api/devices/{device_id}/jobs/{job_id}/cancel", headers=admin))
        assert cancelled["status"] == "cancelled"
    finally:
        removed_user = e2e_client.delete(f"/api/users/{person.user_id}", headers=admin)
        assert removed_user.status_code == 204, removed_user.text
        for role_id in roles:
            removed_role = e2e_client.delete(f"/api/roles/{role_id}", headers=admin)
            assert removed_role.status_code == 204, removed_role.text


def test_provider_membership_alone_is_not_a_device_grant(
    e2e_client, platform_admin, org1, provider_org_user,
):
    admin = platform_admin.headers
    device_id, _ = _enroll(e2e_client, admin, org1["id"], "provider-only-device")
    job = ok(e2e_client.post(f"/api/devices/{device_id}/jobs", headers=admin,
                              json={"script_name": "provider", "script_content": "Write-Output 'private'"}), 201)
    _assert_job_power_denied(e2e_client, provider_org_user.headers, device_id, job["id"])
    registry = e2e_client.get(f"/api/devices/{device_id}", headers=provider_org_user.headers)
    assert registry.status_code == 403, registry.text
    issue = e2e_client.post("/api/device-control-keys", headers=provider_org_user.headers,
                             json={"name": "provider-denied", "organization_id": org1["id"], "device_ids": [device_id]})
    assert issue.status_code == 403, issue.text
    cancelled = ok(e2e_client.post(f"/api/devices/{device_id}/jobs/{job['id']}/cancel", headers=admin))
    assert cancelled["status"] == "cancelled"
