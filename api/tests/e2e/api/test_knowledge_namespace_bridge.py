"""Retained namespace sharing remains reachable through the real HTTP API."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

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
        removed = e2e_client.delete(f"/api/users/{person.user_id}", headers=admin)
        assert removed.status_code == 204
        removed = e2e_client.delete(f"/api/roles/{role['id']}", headers=admin)
        assert removed.status_code == 204
        removed = e2e_client.delete(f"/api/organizations/{org['id']}", headers=admin)
        assert removed.status_code == 204


@pytest.mark.asyncio
async def test_junction_edits_preserve_person_and_engine_content_scope(
    e2e_client, platform_admin, async_session_factory,
):
    """Persist content without embeddings, then change only namespace sharing.

    The junction controls agent namespace selection, not direct content access.
    A person's direct UUID reads allow home/global content, while the SDK key
    lookup uses the exact resolved organization. Those policies and the
    execution credential's explicit scope capability remain unchanged.
    """
    from sqlalchemy import delete

    from src.core.security import mint_engine_token
    from src.models.enums import ExecutionStatus
    from src.models.orm.executions import Execution, WorkflowExecutionAttempt
    from src.models.orm.knowledge import KnowledgeStore

    admin, tag = platform_admin.headers, uuid4().hex[:8]
    orgs = [ok(e2e_client.post("/api/organizations", headers=admin,
                               json={"name": f"Content {label} {tag}"}), 201)
            for label in ("home", "foreign")]
    home, foreign = orgs
    role = ok(e2e_client.post("/api/roles", headers=admin,
                              json={"name": f"Content selector {tag}"}), 201)
    person = create_person(e2e_client, admin, home, tag, "content", additional=[{
        "role_id": role["id"],
        "boundaries": [{"kind": "organization", "organization_id": home["id"]}],
    }])
    namespace = f"content-junction-{tag}"
    docs = {label: {"id": uuid4(), "content": f"synthetic-{label}-{tag}", "org": org_id}
            for label, org_id in [("home", UUID(home["id"])), ("global", None),
                                  ("foreign", UUID(foreign["id"]))]}
    execution_id, attempt_token = uuid4(), uuid4()
    assignment_id = None
    try:
        async with async_session_factory() as db:
            db.add_all([
                KnowledgeStore(id=doc["id"], namespace=namespace, key=label,
                               organization_id=doc["org"], content=doc["content"],
                               doc_metadata={}, embedding=[0.1, 0.2, 0.3],
                               chunk_index=0, chunk_count=1)
                for label, doc in docs.items()
            ])
            db.add(Execution(id=execution_id, workflow_name=f"content-scope-{tag}",
                             executed_by_name="Content scope test", status=ExecutionStatus.RUNNING,
                             organization_id=UUID(home["id"])))
            await db.flush()
            now = datetime.now(timezone.utc)
            db.add(WorkflowExecutionAttempt(execution_id=execution_id, attempt_number=1,
                                            claim_token=attempt_token, status="running", phase="execution",
                                            published_at=now, claimed_at=now, started_at=now))
            await db.commit()
        token, _ = mint_engine_token(execution_id=str(execution_id), attempt_token=str(attempt_token),
                                     organization_id=home["id"])
        engine_headers = {"Authorization": f"Bearer {token}"}

        def assert_content_scope():
            for label, doc in docs.items():
                direct_expected = 404 if label == "foreign" else 200
                sdk_expected = 200 if label == "home" else 404
                direct = e2e_client.get(
                    f"/api/knowledge-sources/{namespace}/documents/{doc['id']}", headers=person.headers,
                )
                assert direct.status_code == direct_expected, f"person direct {label}: {direct.status_code}"
                sdk = e2e_client.get("/api/sdk/knowledge/get", headers=person.headers,
                                     params={"namespace": namespace, "key": label})
                assert sdk.status_code == sdk_expected, f"person SDK {label}: {sdk.status_code}"
                if direct_expected == 200:
                    assert direct.json()["content"] == doc["content"]
                if sdk_expected == 200:
                    assert sdk.json()["content"] == doc["content"]
            # A namespace grant never makes an explicit foreign scope legal.
            refused = e2e_client.get("/api/sdk/knowledge/get", headers=person.headers,
                                     params={"namespace": namespace, "key": "foreign", "scope": foreign["id"]})
            assert refused.status_code == 403
            refused_global = e2e_client.get("/api/sdk/knowledge/get", headers=person.headers,
                                            params={"namespace": namespace, "key": "global", "scope": "global"})
            assert refused_global.status_code == 403
            for label, scope in [("home", home["id"]), ("global", "global"), ("foreign", foreign["id"])]:
                sdk = e2e_client.get("/api/sdk/knowledge/get", headers=engine_headers,
                                     params={"namespace": namespace, "key": label, "scope": scope})
                assert sdk.status_code == 200, f"engine SDK {label}: {sdk.status_code}"
                assert sdk.json()["content"] == docs[label]["content"]
            direct = e2e_client.get(
                f"/api/knowledge-sources/{namespace}/documents/{docs['foreign']['id']}", headers=engine_headers,
            )
            assert direct.status_code == 200
            assert direct.json()["content"] == docs["foreign"]["content"]

        def assert_selected(expected):
            selected = ok(e2e_client.get("/api/agents/accessible-knowledge", headers=person.headers))
            assert (namespace in {entry["namespace"] for entry in selected}) is expected

        assert_selected(False)
        assert_content_scope()
        assigned = ok(e2e_client.post("/api/knowledge-sources/roles", headers=admin, json={
            "namespace": namespace, "role_ids": [role["id"]], "organization_id": home["id"],
        }), 201)
        assignment_id = assigned[0]["id"]
        assert_selected(True)
        assert_content_scope()
        removed = e2e_client.delete(f"/api/knowledge-sources/roles/{assignment_id}", headers=admin)
        assert removed.status_code == 204
        assignment_id = None
        assert_selected(False)
        assert_content_scope()
    finally:
        if assignment_id is not None:
            removed = e2e_client.delete(f"/api/knowledge-sources/roles/{assignment_id}", headers=admin)
            assert removed.status_code == 204
        async with async_session_factory() as db:
            await db.execute(delete(Execution).where(Execution.id == execution_id))
            await db.execute(delete(KnowledgeStore).where(KnowledgeStore.namespace == namespace))
            await db.commit()
        removed = e2e_client.delete(f"/api/users/{person.user_id}", headers=admin)
        assert removed.status_code == 204
        removed = e2e_client.delete(f"/api/roles/{role['id']}", headers=admin)
        assert removed.status_code == 204
        for org in reversed(orgs):
            removed = e2e_client.delete(f"/api/organizations/{org['id']}", headers=admin)
            assert removed.status_code == 204


@pytest.mark.asyncio
async def test_role_namespace_bulk_remove_isolation_and_role_delete_cascade(
    e2e_client, platform_admin, org1, async_session_factory,
):
    """Role-scoped deletion preserves other roles and retained global/home IDs."""
    from sqlalchemy import select

    from src.models.orm.knowledge_sources import KnowledgeNamespaceRole
    from src.models.orm.users import RolePermission

    admin, tag = platform_admin.headers, uuid4().hex[:8]
    namespace, extra_namespace = f"role-junction-{tag}", f"role-junction-extra-{tag}"
    role_ids = []

    def entries(role_id):
        return ok(e2e_client.get(f"/api/roles/{role_id}/knowledge", headers=admin))["entries"]

    def count(role_id):
        role = ok(e2e_client.get(f"/api/roles/{role_id}", headers=admin))
        assert role["grants"] == []
        return role["consumer_counts"]["knowledge"]

    async def persisted_rows():
        async with async_session_factory() as db:
            return set((await db.execute(select(
                KnowledgeNamespaceRole.id, KnowledgeNamespaceRole.role_id,
                KnowledgeNamespaceRole.namespace, KnowledgeNamespaceRole.organization_id,
            ).where(KnowledgeNamespaceRole.namespace.in_([namespace, extra_namespace])))).all())

    try:
        for label in ("remove", "retain"):
            role = ok(e2e_client.post("/api/roles", headers=admin,
                                      json={"name": f"Namespace {label} {tag}"}), 201)
            role_ids.append(role["id"])
        remove_role, retain_role = role_ids
        requested = [{"namespace": namespace, "organization_id": scope}
                     for scope in (None, org1["id"])]
        for role_id in role_ids:
            assigned = e2e_client.post(f"/api/roles/{role_id}/knowledge", headers=admin,
                                       json={"entries": requested})
            assert assigned.status_code == 204
            assert count(role_id) == 2
        original = {role_id: {entry["organization_id"]: entry["id"] for entry in entries(role_id)}
                    for role_id in role_ids}
        original_rows = await persisted_rows()
        assert len(original_rows) == 4

        removed = e2e_client.request("DELETE", f"/api/roles/{remove_role}/knowledge", headers=admin, json={
            "assignment_ids": [original[remove_role][org1["id"]], original[retain_role][None]],
        })
        assert removed.status_code == 204
        assert count(remove_role) == 1
        assert count(retain_role) == 2
        assert {entry["organization_id"]: entry["id"] for entry in entries(remove_role)} == {
            None: original[remove_role][None],
        }
        assert {entry["organization_id"]: entry["id"] for entry in entries(retain_role)} == original[retain_role]
        expected_rows = {row for row in original_rows if str(row.id) != original[remove_role][org1["id"]]}
        assert await persisted_rows() == expected_rows

        # Exercise namespace-level creation and role-level idempotent creation;
        # neither may replace retained assignment IDs or activate permissions.
        created = ok(e2e_client.post("/api/knowledge-sources/roles", headers=admin, json={
            "namespace": extra_namespace, "organization_id": org1["id"], "role_ids": [remove_role],
        }), 201)
        assert len(created) == 1
        extra_id = created[0]["id"]
        duplicate = e2e_client.post(f"/api/roles/{remove_role}/knowledge", headers=admin, json={
            "entries": [requested[0], {"namespace": extra_namespace, "organization_id": org1["id"]}],
        })
        assert duplicate.status_code == 204
        assert count(remove_role) == 2
        assert count(retain_role) == 2
        assert {entry["id"] for entry in entries(remove_role)} == {original[remove_role][None], extra_id}
        async with async_session_factory() as db:
            permissions = (await db.execute(select(RolePermission.permission).where(
                RolePermission.role_id.in_([UUID(remove_role), UUID(retain_role)]),
            ))).scalars().all()
            assert permissions == []

        # Deliberately leave the remaining assignments in place when deleting
        # the role: this must be a real FK cascade, not fixture pre-cleanup.
        deleted = e2e_client.delete(f"/api/roles/{remove_role}", headers=admin)
        assert deleted.status_code == 204
        role_ids.remove(remove_role)
        retained_rows = {row for row in original_rows if str(row.role_id) == retain_role}
        assert await persisted_rows() == retained_rows
        assert count(retain_role) == 2
        assert {entry["organization_id"]: entry["id"] for entry in entries(retain_role)} == original[retain_role]
        namespace_rows = ok(e2e_client.get("/api/knowledge-sources/roles", headers=admin))
        assert {row["id"] for row in namespace_rows if row["namespace"] in (namespace, extra_namespace)} == {
            original[retain_role][None], original[retain_role][org1["id"]],
        }
        deleted = e2e_client.delete(f"/api/roles/{retain_role}", headers=admin)
        assert deleted.status_code == 204
        role_ids.remove(retain_role)
        assert await persisted_rows() == set()
    finally:
        for role_id in role_ids:
            deleted = e2e_client.delete(f"/api/roles/{role_id}", headers=admin)
            assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_external_selector_uses_persisted_home_effective_base_and_additional_roles(
    e2e_client, platform_admin, org1, org2, async_session_factory,
):
    """External sharing is explicit home/global metadata, never foreign reach."""
    from sqlalchemy import delete, select, update

    from src.core.security import decode_token
    from src.models.orm.users import RolePermission, User, UserRole, UserRoleBoundary
    from src.services.authorization.context import build_authorization_context
    from tests.e2e.fixtures.setup import _register_and_authenticate_user
    from tests.e2e.fixtures.users import E2EUser

    admin, tag = platform_admin.headers, uuid4().hex[:8]
    role_ids = []
    person = E2EUser(email=f"external-selector-{tag}@example.com", password=f"External-{uuid4().hex}!",
                     name=f"External selector {tag}", organization_id=UUID(org1["id"]))
    created_user = False
    try:
        for label in ("base", "additional"):
            role = ok(e2e_client.post("/api/roles", headers=admin,
                                      json={"name": f"External {label} {tag}"}), 201)
            role_ids.append(role["id"])
        base_role, additional_role = role_ids
        created = ok(e2e_client.post("/api/users", headers=admin, json={
            "email": person.email, "name": person.name, "organization_id": org1["id"],
            "is_external": True, "invite": False, "trigger_automation": False,
        }), 201)
        assert created["is_external"] is True
        person.user_id = UUID(created["id"])
        created_user = True
        person = _register_and_authenticate_user(person)
        assert person.access_token is not None
        claims = decode_token(person.access_token, expected_type="access")
        assert claims is not None and claims["is_external"] is True
        assert claims["is_superuser"] is False

        def set_additional(boundaries):
            return e2e_client.put(f"/api/users/{person.user_id}/role-assignments", headers=admin, json={
                "base_role_id": base_role,
                "additional": [{"role_id": additional_role, "boundaries": boundaries}],
            })

        assert set_additional([{"kind": "organization", "organization_id": org1["id"]}]).status_code == 200
        expected_base, expected_additional = set(), set()
        for label, role_id, expected in [("base", base_role, expected_base),
                                         ("additional", additional_role, expected_additional)]:
            requested = []
            for scope_name, scope in [("global", None), ("home", org1["id"]), ("foreign", org2["id"])]:
                namespace = f"external-{label}-{scope_name}-{tag}"
                requested.append({"namespace": namespace, "organization_id": scope})
                if scope_name != "foreign":
                    expected.add(namespace)
            assigned = e2e_client.post(f"/api/roles/{role_id}/knowledge", headers=admin,
                                       json={"entries": requested})
            assert assigned.status_code == 204

        def selected():
            return {entry["namespace"] for entry in ok(e2e_client.get(
                "/api/agents/accessible-knowledge", headers=person.headers,
            ))}

        assert selected() == expected_base | expected_additional
        # An explicit foreign-only assignment loses even global selector rows:
        # the role must independently be effective at the holder's home.
        assert set_additional([{"kind": "organization", "organization_id": org2["id"]}]).status_code == 200
        assert selected() == expected_base
        assert set_additional([]).status_code == 422
        # Model a persisted missing-boundary row without weakening the writer.
        async with async_session_factory() as db:
            await db.execute(delete(UserRoleBoundary).where(UserRoleBoundary.user_id == person.user_id,
                                                           UserRoleBoundary.role_id == UUID(additional_role)))
            await db.commit()
            assert await db.scalar(select(UserRole.role_id).where(UserRole.user_id == person.user_id,
                                                                  UserRole.role_id == UUID(additional_role))) is not None
        assert selected() == expected_base
        async with async_session_factory() as db:
            assert list((await db.execute(select(RolePermission.permission).where(
                RolePermission.role_id.in_([UUID(role_id) for role_id in role_ids]),
            ))).scalars()) == []
            # Preserve ck_users_org_requires_superuser: this synthetic NULL
            # home needs the legacy physical flag, but the custom base role
            # still has zero grants. The reader must not invent Admin reach
            # from either that inert flag or the stale token's home claim.
            await db.execute(update(User).where(User.id == person.user_id).values(
                organization_id=None, is_superuser=True,
            ))
            current = await build_authorization_context(db, person.user_id)
            assert current.base_role_id == UUID(base_role)
            assert current.home_organization_id is None
            assert current.is_external
            assert not current.is_platform_admin
            assert current.held_permissions == frozenset()
            assert await db.scalar(select(User.is_superuser).where(User.id == person.user_id)) is True
            await db.commit()
        assert selected() == set()
    finally:
        if created_user:
            removed = e2e_client.delete(f"/api/users/{person.user_id}", headers=admin)
            assert removed.status_code == 204
        for role_id in role_ids:
            removed = e2e_client.delete(f"/api/roles/{role_id}", headers=admin)
            assert removed.status_code == 204
