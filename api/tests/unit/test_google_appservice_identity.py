"""Contract tests for the App Service Google WIF executable adapter."""

import sys
import types
from typing import ClassVar

import pytest
from src.services import google_appservice_identity as adapter


class _AccessToken:
    token = "opaque-test-token"
    expires_on = 1700000000


class _Credential:
    instances: ClassVar[list] = []

    def __init__(self, *, client_id=None):
        self.client_id = client_id
        self.scopes = []
        self.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get_token(self, scope):
        self.scopes.append(scope)
        return _AccessToken()


def _fake_identity_sdk(monkeypatch):
    _Credential.instances = []
    monkeypatch.setitem(
        sys.modules,
        "azure.identity",
        types.SimpleNamespace(ManagedIdentityCredential=_Credential),
    )


def test_fetch_uses_sdk_with_resource_scope_and_uami_selector(monkeypatch):
    _fake_identity_sdk(monkeypatch)
    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://169.254.129.16:41741/MSI/token")
    monkeypatch.setenv("IDENTITY_HEADER", "rotating-header")

    token = adapter.fetch_appservice_token(
        resource="api://google-federation-resource/",
        client_id="uami-client-id",
    )

    credential = _Credential.instances[0]
    assert credential.client_id == "uami-client-id"
    assert credential.scopes == ["api://google-federation-resource/.default"]
    assert token == adapter.AppServiceToken("opaque-test-token", 1700000000)


def test_missing_appservice_markers_reject_before_sdk_construction(monkeypatch):
    _fake_identity_sdk(monkeypatch)
    monkeypatch.delenv("IDENTITY_ENDPOINT", raising=False)
    monkeypatch.delenv("IDENTITY_HEADER", raising=False)

    with pytest.raises(adapter.AppServiceIdentityError, match="IDENTITY_ENDPOINT"):
        adapter.fetch_appservice_token(resource="api://resource")
    assert _Credential.instances == []


def test_missing_uami_selector_rejects_before_sdk_construction(monkeypatch):
    _fake_identity_sdk(monkeypatch)
    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://169.254.129.16/MSI/token")
    monkeypatch.setenv("IDENTITY_HEADER", "rotating-header")
    monkeypatch.delenv(adapter.CLIENT_ID_ENV, raising=False)

    with pytest.raises(adapter.AppServiceIdentityError, match="client ID"):
        adapter.fetch_appservice_token(resource="api://resource")
    assert _Credential.instances == []


def test_sdk_failure_is_normalized_without_token_output(monkeypatch, capsys):
    class FailingCredential(_Credential):
        def get_token(self, scope):
            raise RuntimeError("provider detail must stay chained")

    monkeypatch.setitem(
        sys.modules,
        "azure.identity",
        types.SimpleNamespace(ManagedIdentityCredential=FailingCredential),
    )
    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://169.254.129.16/MSI/token")
    monkeypatch.setenv("IDENTITY_HEADER", "rotating-header")

    with pytest.raises(adapter.AppServiceIdentityError, match="token request failed"):
        adapter.fetch_appservice_token(resource="api://resource", client_id="uami-client-id")
    assert capsys.readouterr().out == ""


def test_executable_response_has_google_schema_and_never_logs_token(capsys):
    result = adapter.executable_response(
        adapter.AppServiceToken("jwt-token", expiration_time=1700000000)
    )
    assert result == {
        "version": 1,
        "success": True,
        "token_type": "urn:ietf:params:oauth:token-type:jwt",
        "id_token": "jwt-token",
        "expiration_time": 1700000000,
    }
    assert capsys.readouterr().out == ""
