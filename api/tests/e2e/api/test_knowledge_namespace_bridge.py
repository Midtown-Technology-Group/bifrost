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
