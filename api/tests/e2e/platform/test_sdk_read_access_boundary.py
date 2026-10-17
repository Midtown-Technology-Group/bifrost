"""Run unchanged on current main and CRED: real HTTP execution/bypass access.

Use a nonexistent integration so admitted reads return null without touching
customer resources. Signed synthetic session tokens exercise real auth; this
does not claim runtime execution, issuance, or a populated integration read.
"""

from uuid import uuid4

import pytest

from src.core.security import create_access_token

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("operation", ["get", "get_mapping"])
@pytest.mark.parametrize(
    ("flags", "expected_status"),
    [
        (None, 401),
        ({}, 403),
        ({"is_external": True}, 403),
        ({"is_superuser": True}, 200),
        ({"is_provider_org": True}, 200),
    ],
    ids=["anonymous", "ordinary", "external", "admin", "provider"],
)
def test_sdk_reads_preserve_execution_or_bypass_access(
    e2e_client, operation, flags, expected_status
):
    headers = {}
    if flags is not None:
        token = create_access_token(
            {
                "sub": str(uuid4()),
                "email": "synthetic-sdk-access@example.invalid",
                "name": "Synthetic SDK access",
                "org_id": str(uuid4()),
                "is_superuser": False,
                "is_provider_org": False,
                "is_external": False,
                **flags,
            }
        )
        headers["Authorization"] = f"Bearer {token}"
    response = e2e_client.post(
        f"/api/sdk/integrations/{operation}",
        json={"name": f"nonexistent-sdk-access-{uuid4()}"},
        headers=headers,
    )
    assert response.status_code == expected_status
    if expected_status == 401:
        assert response.json() == {"detail": "Not authenticated"}
        assert response.headers["www-authenticate"] == "Bearer"
    elif expected_status == 403:
        assert response.json() == {
            "detail": "Execution credentials or platform access required"
        }
    else:
        assert response.json() is None
