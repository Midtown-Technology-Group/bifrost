"""JSON contracts for privileged domain operations; no backend dependencies."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class ScopedAdminRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["global"] | UUID


class OAuthRecoveryRequest(ScopedAdminRequest):
    action: Literal["refresh", "exchange_code_without_scope"]
    refresh_token: SecretStr | None = None
    code: SecretStr | None = None
    redirect_uri: str | None = None


class OAuthRecoveryResult(BaseModel):
    connection_name: str
    provider_id: str
    has_refresh_token: bool
    expires_at: str
    scope: str


class OAuthProviderMetadata(BaseModel):
    provider_id: str
    organization_id: str | None
    flow: str
    status: str


class OAuthTokenMetadata(BaseModel):
    provider_id: str
    provider_org_id: str | None
    token_id: str
    token_org_id: str | None
    has_access_token: bool
    has_refresh_token: bool
    expires_at: str | None
    scopes: list[str] = Field(default_factory=list)


class OAuthDiagnostics(BaseModel):
    providers: list[OAuthProviderMetadata]
    tokens: list[OAuthTokenMetadata]


class OAuthReconciliationResult(BaseModel):
    provider_rows_updated: int
    token_rows_updated: int


class ExecutionRedactionResult(BaseModel):
    execution_id: str
    rows_updated: int
    redacted: bool = True
