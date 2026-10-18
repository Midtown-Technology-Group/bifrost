"""Retained namespace sharing remains reachable through the real HTTP API."""

from uuid import uuid4

import pytest

from tests.e2e.fixtures.run_as import create_person, ok

pytestmark = pytest.mark.e2e


def test_namespace_crud_selection_counts_and_revocation(e2e_client, platform_admin):
    admin = platform_admin.headers
    tag = uuid4().hex[:8]
    org = ok(e2e_client.post("/api/organizations", headers=admin,
                             json={"name": f"Namespace bridge {tag}"}), 201)
    role = ok(e2e_client.post("/api/roles", headers=admin,
                              json={"name": f"Namespace sharing {tag}"}), 201)
    person = create_person(e2e_client, admin, org, tag, "namespace", additional=[{
        "role_id": role["id"],
        "boundaries": [{"kind": "organization", "organization_id": org["id"]}],
    }])
    assignment_ids = []
    try:
        namespace = f"namespace-bridge-{tag}"
        assigned = ok(e2e_client.post("/api/knowledge-sources/roles", headers=admin, json={
            "namespace": namespace, "role_ids": [role["id"]], "organization_id": None,
        }), 201)
        assert len(assigned) == 1
        assignment_ids.append(assigned[0]["id"])
        assert assigned[0]["organization_id"] is None
        # This is a selector row: no content or knowledge.read grant is created.
        accessible = ok(e2e_client.get("/api/agents/accessible-knowledge", headers=person.headers))
        assert namespace in {row["namespace"] for row in accessible}
        counts = ok(e2e_client.get(f"/api/roles/{role['id']}", headers=admin))
        assert counts["consumer_counts"]["knowledge"] == 1
        listed = ok(e2e_client.get(f"/api/roles/{role['id']}/knowledge", headers=admin))
        assert listed["entries"][0]["id"] == assignment_ids[0]
        refused = e2e_client.post("/api/knowledge-sources/roles", headers=person.headers,
                                  json={"namespace": namespace, "role_ids": [role["id"]]})
        assert refused.status_code == 403
        ok(e2e_client.put(f"/api/users/{person.user_id}/role-assignments", headers=admin,
                           json={"base_role_id": "00000000-0000-0000-0000-000000000006", "additional": []}))
        accessible = ok(e2e_client.get("/api/agents/accessible-knowledge", headers=person.headers))
        assert namespace not in {row["namespace"] for row in accessible}
        # Revocation changes membership, never the namespace assignment identity.
        listed = ok(e2e_client.get(f"/api/roles/{role['id']}/knowledge", headers=admin))
        assert listed["entries"][0]["id"] == assignment_ids[0]
    finally:
        for assignment_id in assignment_ids:
            response = e2e_client.delete(f"/api/knowledge-sources/roles/{assignment_id}", headers=admin)
            assert response.status_code == 204, response.text
        e2e_client.delete(f"/api/users/{person.user_id}", headers=admin)
        e2e_client.delete(f"/api/roles/{role['id']}", headers=admin)
        e2e_client.delete(f"/api/organizations/{org['id']}", headers=admin)
