"""Real PostgreSQL/API proof that competing reviewed writes have one winner."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest


@pytest.mark.e2e
def test_conditional_document_atomic_concurrency(e2e_client, platform_admin):
    headers = platform_admin.headers
    table = e2e_client.post(
        "/api/tables", headers=headers, json={"name": f"cas_{uuid4().hex}"}
    )
    assert table.status_code == 201, table.text
    table_id = table.json()["id"]
    try:
        created = e2e_client.post(
            f"/api/tables/{table_id}/documents",
            headers=headers,
            json={"data": {"state": "open", "preserved": 1}},
        )
        assert created.status_code == 201, created.text
        original = created.json()
        path = f"/api/tables/{table_id}/documents/{original['id']}"
        precondition = {
            "expected_updated_at": original["updated_at"],
            "expected_data": original["data"],
        }
        wrong = e2e_client.patch(
            path + "/conditional",
            headers=headers,
            json={"data": {"state": "wrong"}, **precondition, "expected_data": {}},
        )
        assert wrong.status_code == 409, wrong.text
        # Python considers True == 1; JSONB preconditions must distinguish them.
        wrong_type = e2e_client.patch(
            path + "/conditional",
            headers=headers,
            json={
                "data": {"state": "wrong"},
                **precondition,
                "expected_data": {"state": "open", "preserved": True},
            },
        )
        assert wrong_type.status_code == 409, wrong_type.text

        def attempt(index):
            return e2e_client.patch(
                path + "/conditional",
                headers=headers,
                json={"data": {"winner": index, "state": "closed"}, **precondition},
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(attempt, range(8)))
        assert [r.status_code for r in outcomes].count(200) == 1, [
            r.text for r in outcomes
        ]
        assert [r.status_code for r in outcomes].count(409) == 7
        winner = next(r.json() for r in outcomes if r.status_code == 200)
        readback = e2e_client.get(path, headers=headers)
        assert readback.status_code == 200
        assert readback.json() == winner
        assert winner["data"]["preserved"] == 1
        assert winner["updated_at"] != original["updated_at"]
        missing = e2e_client.patch(
            path + "/conditional", headers=headers, json={"data": {}}
        )
        assert missing.status_code == 422
        # Ordinary callers retain the original merge behavior.
        ordinary = e2e_client.patch(
            path, headers=headers, json={"data": {"ordinary": True}}
        )
        assert ordinary.status_code == 200
        assert ordinary.json()["data"]["winner"] == winner["data"]["winner"]
    finally:
        e2e_client.delete(f"/api/tables/{table_id}", headers=headers)


@pytest.mark.e2e
def test_conditional_document_denies_foreign_scope(
    e2e_client, platform_admin, org1_user, org2
):
    headers = platform_admin.headers
    response = e2e_client.post(
        "/api/tables",
        headers=headers,
        json={"name": f"cas_scope_{uuid4().hex}", "organization_id": str(org2["id"])},
    )
    assert response.status_code == 201, response.text
    table_id = response.json()["id"]
    try:
        document = e2e_client.post(
            f"/api/tables/{table_id}/documents?scope={org2['id']}",
            headers=headers,
            json={"data": {"state": "original"}},
        )
        assert document.status_code == 201, document.text
        original = document.json()
        path = f"/api/tables/{table_id}/documents/{original['id']}"
        denied = e2e_client.patch(
            path + f"/conditional?scope={org2['id']}",
            headers=org1_user.headers,
            json={
                "data": {"state": "bad"},
                "expected_updated_at": original["updated_at"],
                "expected_data": original["data"],
            },
        )
        assert denied.status_code == 403, denied.text
        readback = e2e_client.get(path + f"?scope={org2['id']}", headers=headers)
        assert readback.status_code == 200
        assert readback.json() == original
    finally:
        e2e_client.delete(f"/api/tables/{table_id}?scope={org2['id']}", headers=headers)
