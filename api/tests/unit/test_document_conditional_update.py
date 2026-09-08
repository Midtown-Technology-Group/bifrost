"""Atomic document preconditions and conditional-only SDK transport contract."""

from datetime import datetime, timezone
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from src.models.contracts.tables import ConditionalDocumentUpdate, DocumentUpdate
from src.routers import tables as routes

sdk = import_module("bifrost.tables")
REVISION = datetime(2026, 9, 7, tzinfo=timezone.utc)


def test_contract_requires_aware_revision_and_data_dependency():
    with pytest.raises(ValidationError):
        ConditionalDocumentUpdate(data={})
    with pytest.raises(ValidationError):
        DocumentUpdate(data={}, expected_updated_at="2026-09-07T00:00:00")
    with pytest.raises(ValidationError):
        DocumentUpdate(data={}, expected_data={})
    assert (
        ConditionalDocumentUpdate(
            data={}, expected_updated_at=REVISION
        ).expected_updated_at
        == REVISION
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [409, 404, 503])
async def test_sdk_conditional_endpoint_never_falls_back(monkeypatch, status):
    response = httpx.Response(
        status,
        json={"detail": "rejected"},
        request=httpx.Request("PATCH", "https://test/"),
    )
    client = SimpleNamespace(patch=AsyncMock(return_value=response))
    monkeypatch.setattr(sdk, "get_client", lambda: client)
    monkeypatch.setattr(sdk, "resolve_scope", lambda scope: scope)
    with pytest.raises(httpx.HTTPStatusError):
        await sdk.tables.update(
            "rows",
            "doc",
            {"status": "closed"},
            scope="global",
            expected_updated_at=REVISION,
            expected_data={},
        )
    client.patch.assert_awaited_once_with(
        "/api/tables/rows/documents/doc/conditional?scope=global",
        json={
            "data": {"status": "closed"},
            "expected_updated_at": REVISION.isoformat(),
            "expected_data": {},
        },
        retry_safe=False,
    )


@pytest.mark.asyncio
async def test_sdk_uncertain_transport_is_not_replayed(monkeypatch):
    client = SimpleNamespace(
        patch=AsyncMock(side_effect=httpx.ReadTimeout("uncertain"))
    )
    monkeypatch.setattr(sdk, "get_client", lambda: client)
    monkeypatch.setattr(sdk, "resolve_scope", lambda scope: scope)
    with pytest.raises(httpx.ReadTimeout):
        await sdk.tables.update(
            "rows", "doc", {}, expected_updated_at=REVISION.isoformat()
        )
    assert client.patch.await_count == 1


@pytest.mark.asyncio
async def test_repository_uses_single_atomic_scoped_update():
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))
    )
    table = SimpleNamespace(id=uuid4())
    repo = routes.DocumentRepository(session, table)
    assert (
        await repo.update(
            "doc",
            {"state": "closed"},
            None,
            expected_updated_at=REVISION,
            expected_data={"state": "open"},
        )
        is None
    )
    stmt = session.execute.call_args.args[0]
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "UPDATE documents SET" in sql
    assert "documents.data ||" in sql
    assert (
        "documents.table_id =" in sql
        and "documents.updated_at =" in sql
        and "documents.data =" in sql
    )
    assert "RETURNING documents.id" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["scope", "ownership", "policy"])
async def test_conditional_route_retains_all_write_gates(monkeypatch, gate):
    table = SimpleNamespace(id=uuid4())
    existing = SimpleNamespace(id="doc", data={}, updated_at=REVISION)
    ctx = SimpleNamespace(
        db=SimpleNamespace(commit=AsyncMock()), user=SimpleNamespace()
    )
    error = routes.HTTPException(status_code=403, detail="denied")
    monkeypatch.setattr(
        routes,
        "get_table_or_404",
        AsyncMock(side_effect=error if gate == "scope" else None, return_value=table),
    )
    monkeypatch.setattr(
        routes,
        "_assert_solution_write_targets_owned_table",
        AsyncMock(side_effect=error if gate == "ownership" else None),
    )
    monkeypatch.setattr(
        routes,
        "_check_action_or_403",
        AsyncMock(side_effect=error if gate == "policy" else None),
    )
    monkeypatch.setattr(routes, "_resolve_attribution", lambda *args: (None, None))
    monkeypatch.setattr(routes, "_row_from_doc", lambda doc: doc.data)
    repo = SimpleNamespace(get=AsyncMock(return_value=existing), update=AsyncMock())
    monkeypatch.setattr(routes, "DocumentRepository", lambda *args: repo)
    with pytest.raises(routes.HTTPException) as raised:
        await routes.update_document_conditional(
            "rows",
            "doc",
            ConditionalDocumentUpdate(data={}, expected_updated_at=REVISION),
            ctx,
            "global",
        )
    assert raised.value.status_code == 403
    repo.update.assert_not_awaited()
    ctx.db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["revision", "data", "during_write"])
async def test_stale_or_racing_snapshot_returns409_without_publication(
    monkeypatch, changed
):
    table = SimpleNamespace(id=uuid4())
    existing = SimpleNamespace(id="doc", data={"state": "open"}, updated_at=REVISION)
    ctx = SimpleNamespace(
        db=SimpleNamespace(commit=AsyncMock()), user=SimpleNamespace()
    )
    monkeypatch.setattr(routes, "get_table_or_404", AsyncMock(return_value=table))
    monkeypatch.setattr(
        routes, "_assert_solution_write_targets_owned_table", AsyncMock()
    )
    monkeypatch.setattr(routes, "_check_action_or_403", AsyncMock())
    monkeypatch.setattr(routes, "_resolve_attribution", lambda *args: (None, None))
    monkeypatch.setattr(routes, "_row_from_doc", lambda doc: doc.data)
    publish = AsyncMock()
    monkeypatch.setattr(routes, "publish_document_change", publish)
    repo = SimpleNamespace(
        get=AsyncMock(return_value=existing), update=AsyncMock(return_value=None)
    )
    monkeypatch.setattr(routes, "DocumentRepository", lambda *args: repo)
    revision = REVISION.replace(year=2025) if changed == "revision" else REVISION
    data = {} if changed == "data" else existing.data
    with pytest.raises(routes.HTTPException) as raised:
        await routes.update_document_conditional(
            "rows",
            "doc",
            ConditionalDocumentUpdate(
                data={"state": "closed"},
                expected_updated_at=revision,
                expected_data=data,
            ),
            ctx,
            "global",
        )
    assert raised.value.status_code == 409
    if changed == "during_write":
        repo.update.assert_awaited_once_with(
            "doc",
            {"state": "closed"},
            updated_by=None,
            expected_updated_at=REVISION,
            expected_data=existing.data,
            authorized_data=existing.data,
        )
    else:
        repo.update.assert_not_awaited()
    ctx.db.commit.assert_not_awaited()
    publish.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [409, 503, "timeout"])
async def test_actual_patch_transport_does_not_retry_uncertain_write(outcome):
    from bifrost.client import BifrostClient

    transport = SimpleNamespace(patch=AsyncMock())
    if outcome == "timeout":
        transport.patch.side_effect = httpx.ReadTimeout("uncertain")
    else:
        transport.patch.return_value = httpx.Response(
            outcome, request=httpx.Request("PATCH", "https://test/")
        )
    client = object.__new__(BifrostClient)
    client._access_token = "test-token"
    client._get_async_client = lambda: transport
    if outcome == "timeout":
        with pytest.raises(httpx.ReadTimeout):
            await client.patch("/conditional", json={}, retry_safe=False)
    else:
        response = await client.patch("/conditional", json={}, retry_safe=False)
        assert response.status_code == outcome
    assert transport.patch.await_count == 1
