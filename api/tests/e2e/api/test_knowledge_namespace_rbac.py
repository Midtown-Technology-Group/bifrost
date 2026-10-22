"""Embedding-independent E2E coverage for knowledge namespace RBAC."""

from uuid import UUID, uuid4

import pytest

from src.models.orm.knowledge import KnowledgeStore


pytestmark = pytest.mark.e2e


@pytest.fixture
async def namespace_document(db_session, org1):
    """Seed one document without requiring an external embedding provider."""
    suffix = uuid4().hex[:8]
    document = KnowledgeStore(
        namespace=f"rbac-{suffix}",
        organization_id=UUID(org1["id"]),
        key=f"protected-{suffix}",
        content="namespace role protected content",
        doc_metadata={},
        embedding=[0.1, 0.2, 0.3],
        chunk_index=0,
        chunk_count=1,
    )
    db_session.add(document)
    await db_session.commit()

    yield document

    await db_session.delete(document)
    await db_session.commit()


class TestKnowledgeNamespaceRoleAccess:
    """Direct API and SDK reads require a live namespace role assignment."""

    def test_role_grant_controls_reads_without_external_embeddings(
        self,
        e2e_client,
        platform_admin,
        org1,
        org2,
        org1_user,
        org2_user,
        namespace_document,
    ):
        namespace = namespace_document.namespace
        document_key = namespace_document.key
        document_path = (
            f"/api/knowledge-sources/{namespace}/documents/"
            f"{namespace_document.id}"
        )
        role_id = None

        try:
            unassigned_all = e2e_client.get(
                "/api/knowledge-sources/documents",
                headers=org1_user.headers,
                params={"namespace": namespace},
            )
            assert unassigned_all.status_code == 200, unassigned_all.text
            assert unassigned_all.json() == []

            unassigned_namespace = e2e_client.get(
                f"/api/knowledge-sources/{namespace}/documents",
                headers=org1_user.headers,
            )
            assert unassigned_namespace.status_code == 200, (
                unassigned_namespace.text
            )
            assert unassigned_namespace.json() == []

            unassigned_document = e2e_client.get(
                document_path, headers=org1_user.headers
            )
            assert unassigned_document.status_code == 404, (
                unassigned_document.text
            )

            unassigned_sdk_get = e2e_client.get(
                "/api/sdk/knowledge/get",
                headers=org1_user.headers,
                params={"namespace": namespace, "key": document_key},
            )
            assert unassigned_sdk_get.status_code == 404, (
                unassigned_sdk_get.text
            )

            # No grant short-circuits before the embedding client is resolved.
            unassigned_sdk_search = e2e_client.post(
                "/api/sdk/knowledge/search",
                headers=org1_user.headers,
                json={
                    "query": "namespace role protected content",
                    "namespace": [namespace],
                    "limit": 10,
                },
            )
            assert unassigned_sdk_search.status_code == 200, (
                unassigned_sdk_search.text
            )
            assert unassigned_sdk_search.json() == []

            unassigned_sdk_namespaces = e2e_client.get(
                "/api/sdk/knowledge/namespaces",
                headers=org1_user.headers,
            )
            assert unassigned_sdk_namespaces.status_code == 200, (
                unassigned_sdk_namespaces.text
            )
            assert namespace not in {
                item["namespace"] for item in unassigned_sdk_namespaces.json()
            }

            role = e2e_client.post(
                "/api/roles",
                headers=platform_admin.headers,
                json={"name": f"Knowledge reader {uuid4().hex[:8]}"},
            )
            assert role.status_code == 201, role.text
            role_id = role.json()["id"]

            assigned_users = e2e_client.post(
                f"/api/roles/{role_id}/users",
                headers=platform_admin.headers,
                json={
                    "user_ids": [
                        str(org1_user.user_id),
                        str(org2_user.user_id),
                    ]
                },
            )
            assert assigned_users.status_code in (200, 201, 204), (
                assigned_users.text
            )

            assigned_namespaces = e2e_client.post(
                f"/api/roles/{role_id}/knowledge",
                headers=platform_admin.headers,
                json={
                    "entries": [
                        {
                            "namespace": namespace,
                            "organization_id": org1["id"],
                        },
                        {
                            "namespace": namespace,
                            "organization_id": org2["id"],
                        },
                    ]
                },
            )
            assert assigned_namespaces.status_code == 204, (
                assigned_namespaces.text
            )

            allowed_document = e2e_client.get(
                document_path, headers=org1_user.headers
            )
            assert allowed_document.status_code == 200, allowed_document.text
            assert allowed_document.json()["content"] == (
                "namespace role protected content"
            )

            allowed_sdk_get = e2e_client.get(
                "/api/sdk/knowledge/get",
                headers=org1_user.headers,
                params={"namespace": namespace, "key": document_key},
            )
            assert allowed_sdk_get.status_code == 200, allowed_sdk_get.text
            assert allowed_sdk_get.json()["content"] == (
                "namespace role protected content"
            )

            allowed_sdk_namespaces = e2e_client.get(
                "/api/sdk/knowledge/namespaces",
                headers=org1_user.headers,
            )
            assert allowed_sdk_namespaces.status_code == 200, (
                allowed_sdk_namespaces.text
            )
            assert namespace in {
                item["namespace"] for item in allowed_sdk_namespaces.json()
            }

            # Org2 has the same namespace grant but not document scope.
            other_org_document = e2e_client.get(
                document_path, headers=org2_user.headers
            )
            assert other_org_document.status_code == 404, (
                other_org_document.text
            )
            other_org_sdk_get = e2e_client.get(
                "/api/sdk/knowledge/get",
                headers=org2_user.headers,
                params={"namespace": namespace, "key": document_key},
            )
            assert other_org_sdk_get.status_code == 404, (
                other_org_sdk_get.text
            )
        finally:
            if role_id is not None:
                deleted = e2e_client.delete(
                    f"/api/roles/{role_id}", headers=platform_admin.headers
                )
                assert deleted.status_code in (200, 204), deleted.text
