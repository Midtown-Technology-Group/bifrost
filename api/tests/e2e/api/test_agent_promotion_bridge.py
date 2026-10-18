"""Real HTTP promotion honors bridge edits with the same signed-in token."""

from uuid import uuid4

import pytest

from tests.e2e.fixtures.run_as import create_person, ok

pytestmark = pytest.mark.e2e


def _set_grants(client, admin, role_id, permissions):
    path = f"/api/roles/{role_id}/bridge-grants"
    current = ok(client.get(path, headers=admin))
    return ok(client.put(path, headers=admin, json={
        "permissions": permissions, "expected_permissions": current["permissions"],
        "expected_version": current["version"],
    }))


def test_owner_home_grant_and_immediate_revocation(e2e_client, platform_admin):
    tag, admin = uuid4().hex[:8], platform_admin.headers
    org = ok(e2e_client.post("/api/organizations", headers=admin,
                             json={"name": f"Promotion bridge {tag}"}), 201)
    role = ok(e2e_client.post("/api/roles", headers=admin,
                              json={"name": f"Promoters {tag}"}), 201)
    _set_grants(e2e_client, admin, role["id"], ["agents.promote"])
    person = create_person(e2e_client, admin, org, tag, "promotion", additional=[{
        "role_id": role["id"],
        "boundaries": [{"kind": "organization", "organization_id": org["id"]}],
    }])
    agent_ids = []
    try:
        def create_private(label):
            agent = ok(e2e_client.post("/api/agents", headers=person.headers, json={
                "name": f"Private {label} {tag}", "system_prompt": "Reply briefly.",
                "channels": ["chat"], "access_level": "private",
            }), 201)
            agent_ids.append(agent["id"])
            return agent

        permitted = create_private("permitted")
        promoted = ok(e2e_client.post(f"/api/agents/{permitted['id']}/promote", headers=person.headers,
                                       json={"access_level": "authenticated"}))
        assert promoted["access_level"] == "authenticated"
        assert promoted["owner_user_id"] is None

        revoked = create_private("revoked")
        _set_grants(e2e_client, admin, role["id"], [])
        refused = e2e_client.post(f"/api/agents/{revoked['id']}/promote", headers=person.headers,
                                  json={"access_level": "authenticated"})
        assert refused.status_code == 403, refused.text
        unchanged = ok(e2e_client.get(f"/api/agents/{revoked['id']}", headers=person.headers))
        assert unchanged["access_level"] == "private"
        assert unchanged["owner_user_id"] == str(person.user_id)
        # The Admin wildcard retains its ownership exception.
        promoted = ok(e2e_client.post(f"/api/agents/{revoked['id']}/promote", headers=admin,
                                       json={"access_level": "authenticated"}))
        assert promoted["access_level"] == "authenticated"
    finally:
        for agent_id in agent_ids:
            response = e2e_client.delete(f"/api/agents/{agent_id}", headers=admin)
            assert response.status_code == 204, response.text
        e2e_client.delete(f"/api/users/{person.user_id}", headers=admin)
        e2e_client.delete(f"/api/roles/{role['id']}", headers=admin)
        e2e_client.delete(f"/api/organizations/{org['id']}", headers=admin)
