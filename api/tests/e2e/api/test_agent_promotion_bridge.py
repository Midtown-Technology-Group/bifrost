"""Real HTTP promotion honors bridge edits with the same signed-in token."""

from uuid import UUID, uuid4

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
        removed = e2e_client.delete(f"/api/users/{person.user_id}", headers=admin)
        assert removed.status_code == 204
        removed = e2e_client.delete(f"/api/roles/{role['id']}", headers=admin)
        assert removed.status_code == 204
        removed = e2e_client.delete(f"/api/organizations/{org['id']}", headers=admin)
        assert removed.status_code == 204


@pytest.mark.asyncio
async def test_http_promotion_owner_home_solution_and_permission_matrix(
    e2e_client, platform_admin, async_session_factory,
):
    """Seed bounded fixtures, then exercise every preserved gate over HTTP.

    The synthetic Solution and agents require no deploy/build/execution job.
    Core inserts represent already installed rows without mutating protected
    portable state through an ordinary runtime API.
    """
    from sqlalchemy import delete, insert, select

    from src.core.security import create_access_token
    from src.models.enums import AgentAccessLevel
    from src.models.orm.agents import Agent
    from src.models.orm.users import RolePermission

    tag, admin = uuid4().hex[:8], platform_admin.headers
    orgs = [ok(e2e_client.post("/api/organizations", headers=admin,
                               json={"name": f"Promotion {label} {tag}"}), 201)
            for label in ("home", "foreign")]
    home, foreign = orgs
    role = ok(e2e_client.post("/api/roles", headers=admin,
                              json={"name": f"Promotion matrix {tag}"}), 201)
    _set_grants(e2e_client, admin, role["id"], ["agents.promote"])
    person = create_person(e2e_client, admin, home, tag, "promotion-matrix", additional=[{
        "role_id": role["id"],
        "boundaries": [{"kind": "organization", "organization_id": home["id"]}],
    }])
    solution = ok(e2e_client.post("/api/solutions", headers=admin, json={
        "slug": f"promotion-matrix-{tag}", "name": f"Promotion matrix {tag}",
        "organization_id": home["id"], "allow_outbound_access": False,
    }), 201)
    cases = {
        "other-owner": {"owner_user_id": platform_admin.user_id},
        "foreign-org": {"organization_id": UUID(foreign["id"])},
        "solution": {"solution_id": UUID(solution["id"])},
        "nonprivate": {"access_level": AgentAccessLevel.AUTHENTICATED},
        "unrelated-permission": {},
    }
    agent_ids = {case: uuid4() for case in cases}
    try:
        async with async_session_factory() as db:
            for case, overrides in cases.items():
                await db.execute(insert(Agent).values({
                    "id": agent_ids[case], "name": f"Matrix {case} {tag}",
                    "system_prompt": "Synthetic promotion fixture.", "channels": ["chat"],
                    "access_level": AgentAccessLevel.PRIVATE, "organization_id": UUID(home["id"]),
                    "owner_user_id": person.user_id, "created_by": "promotion-matrix-test",
                    **overrides,
                }))
            await db.commit()

        def promote(case, headers):
            return e2e_client.post(f"/api/agents/{agent_ids[case]}/promote", headers=headers,
                                   json={"access_level": "authenticated"})

        for case, expected in [("other-owner", 403), ("foreign-org", 403),
                               ("solution", 409), ("nonprivate", 400)]:
            response = promote(case, person.headers)
            assert response.status_code == expected, f"{case}: {response.status_code}"
        # A Platform Admin still cannot mutate Solution-owned agent state.
        assert promote("solution", admin).status_code == 409
        # The Admin exception does cover a different owner's organization.
        assert promote("foreign-org", admin).status_code == 200

        _set_grants(e2e_client, admin, role["id"], [])
        for permission in ("agents.readwrite", "agents.readwrite.all"):
            async with async_session_factory() as db:
                await db.execute(delete(RolePermission).where(RolePermission.role_id == UUID(role["id"])))
                db.add(RolePermission(role_id=UUID(role["id"]), permission=permission))
                await db.commit()
            denied = promote("unrelated-permission", person.headers)
            assert denied.status_code == 403, f"{permission}: {denied.status_code}"
        # A stale signed admin claim is not a stored Platform Admin wildcard.
        stale = create_access_token({"sub": str(person.user_id), "email": person.email,
                                      "org_id": home["id"], "is_superuser": True})
        assert promote("unrelated-permission", {"Authorization": f"Bearer {stale}"}).status_code == 403

        async with async_session_factory() as db:
            rows = (await db.execute(select(Agent.id, Agent.access_level, Agent.owner_user_id)
                                      .where(Agent.id.in_(agent_ids.values())))).all()
            by_id = {row.id: row for row in rows}
            for case in ("other-owner", "solution", "unrelated-permission"):
                assert by_id[agent_ids[case]].access_level == AgentAccessLevel.PRIVATE
            assert by_id[agent_ids["unrelated-permission"]].owner_user_id == person.user_id
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(Agent).where(Agent.id.in_(agent_ids.values())))
            await db.commit()
        removed = e2e_client.delete(f"/api/solutions/{solution['id']}", headers=admin,
                                     params={"confirm": solution["slug"]})
        assert removed.status_code == 200
        removed = e2e_client.delete(f"/api/users/{person.user_id}", headers=admin)
        assert removed.status_code == 204
        removed = e2e_client.delete(f"/api/roles/{role['id']}", headers=admin)
        assert removed.status_code == 204
        for org in reversed(orgs):
            removed = e2e_client.delete(f"/api/organizations/{org['id']}", headers=admin)
            assert removed.status_code == 204
