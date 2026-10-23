"""Regression coverage for scoped config upsert field preservation."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models import ConfigType, SetConfigRequest
from src.repositories.config import ConfigRepository


def _row(*, org_id, config_type="secret", description="keep me"):
    row = MagicMock()
    row.id = uuid4()
    row.key = "API_TOKEN"
    row.organization_id = org_id
    row.integration_id = None
    row.value = {"value": "encrypted-old"}
    row.config_type = MagicMock(value=config_type)
    row.description = description
    row.required = False
    row.position = 0
    row.updated_at = None
    row.updated_by = None
    return row


def _repo(row, *, org_id):
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session.execute.return_value = result
    return ConfigRepository(session, org_id=org_id, is_superuser=True), session


@pytest.mark.asyncio
async def test_upsert_without_type_preserves_secret_encryption_and_description():
    org_id = uuid4()
    row = _row(org_id=org_id)
    repo, session = _repo(row, org_id=org_id)

    with patch("src.core.security.encrypt_secret", return_value="encrypted-new") as encrypt:
        response = await repo.set_config(
            SetConfigRequest(key="API_TOKEN", value="new secret"),
            updated_by="operator@example.com",
        )

    encrypt.assert_called_once_with("new secret")
    assert row.value == {"value": "encrypted-new"}
    assert row.config_type.value == "secret"
    assert row.description == "keep me"
    assert response.type == ConfigType.SECRET
    assert response.description == "keep me"
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_explicit_type_and_description_change_are_applied():
    org_id = uuid4()
    row = _row(org_id=org_id)
    repo, _ = _repo(row, org_id=org_id)

    response = await repo.set_config(
        SetConfigRequest(
            key="API_TOKEN",
            value="visible",
            type=ConfigType.STRING,
            description=None,
        ),
        updated_by="operator@example.com",
    )

    assert row.value == {"value": "visible"}
    assert row.config_type.value == "string"
    assert row.description is None
    assert response.type == ConfigType.STRING


@pytest.mark.asyncio
async def test_upsert_query_is_bound_to_the_repository_org():
    org_id = uuid4()
    repo, session = _repo(_row(org_id=org_id), org_id=org_id)

    await repo.set_config(
        SetConfigRequest(key="API_TOKEN", value="value"),
        updated_by="operator@example.com",
    )

    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert f"organization_id = '{org_id.hex}'" in sql
    assert "integration_id IS NULL" in sql
