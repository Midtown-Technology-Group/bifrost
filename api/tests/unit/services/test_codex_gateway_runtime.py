import json
from asyncio import sleep
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import MultipleResultsFound

from src.services.codex_gateway.runtime import (
    CodexGatewayRuntime,
    CodexGatewayUpstreamResponse,
    extract_gateway_key,
)

VALID_GATEWAY_KEY = f"bfck_{'a' * 43}"


class FakeUpstreamClient:
    def __init__(self):
        self.requests = []

    async def create_response(self, *, access_token, payload):
        await sleep(0)
        self.requests.append({"access_token": access_token, "payload": payload})
        return CodexGatewayUpstreamResponse(
            status_code=200,
            body={"id": "resp_test", "model": payload["model"], "output": []},
            input_token_count=12,
            output_token_count=3,
        )


class FailingUpstreamClient:
    async def create_response(self, *, access_token, payload):
        await sleep(0)
        raise RuntimeError("upstream failed")


class SlowUpstreamClient:
    async def create_response(self, *, access_token, payload):
        await sleep(1)
        return CodexGatewayUpstreamResponse(status_code=200, body={})


def _key_record(*, allowed_models=None, denied_models=None):
    return SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        project_id=uuid4(),
        name="developer workstation",
        allowed_models=allowed_models or [],
        denied_models=denied_models or [],
        daily_limit=None,
        monthly_limit=None,
        status="active",
        revoked_at=None,
    )


def _account_record(user_id):
    return SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        upstream_subject="chatgpt-user-123",
        upstream_email="dev@example.test",
        upstream_workspace_id="workspace-midtown",
        encrypted_access_token="encrypted-access-token",
        access_token_expires_at=None,
        last_refresh_at=None,
        last_used_at=None,
        revoked_at=None,
    )


def _repository(*, key_record, account_record):
    repository = AsyncMock()
    repository.get_active_gateway_key_by_plaintext.return_value = key_record
    repository.get_active_upstream_account_for_user.return_value = account_record
    repository.create_request_log = AsyncMock()
    return repository


@pytest.mark.asyncio
async def test_allowed_response_uses_same_user_upstream_token_and_logs_metadata(
    monkeypatch,
):
    key = _key_record(allowed_models=["gpt-5.1-codex"])
    account = _account_record(key.user_id)
    repository = _repository(key_record=key, account_record=account)
    upstream = FakeUpstreamClient()
    monkeypatch.setattr(
        "src.services.codex_gateway.runtime.decrypt_secret",
        lambda encrypted: f"plain::{encrypted}",
    )
    runtime = CodexGatewayRuntime(repository=repository, upstream_client=upstream)

    response = await runtime.create_response(
        gateway_key=VALID_GATEWAY_KEY,
        payload={"model": "gpt-5.1-codex", "input": "do not log me"},
        source_ip="203.0.113.10",
        client_user_agent="codex-cli",
    )

    assert response.status_code == 200
    assert response.body["id"] == "resp_test"
    assert upstream.requests == [
        {
            "access_token": "plain::encrypted-access-token",
            "payload": {"model": "gpt-5.1-codex", "input": "do not log me"},
        }
    ]
    repository.create_request_log.assert_awaited_once()
    log_kwargs = repository.create_request_log.call_args.kwargs
    assert log_kwargs["user_id"] == key.user_id
    assert log_kwargs["gateway_key_id"] == key.id
    assert log_kwargs["oauth_account_id"] == account.id
    assert log_kwargs["policy_decision"] == "allow"
    assert log_kwargs["request_metadata"]["client_type"] == "openai-compatible"
    assert "input" not in log_kwargs["request_metadata"]
    assert log_kwargs["input_token_count"] == 12
    assert log_kwargs["output_token_count"] == 3


@pytest.mark.asyncio
async def test_unknown_well_formed_gateway_key_returns_openai_error_without_log():
    repository = _repository(key_record=None, account_record=None)
    runtime = CodexGatewayRuntime(
        repository=repository, upstream_client=FakeUpstreamClient()
    )

    response = await runtime.create_response(
        gateway_key=VALID_GATEWAY_KEY,
        payload={"model": "gpt-5.1-codex"},
    )

    assert response.status_code == 401
    assert response.body["error"]["code"] == "invalid_gateway_key"
    repository.create_request_log.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_gateway_key_returns_openai_error_without_lookup_or_log():
    repository = _repository(key_record=None, account_record=None)
    runtime = CodexGatewayRuntime(
        repository=repository, upstream_client=FakeUpstreamClient()
    )

    response = await runtime.create_response(
        gateway_key="not-a-bifrost-key",
        payload={"model": "gpt-5.1-codex"},
    )

    assert response.status_code == 401
    assert response.body["error"]["code"] == "invalid_gateway_key"
    repository.get_active_gateway_key_by_plaintext.assert_not_awaited()
    repository.create_request_log.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_key_authentication_happens_before_payload_validation():
    repository = _repository(key_record=None, account_record=None)
    runtime = CodexGatewayRuntime(
        repository=repository, upstream_client=FakeUpstreamClient()
    )

    response = await runtime.create_response(
        gateway_key=VALID_GATEWAY_KEY,
        payload={"input": "missing model"},
    )

    assert response.status_code == 401
    assert response.body["error"]["code"] == "invalid_gateway_key"


@pytest.mark.asyncio
async def test_missing_upstream_account_fails_closed_and_logs_denial():
    key = _key_record()
    repository = _repository(key_record=key, account_record=None)
    runtime = CodexGatewayRuntime(
        repository=repository, upstream_client=FakeUpstreamClient()
    )

    response = await runtime.create_response(
        gateway_key=VALID_GATEWAY_KEY,
        payload={"model": "gpt-5.1-codex"},
    )

    assert response.status_code == 403
    assert response.body["error"]["code"] == "upstream_identity_not_connected"
    repository.create_request_log.assert_awaited_once()
    log_kwargs = repository.create_request_log.call_args.kwargs
    assert log_kwargs["user_id"] == key.user_id
    assert log_kwargs["oauth_account_id"] is None
    assert log_kwargs["policy_decision"] == "deny"


@pytest.mark.asyncio
async def test_ambiguous_upstream_account_fails_closed_and_logs_denial():
    key = _key_record()
    repository = _repository(key_record=key, account_record=None)
    repository.get_active_upstream_account_for_user.side_effect = MultipleResultsFound()
    runtime = CodexGatewayRuntime(
        repository=repository, upstream_client=FakeUpstreamClient()
    )

    response = await runtime.create_response(
        gateway_key=VALID_GATEWAY_KEY,
        payload={"model": "gpt-5.1-codex"},
    )

    assert response.status_code == 403
    assert response.body["error"]["code"] == "upstream_identity_ambiguous"
    repository.create_request_log.assert_awaited_once()
    log_kwargs = repository.create_request_log.call_args.kwargs
    assert log_kwargs["user_id"] == key.user_id
    assert log_kwargs["oauth_account_id"] is None
    assert log_kwargs["policy_decision"] == "deny"


@pytest.mark.asyncio
async def test_denied_model_returns_structured_error_and_does_not_call_upstream():
    key = _key_record(allowed_models=["gpt-5.1-codex"])
    account = _account_record(key.user_id)
    repository = _repository(key_record=key, account_record=account)
    upstream = FakeUpstreamClient()
    runtime = CodexGatewayRuntime(repository=repository, upstream_client=upstream)

    response = await runtime.create_response(
        gateway_key=VALID_GATEWAY_KEY,
        payload={"model": "gpt-4o"},
    )

    assert response.status_code == 403
    assert response.body["error"]["code"] == "model_not_allowed"
    assert upstream.requests == []
    repository.create_request_log.assert_awaited_once()
    assert repository.create_request_log.call_args.kwargs["policy_decision"] == "deny"


@pytest.mark.asyncio
async def test_denied_model_list_blocks_request_and_logs_denial():
    key = _key_record(denied_models=["gpt-4o"])
    account = _account_record(key.user_id)
    repository = _repository(key_record=key, account_record=account)
    upstream = FakeUpstreamClient()
    runtime = CodexGatewayRuntime(repository=repository, upstream_client=upstream)

    response = await runtime.create_response(
        gateway_key=VALID_GATEWAY_KEY,
        payload={"model": "gpt-4o"},
    )

    assert response.status_code == 403
    assert response.body["error"]["code"] == "model_denied"
    assert upstream.requests == []
    repository.create_request_log.assert_awaited_once()
    assert repository.create_request_log.call_args.kwargs["policy_decision"] == "deny"


@pytest.mark.asyncio
async def test_decrypt_failure_returns_gateway_error_and_logs_denial(monkeypatch):
    key = _key_record(allowed_models=["gpt-5.1-codex"])
    account = _account_record(key.user_id)
    repository = _repository(key_record=key, account_record=account)
    monkeypatch.setattr(
        "src.services.codex_gateway.runtime.decrypt_secret",
        lambda encrypted: (_ for _ in ()).throw(ValueError("bad token")),
    )
    runtime = CodexGatewayRuntime(
        repository=repository, upstream_client=FakeUpstreamClient()
    )

    response = await runtime.create_response(
        gateway_key=VALID_GATEWAY_KEY,
        payload={"model": "gpt-5.1-codex"},
    )

    assert response.status_code == 403
    assert response.body["error"]["code"] == "upstream_token_unavailable"
    repository.create_request_log.assert_awaited_once()
    log_kwargs = repository.create_request_log.call_args.kwargs
    assert log_kwargs["oauth_account_id"] == account.id
    assert log_kwargs["policy_decision"] == "deny"


@pytest.mark.asyncio
async def test_upstream_failure_returns_gateway_error_and_logs_metadata(monkeypatch):
    key = _key_record(allowed_models=["gpt-5.1-codex"])
    account = _account_record(key.user_id)
    repository = _repository(key_record=key, account_record=account)
    monkeypatch.setattr(
        "src.services.codex_gateway.runtime.decrypt_secret",
        lambda encrypted: f"plain::{encrypted}",
    )
    runtime = CodexGatewayRuntime(
        repository=repository, upstream_client=FailingUpstreamClient()
    )

    response = await runtime.create_response(
        gateway_key=VALID_GATEWAY_KEY,
        payload={"model": "gpt-5.1-codex"},
    )

    assert response.status_code == 502
    assert response.body["error"]["code"] == "upstream_unavailable"
    repository.create_request_log.assert_awaited_once()
    log_kwargs = repository.create_request_log.call_args.kwargs
    assert log_kwargs["provider_error_code"] == "upstream_unavailable"
    assert log_kwargs["policy_decision"] == "allow"
    assert isinstance(log_kwargs["latency_ms"], int)


@pytest.mark.asyncio
async def test_upstream_timeout_returns_gateway_error_and_logs_metadata(monkeypatch):
    key = _key_record(allowed_models=["gpt-5.1-codex"])
    account = _account_record(key.user_id)
    repository = _repository(key_record=key, account_record=account)
    monkeypatch.setattr(
        "src.services.codex_gateway.runtime.CODEX_GATEWAY_UPSTREAM_TIMEOUT_SECONDS",
        0.001,
    )
    monkeypatch.setattr(
        "src.services.codex_gateway.runtime.decrypt_secret",
        lambda encrypted: f"plain::{encrypted}",
    )
    runtime = CodexGatewayRuntime(
        repository=repository, upstream_client=SlowUpstreamClient()
    )

    response = await runtime.create_response(
        gateway_key=VALID_GATEWAY_KEY,
        payload={"model": "gpt-5.1-codex"},
    )

    assert response.status_code == 504
    assert response.body["error"]["code"] == "upstream_timeout"
    repository.create_request_log.assert_awaited_once()
    log_kwargs = repository.create_request_log.call_args.kwargs
    assert log_kwargs["provider_error_code"] == "upstream_timeout"
    assert log_kwargs["policy_decision"] == "allow"
    assert isinstance(log_kwargs["latency_ms"], int)


def test_extract_gateway_key_prefers_openai_compatible_bearer_auth():
    assert extract_gateway_key(f"Bearer {VALID_GATEWAY_KEY}", None) == VALID_GATEWAY_KEY
    assert extract_gateway_key(f"bearer {VALID_GATEWAY_KEY}", None) == VALID_GATEWAY_KEY
    assert extract_gateway_key(None, VALID_GATEWAY_KEY) == VALID_GATEWAY_KEY
    assert extract_gateway_key("Basic abc", None) is None


class ProviderErrorUpstreamClient:
    """Fake upstream that answers with a provider-side error response."""

    def __init__(self, response):
        self.response = response
        self.requests = []

    async def create_response(self, *, access_token, payload):
        await sleep(0)
        self.requests.append({"access_token": access_token, "payload": payload})
        return self.response


DECRYPTED_TOKEN_SENTINEL = "plain::encrypted-access-token"
PROVIDER_RATE_LIMIT_BODY = {
    "error": {
        "message": "Rate limit reached for requests.",
        "type": "rate_limit_error",
        "param": None,
        "code": "rate_limit_exceeded",
    }
}


@pytest.mark.asyncio
async def test_account_resolution_is_scoped_to_the_gateway_key_user(monkeypatch):
    key = _key_record(allowed_models=["gpt-5.1-codex"])
    account = _account_record(key.user_id)
    repository = _repository(key_record=key, account_record=account)
    upstream = FakeUpstreamClient()
    monkeypatch.setattr(
        "src.services.codex_gateway.runtime.decrypt_secret",
        lambda encrypted: f"plain::{encrypted}",
    )
    runtime = CodexGatewayRuntime(repository=repository, upstream_client=upstream)

    response = await runtime.create_response(
        gateway_key=VALID_GATEWAY_KEY,
        payload={"model": "gpt-5.1-codex", "input": "hello"},
    )

    assert response.status_code == 200
    repository.get_active_gateway_key_by_plaintext.assert_awaited_once_with(
        VALID_GATEWAY_KEY
    )
    repository.get_active_upstream_account_for_user.assert_awaited_once_with(
        key.user_id
    )
    assert upstream.requests[0]["access_token"] == DECRYPTED_TOKEN_SENTINEL


@pytest.mark.asyncio
async def test_wrong_user_account_fails_closed_before_upstream_dispatch(monkeypatch):
    """Defense in depth: a cross-user account never reaches the upstream client."""
    key = _key_record(allowed_models=["gpt-5.1-codex"])
    foreign_account = _account_record(uuid4())
    repository = _repository(key_record=key, account_record=foreign_account)
    upstream = FakeUpstreamClient()
    monkeypatch.setattr(
        "src.services.codex_gateway.runtime.decrypt_secret",
        lambda encrypted: f"plain::{encrypted}",
    )
    runtime = CodexGatewayRuntime(repository=repository, upstream_client=upstream)

    response = await runtime.create_response(
        gateway_key=VALID_GATEWAY_KEY,
        payload={"model": "gpt-5.1-codex"},
    )

    assert response.status_code == 403
    assert response.body["error"]["code"] == "upstream_identity_mismatch"
    assert upstream.requests == []
    repository.create_request_log.assert_awaited_once()
    log_kwargs = repository.create_request_log.call_args.kwargs
    assert log_kwargs["policy_decision"] == "deny"
    assert log_kwargs["denied_reason"] == "upstream_identity_mismatch"


@pytest.mark.asyncio
async def test_provider_error_status_and_body_pass_through_and_log_provider_code(
    monkeypatch,
):
    key = _key_record(allowed_models=["gpt-5.1-codex"])
    account = _account_record(key.user_id)
    repository = _repository(key_record=key, account_record=account)
    upstream = ProviderErrorUpstreamClient(
        CodexGatewayUpstreamResponse(
            status_code=429,
            body=PROVIDER_RATE_LIMIT_BODY,
            provider_error_code="rate_limit_exceeded",
        )
    )
    monkeypatch.setattr(
        "src.services.codex_gateway.runtime.decrypt_secret",
        lambda encrypted: f"plain::{encrypted}",
    )
    runtime = CodexGatewayRuntime(repository=repository, upstream_client=upstream)

    response = await runtime.create_response(
        gateway_key=VALID_GATEWAY_KEY,
        payload={"model": "gpt-5.1-codex"},
    )

    assert response.status_code == 429
    assert response.body == PROVIDER_RATE_LIMIT_BODY
    assert upstream.requests[0]["access_token"] == DECRYPTED_TOKEN_SENTINEL
    repository.create_request_log.assert_awaited_once()
    log_kwargs = repository.create_request_log.call_args.kwargs
    assert log_kwargs["status_code"] == 429
    assert log_kwargs["provider_error_code"] == "rate_limit_exceeded"
    assert log_kwargs["policy_decision"] == "allow"
    assert isinstance(log_kwargs["latency_ms"], int)


@pytest.mark.asyncio
async def test_secret_material_never_reaches_request_logs_or_client_response(
    monkeypatch,
):
    key = _key_record(allowed_models=["gpt-5.1-codex"])
    account = _account_record(key.user_id)
    repository = _repository(key_record=key, account_record=account)
    monkeypatch.setattr(
        "src.services.codex_gateway.runtime.decrypt_secret",
        lambda encrypted: f"plain::{encrypted}",
    )
    runtime = CodexGatewayRuntime(
        repository=repository, upstream_client=FakeUpstreamClient()
    )

    response = await runtime.create_response(
        gateway_key=VALID_GATEWAY_KEY,
        payload={"model": "gpt-5.1-codex", "input": "prompt that must not be logged"},
        source_ip="203.0.113.10",
        client_user_agent="codex-cli",
    )

    assert response.status_code == 200
    repository.create_request_log.assert_awaited_once()
    log_kwargs = repository.create_request_log.call_args.kwargs
    serialized_log = json.dumps(log_kwargs, default=str)
    serialized_response = json.dumps(response.body, default=str)
    for sensitive in (
        DECRYPTED_TOKEN_SENTINEL,
        account.encrypted_access_token,
        VALID_GATEWAY_KEY,
    ):
        assert sensitive not in serialized_log
        assert sensitive not in serialized_response
    assert "prompt that must not be logged" not in serialized_log
    metadata = log_kwargs["request_metadata"]
    assert "prompt that must not be logged" not in json.dumps(metadata, default=str)
    assert "sensitive_input_preview" not in metadata
    assert "sensitive_output_preview" not in metadata


@pytest.mark.asyncio
async def test_unconfigured_upstream_fails_closed_without_leaking_token(monkeypatch):
    key = _key_record(allowed_models=["gpt-5.1-codex"])
    account = _account_record(key.user_id)
    repository = _repository(key_record=key, account_record=account)
    monkeypatch.setattr(
        "src.services.codex_gateway.runtime.decrypt_secret",
        lambda encrypted: f"plain::{encrypted}",
    )
    runtime = CodexGatewayRuntime(repository=repository)

    response = await runtime.create_response(
        gateway_key=VALID_GATEWAY_KEY,
        payload={"model": "gpt-5.1-codex"},
    )

    assert response.status_code == 502
    assert response.body["error"]["code"] == "upstream_not_configured"
    repository.create_request_log.assert_awaited_once()
    log_kwargs = repository.create_request_log.call_args.kwargs
    assert log_kwargs["provider_error_code"] == "upstream_not_configured"
    assert log_kwargs["policy_decision"] == "allow"
    assert DECRYPTED_TOKEN_SENTINEL not in json.dumps(log_kwargs, default=str)
