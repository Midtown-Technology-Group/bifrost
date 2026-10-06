"""Domain tests for the public workspace/platform boundary."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from bifrost.admin_models import OAuthRecoveryRequest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.auth import get_execution_context
from src.core.principal import UserPrincipal
from src.models.enums import ExecutionStatus
from src.routers.workspace_admin import router
from src.services import workspace_admin as service

pytestmark = pytest.mark.asyncio


def principal(**kwargs):
    return UserPrincipal(
        user_id=uuid4(),
        email="admin@example.test",
        organization_id=None,
        is_superuser=True,
        **kwargs,
    )


def provider(org_id=None):
    return SimpleNamespace(
        id=uuid4(),
        organization_id=org_id,
        provider_name="NinjaOne",
        oauth_flow_type="authorization_code",
        token_url="https://oauth.example/token",
        token_url_defaults={},
        encrypted_client_secret=b"encrypted-secret",
        client_id="client",
        audience=None,
        redirect_uri="https://example/callback",
        scopes=["monitoring"],
        status="completed",
    )


def token(org_id=None, **kwargs):
    values = {
        "id": uuid4(),
        "organization_id": org_id,
        "encrypted_access_token": b"encrypted-access",
        "encrypted_refresh_token": b"encrypted-refresh",
        "expires_at": datetime.now(UTC),
        "scopes": ["monitoring"],
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


@pytest.fixture
def seams(monkeypatch):
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock())
    db.flush = AsyncMock()
    p = provider()
    rows = [token()]
    monkeypatch.setattr(service, "connection", AsyncMock(return_value=p))
    monkeypatch.setattr(service, "token_rows", AsyncMock(return_value=rows))
    monkeypatch.setattr(service, "emit_audit", AsyncMock())
    monkeypatch.setattr(service, "decrypt_secret", lambda _value: "plaintext-secret")
    monkeypatch.setattr(service, "encrypt_secret", lambda _value: "ciphertext")
    client = SimpleNamespace(
        refresh_access_token=AsyncMock(), exchange_code_for_token=AsyncMock()
    )
    monkeypatch.setattr(service, "OAuthProviderClient", lambda **_kwargs: client)
    return db, p, rows, client


@pytest.mark.parametrize(
    "engine,delegated", [(False, False), (True, False), (True, True)]
)
async def test_admin_grant(engine, delegated):
    user = principal(
        is_engine_token=engine,
        delegated_is_superuser=delegated,
        delegated_user_id=uuid4() if engine else None,
    )
    if not engine or delegated:
        assert service.authorize_admin(user, "global") is None
    else:
        with pytest.raises(service.AdminOperationError) as error:
            service.authorize_admin(user, "global")
        assert error.value.status_code == 403


async def test_non_admin_rejected_before_database(seams):
    db, *_ = seams
    user = principal()
    user.is_superuser = False
    with pytest.raises(service.AdminOperationError):
        await service.inspect_oauth(db, user, "NinjaOne", "global")
    service.connection.assert_not_awaited()


async def test_delegated_admin_can_select_explicit_target_scope():
    user = principal(
        is_engine_token=True, delegated_is_superuser=True, delegated_user_id=uuid4()
    )
    user.organization_id = uuid4()
    assert service.authorize_admin(user, user.organization_id) == user.organization_id
    assert service.authorize_admin(user, "global") is None
    other_org = uuid4()
    assert service.authorize_admin(user, other_org) == other_org
    user.delegated_is_superuser = False
    for scope in ("global", user.organization_id, other_org):
        with pytest.raises(service.AdminOperationError) as error:
            service.authorize_admin(user, scope)
        assert error.value.status_code == 403


async def test_diagnostics_returns_presence_only(seams):
    db, p, _rows, _ = seams
    result = await service.inspect_oauth(db, principal(), "NinjaOne", "global")
    wire = result.model_dump_json()
    assert result.tokens[0].has_refresh_token
    assert "encrypted-access" not in wire and "encrypted-refresh" not in wire
    assert "plaintext-secret" not in wire and "client_id" not in wire
    service.token_rows.assert_awaited_once_with(db, p, None)
    assert service.emit_audit.await_args.kwargs["strict"] is True


@pytest.mark.parametrize("action", ["refresh", "exchange_code_without_scope"])
async def test_recovery_preserves_contract_without_returning_tokens(seams, action):
    db, p, rows, client = seams
    body = {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_at": datetime.now(UTC),
        "scope": "monitoring management",
    }
    client.refresh_access_token.return_value = (True, body)
    client.exchange_code_for_token.return_value = (True, body)
    args = (
        {"refresh_token": "provided-refresh"}
        if action == "refresh"
        else {"code": "secret-code", "redirect_uri": p.redirect_uri}
    )
    result = await service.recover_oauth(
        db,
        principal(),
        "NinjaOne",
        OAuthRecoveryRequest(scope="global", action=action, **args),
    )
    assert result.has_refresh_token
    assert rows[0].encrypted_access_token == b"ciphertext"
    assert rows[0].encrypted_refresh_token == b"ciphertext"
    for secret in (
        "new-access",
        "new-refresh",
        "provided-refresh",
        "secret-code",
        "plaintext-secret",
    ):
        assert secret not in result.model_dump_json()
        assert secret not in str(service.emit_audit.await_args)
    if action == "exchange_code_without_scope":
        assert client.exchange_code_for_token.await_args.kwargs["scopes"] is None
        assert result.scope == "monitoring management"
    else:
        assert result.scope == "monitoring"


async def test_refresh_falls_back_to_supplied_token(seams):
    db, _, rows, client = seams
    client.refresh_access_token.return_value = (True, {"access_token": "new-access"})
    await service.recover_oauth(
        db,
        principal(),
        "NinjaOne",
        OAuthRecoveryRequest(
            scope="global", action="refresh", refresh_token="provided-refresh"
        ),
    )
    assert rows[0].encrypted_refresh_token == b"ciphertext"


async def test_refresh_uses_stored_token(seams):
    db, _, _, client = seams
    client.refresh_access_token.return_value = (True, {"access_token": "new-access"})
    await service.recover_oauth(
        db,
        principal(),
        "NinjaOne",
        OAuthRecoveryRequest(scope="global", action="refresh"),
    )
    assert (
        client.refresh_access_token.await_args.kwargs["refresh_token"]
        == "plaintext-secret"
    )


async def test_vendor_failure_is_redacted_and_does_not_persist(seams):
    db, _, _, client = seams
    client.refresh_access_token.return_value = (
        False,
        {"error_description": "leaked-token"},
    )
    with pytest.raises(service.AdminOperationError) as error:
        await service.recover_oauth(
            db,
            principal(),
            "NinjaOne",
            OAuthRecoveryRequest(
                scope="global", action="refresh", refresh_token="secret"
            ),
        )
    assert "leaked-token" not in str(error.value)
    db.flush.assert_not_awaited()
    service.emit_audit.assert_not_awaited()


async def test_exchange_without_refresh_token_does_not_persist(seams):
    db, p, _, client = seams
    client.exchange_code_for_token.return_value = (True, {"access_token": "new-access"})
    with pytest.raises(service.AdminOperationError):
        await service.recover_oauth(
            db,
            principal(),
            "NinjaOne",
            OAuthRecoveryRequest(
                scope="global",
                action="exchange_code_without_scope",
                code="code",
                redirect_uri=p.redirect_uri,
            ),
        )
    db.flush.assert_not_awaited()


async def test_reconciliation_uses_scoped_source_and_is_idempotent(seams):
    db, p, rows, _ = seams
    rows.append(token(encrypted_access_token=None, encrypted_refresh_token=None))
    for _ in range(2):
        result = await service.reconcile_oauth(db, principal(), "NinjaOne", "global")
        assert result.token_rows_updated == 2
        assert result.provider_rows_updated == 1
        assert rows[1].encrypted_refresh_token == rows[0].encrypted_refresh_token
    service.token_rows.assert_awaited_with(db, p, None)


async def test_reconciliation_no_source_is_noop(seams):
    db, _, rows, _ = seams
    rows[0].encrypted_refresh_token = None
    result = await service.reconcile_oauth(db, principal(), "NinjaOne", "global")
    assert result.token_rows_updated == 0
    db.flush.assert_not_awaited()


@pytest.mark.parametrize("status", list(ExecutionStatus))
async def test_redaction_terminal_status_and_idempotence(seams, status):
    db, _, _, _ = seams
    execution = SimpleNamespace(
        id=uuid4(),
        status=status,
        parameters={"secret": "value"},
        result={"secret": "value"},
        variables={},
        execution_context={},
        error_message="sensitive",
        workflow_id=uuid4(),
    )
    db.execute.return_value.scalar_one_or_none.return_value = execution
    terminal = status in {
        ExecutionStatus.SUCCESS,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMEOUT,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.COMPLETED_WITH_ERRORS,
    }
    original_workflow = execution.workflow_id
    if terminal:
        for _ in range(2):
            result = await service.redact_execution(
                db, principal(), execution.id, "global"
            )
            assert result.rows_updated == 1
        assert (
            execution.parameters
            == execution.result
            == execution.variables
            == execution.execution_context
            == {"redacted": True}
        )
        assert execution.error_message is None
        assert execution.workflow_id == original_workflow
    else:
        with pytest.raises(service.AdminOperationError) as error:
            await service.redact_execution(db, principal(), execution.id, "global")
        assert error.value.status_code == 409
        assert execution.parameters == {"secret": "value"}


async def test_redaction_missing_returns_404(seams):
    db, *_ = seams
    db.execute.return_value.scalar_one_or_none.return_value = None
    with pytest.raises(service.AdminOperationError) as error:
        await service.redact_execution(db, principal(), uuid4(), "global")
    assert error.value.status_code == 404


async def test_real_queries_pin_provider_token_and_execution_scope(monkeypatch):
    db = MagicMock(execute=AsyncMock(return_value=MagicMock()))
    p = provider(uuid4())
    db.execute.return_value.scalar_one_or_none.return_value = p
    assert await service.connection(db, "NinjaOne", p.organization_id) is p
    statement = str(db.execute.await_args.args[0])
    assert "oauth_providers.organization_id =" in statement
    assert "FOR UPDATE" in statement
    db.execute.return_value.scalars.return_value.all.return_value = []
    await service.token_rows(db, p, p.organization_id)
    statement = str(db.execute.await_args.args[0])
    assert "oauth_tokens.organization_id =" in statement
    assert "oauth_tokens.provider_id =" in statement
    monkeypatch.setattr(service, "audit_admin", AsyncMock())
    db.execute.return_value.scalar_one_or_none.return_value = None
    with pytest.raises(service.AdminOperationError):
        await service.redact_execution(db, principal(), uuid4(), p.organization_id)
    statement = str(db.execute.await_args.args[0])
    assert "executions.organization_id =" in statement
    assert "executions.id =" in statement


async def test_audit_uses_delegated_admin_and_target_org(seams):
    db, *_ = seams
    delegated_id = uuid4()
    org_id = uuid4()
    user = principal(
        is_engine_token=True,
        delegated_user_id=delegated_id,
        delegated_is_superuser=True,
    )
    await service.audit_admin(
        db, user, org_id, "oauth.recover", "oauth_provider", uuid4()
    )
    args = service.emit_audit.await_args.kwargs
    assert args["actor_override"].user_id == delegated_id
    assert args["actor_override"].organization_id == org_id
    assert args["strict"] is True


async def test_http_auth_and_validation_errors_never_echo_credentials():
    app = FastAPI()
    app.include_router(router)
    user = principal()
    user.is_superuser = False
    db = MagicMock(execute=AsyncMock(return_value=MagicMock()))
    app.dependency_overrides[get_execution_context] = lambda: SimpleNamespace(
        db=db, user=user
    )
    with TestClient(app) as client:
        response = client.get(
            "/api/oauth/connections/NinjaOne/diagnostics", params={"scope": "global"}
        )
        assert response.status_code == 403
        response = client.post(
            "/api/oauth/connections/NinjaOne/recover",
            json={
                "scope": "global",
                "action": "invalid",
                "refresh_token": "must-never-echo",
            },
        )
        assert response.status_code == 422
        assert "must-never-echo" not in response.text
        response = client.post(
            f"/api/executions/{uuid4()}/redact-sensitive-fields",
            json={"scope": "global", "result": {"arbitrary": "mutation"}},
        )
        assert response.status_code == 422
    db.execute.assert_not_awaited()


@pytest.mark.parametrize(
    "kind",
    ["external", "embed", "inactive", "engine_without_delegation", "engine_external"],
)
async def test_privileged_operations_reject_ineligible_principals(kind, seams):
    db, *_ = seams
    user = principal()
    if kind == "external":
        user.is_external = True
    elif kind == "embed":
        user.embed = True
    elif kind == "inactive":
        user.is_active = False
    else:
        user.is_engine_token = True
        if kind == "engine_external":
            user.delegated_user_id = uuid4()
            user.delegated_is_superuser = True
            user.delegated_is_external = True
    with pytest.raises(service.AdminOperationError) as error:
        await service.reconcile_oauth(db, user, "NinjaOne", "global")
    assert error.value.status_code == 403
    service.connection.assert_not_awaited()


async def test_org_recovery_leaves_global_provider_status_unchanged(seams):
    db, p, rows, client = seams
    org_id = uuid4()
    rows[0].organization_id = org_id
    p.status = "not_connected"
    client.refresh_access_token.return_value = (
        True,
        {"access_token": "new", "refresh_token": "refresh"},
    )
    await service.recover_oauth(
        db,
        principal(),
        "NinjaOne",
        OAuthRecoveryRequest(scope=org_id, action="refresh", refresh_token="input"),
    )
    assert p.status == "not_connected"
    service.token_rows.assert_awaited_once_with(db, p, org_id)


async def test_strict_audit_failure_prevents_success(seams):
    db, *_ = seams
    service.emit_audit.side_effect = RuntimeError("Audit storage unavailable")
    with pytest.raises(RuntimeError, match="Audit storage unavailable"):
        await service.reconcile_oauth(db, principal(), "NinjaOne", "global")
    db.commit.assert_not_called()


async def test_provider_controlled_error_material_is_never_logged(monkeypatch, caplog):
    from src.services import oauth_provider

    class Response:
        status = 400

        async def json(self):
            return {
                "error": "secret-error-code",
                "error_description": "secret-description",
            }

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class Session(Response):
        def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(oauth_provider.aiohttp, "TCPConnector", lambda **_kwargs: None)
    monkeypatch.setattr(
        oauth_provider.aiohttp, "ClientSession", lambda **_kwargs: Session()
    )
    success, _body = await oauth_provider.OAuthProviderClient(
        max_retries=1
    )._make_token_request(
        "https://oauth.example/token", {"refresh_token": "secret-refresh"}
    )
    assert not success
    assert "status=400" in caplog.text
    for secret in ("secret-error-code", "secret-description", "secret-refresh"):
        assert secret not in caplog.text


@pytest.mark.parametrize("org_id", [None, uuid4()])
async def test_redaction_resolves_and_pins_target_scope_without_payload_read(
    seams, org_id
):
    db, *_ = seams
    execution = SimpleNamespace(id=uuid4(), status=ExecutionStatus.SUCCESS)
    db.execute.return_value.one_or_none.return_value = (org_id,)
    db.execute.return_value.scalar_one_or_none.return_value = execution
    result = await service.redact_execution(db, principal(), execution.id)
    assert result.redacted
    statements = [str(call.args[0]) for call in db.execute.await_args_list]
    assert statements[0].startswith("SELECT executions.organization_id")
    assert "executions.id =" in statements[0] and "FOR UPDATE" in statements[0]
    assert "executions.organization_id" in statements[1]
    assert (
        service.emit_audit.await_args.kwargs["actor_override"].organization_id == org_id
    )


async def test_redaction_auto_scope_missing_and_unauthorized(seams):
    db, *_ = seams
    user = principal()
    user.is_superuser = False
    with pytest.raises(service.AdminOperationError) as denied:
        await service.redact_execution(db, user, uuid4())
    assert denied.value.status_code == 403
    db.execute.assert_not_awaited()
    db.execute.return_value.one_or_none.return_value = None
    with pytest.raises(service.AdminOperationError) as missing:
        await service.redact_execution(db, principal(), uuid4())
    assert missing.value.status_code == 404
