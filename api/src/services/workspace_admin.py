"""Scoped, audited domain operations used by workspace administrative workflows."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from bifrost.admin_models import (
    ExecutionRedactionResult,
    OAuthDiagnostics,
    OAuthProviderMetadata,
    OAuthReconciliationResult,
    OAuthRecoveryRequest,
    OAuthRecoveryResult,
    OAuthTokenMetadata,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.principal import UserPrincipal
from src.core.security import decrypt_secret, encrypt_secret
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.services.audit import emit_audit
from src.services.audit_context import ActorContext, current_actor
from src.services.execution.delegation_authorization import is_engine_principal
from src.services.oauth_provider import OAuthProviderClient, resolve_url_template


class AdminOperationError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


def authorize_admin(user: UserPrincipal, scope: str | UUID) -> UUID | None:
    """Engine transport authority must never become the workspace caller's grant."""
    if is_engine_principal(user):
        allowed = (
            user.delegated_user_id is not None
            and user.delegated_is_superuser
            and not user.delegated_is_external
            and user.is_active
        )
    else:
        allowed = (
            user.is_superuser
            and user.is_active
            and not user.is_external
            and not user.embed
        )
    if not allowed:
        raise AdminOperationError(403, "Platform administrator required")
    org_id = None if scope == "global" else UUID(str(scope))
    # These are explicit administrator actions on the requested target scope.
    # Global workflows inherit the caller's org as their execution context;
    # that context must not erase the original platform administrator's grant.
    return org_id


async def audit_admin(
    db: AsyncSession,
    user: UserPrincipal,
    org_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: UUID,
    details: dict | None = None,
) -> None:
    actor = current_actor() or ActorContext(user.user_id, org_id)
    actor = replace(actor, organization_id=org_id)
    if is_engine_principal(user):
        actor = replace(
            actor,
            user_id=user.delegated_user_id,
            email=user.delegated_email,
            name=user.delegated_name,
        )
    await emit_audit(
        db,
        action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        actor_override=actor,
        strict=True,
    )


async def connection(db: AsyncSession, name: str, org_id: UUID | None) -> OAuthProvider:
    # Exact scope; never use the repository's unscoped integration UUID lookup.
    try:
        identity = OAuthProvider.integration_id == UUID(name)
    except ValueError:
        identity = OAuthProvider.provider_name == name
    provider = (
        await db.execute(
            select(OAuthProvider)
            .where(
                identity,
                OAuthProvider.organization_id == org_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if provider is None and org_id is not None:
        provider = (
            await db.execute(
                select(OAuthProvider)
                .where(
                    identity,
                    OAuthProvider.organization_id.is_(None),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
    if provider is None:
        raise AdminOperationError(404, "OAuth connection not found in requested scope")
    return provider


async def token_rows(
    db: AsyncSession, provider: OAuthProvider, org_id: UUID | None
) -> list[OAuthToken]:
    return list(
        (
            await db.execute(
                select(OAuthToken)
                .where(
                    OAuthToken.provider_id == provider.id,
                    OAuthToken.organization_id == org_id,
                    OAuthToken.user_id.is_(None),
                )
                .order_by(
                    OAuthToken.expires_at.desc().nulls_last(),
                    OAuthToken.created_at.desc(),
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )


async def inspect_oauth(
    db: AsyncSession, user: UserPrincipal, name: str, scope: str | UUID
) -> OAuthDiagnostics:
    org_id = authorize_admin(user, scope)
    provider = await connection(db, name, org_id)
    tokens = await token_rows(db, provider, org_id)
    await audit_admin(
        db, user, org_id, "oauth.inspect_metadata", "oauth_provider", provider.id
    )
    return OAuthDiagnostics(
        providers=[
            OAuthProviderMetadata(
                provider_id=str(provider.id),
                organization_id=str(provider.organization_id)
                if provider.organization_id
                else None,
                flow=provider.oauth_flow_type,
                status=provider.status,
            )
        ],
        tokens=[
            OAuthTokenMetadata(
                provider_id=str(provider.id),
                provider_org_id=str(provider.organization_id)
                if provider.organization_id
                else None,
                token_id=str(token.id),
                token_org_id=str(token.organization_id)
                if token.organization_id
                else None,
                has_access_token=bool(token.encrypted_access_token),
                has_refresh_token=bool(token.encrypted_refresh_token),
                expires_at=token.expires_at.isoformat() if token.expires_at else None,
                scopes=token.scopes or [],
            )
            for token in tokens
        ],
    )


async def recover_oauth(
    db: AsyncSession, user: UserPrincipal, name: str, request: OAuthRecoveryRequest
) -> OAuthRecoveryResult:
    org_id = authorize_admin(user, request.scope)
    provider = await connection(db, name, org_id)
    if provider.oauth_flow_type != "authorization_code" or not provider.token_url:
        raise AdminOperationError(
            400, "Recovery requires authorization_code flow and a token URL"
        )
    rows = await token_rows(db, provider, org_id)
    secret = (
        decrypt_secret(provider.encrypted_client_secret.decode())
        if provider.encrypted_client_secret
        else None
    )
    token_url = resolve_url_template(
        url=provider.token_url, defaults=provider.token_url_defaults
    )
    # Codes and rotating refresh tokens are single use.
    client = OAuthProviderClient(max_retries=1)
    supplied_refresh = (
        request.refresh_token.get_secret_value() if request.refresh_token else None
    )
    if request.action == "exchange_code_without_scope":
        if not request.code or not request.redirect_uri or request.refresh_token:
            raise AdminOperationError(
                400, "Code and redirect URI required; refresh token must be omitted"
            )
        if provider.redirect_uri and request.redirect_uri != provider.redirect_uri:
            raise AdminOperationError(
                400, "Redirect URI must match the configured connection"
            )
        success, body = await client.exchange_code_for_token(
            token_url=token_url,
            code=request.code.get_secret_value(),
            client_id=provider.client_id,
            client_secret=secret,
            redirect_uri=request.redirect_uri,
            scopes=None,
        )
    else:
        if request.code is not None or request.redirect_uri is not None:
            raise AdminOperationError(
                400, "Code and redirect URI must be omitted for refresh"
            )
        if not supplied_refresh and rows and rows[0].encrypted_refresh_token:
            supplied_refresh = decrypt_secret(rows[0].encrypted_refresh_token.decode())
        if not supplied_refresh:
            raise AdminOperationError(400, "No refresh token available")
        success, body = await client.refresh_access_token(
            token_url=token_url,
            refresh_token=supplied_refresh,
            client_id=provider.client_id,
            client_secret=secret,
            audience=provider.audience,
        )
    # Provider error text can contain credentials; never return or audit it.
    if not success or not body.get("access_token"):
        raise AdminOperationError(
            502, "OAuth recovery failed; reauthorization may be required"
        )
    refresh = body.get("refresh_token") or supplied_refresh
    if not refresh:
        raise AdminOperationError(502, "OAuth exchange returned no refresh token")
    expires_at = body.get("expires_at") or datetime.now(UTC) + timedelta(hours=1)
    scopes = provider.scopes or []
    if request.action == "exchange_code_without_scope" and body.get("scope"):
        scopes = str(body["scope"]).split()
    # Update only the locked token scope, including historical duplicate rows.
    # Never let name/UUID cascade lookups choose a different provider on write.
    if not rows:
        rows = [OAuthToken(provider_id=provider.id, organization_id=org_id)]
        db.add(rows[0])
    now = datetime.now(UTC)
    encrypted_access = encrypt_secret(body["access_token"]).encode()
    encrypted_refresh = encrypt_secret(refresh).encode()
    for token in rows:
        token.encrypted_access_token = encrypted_access
        token.encrypted_refresh_token = encrypted_refresh
        token.expires_at = expires_at
        token.scopes = list(scopes)
        token.status = "completed"
        token.status_message = None
        token.last_refresh_at = now
    if provider.organization_id == org_id:
        provider.status = "completed"
        provider.status_message = None
        provider.last_token_refresh = now
    await db.flush()
    await audit_admin(
        db,
        user,
        org_id,
        "oauth.recover",
        "oauth_provider",
        provider.id,
        {"action": request.action, "token_rows_updated": len(rows)},
    )
    return OAuthRecoveryResult(
        connection_name=name,
        provider_id=str(provider.id),
        has_refresh_token=True,
        expires_at=expires_at.isoformat(),
        scope=" ".join(scopes),
    )


async def reconcile_oauth(
    db: AsyncSession, user: UserPrincipal, name: str, scope: str | UUID
) -> OAuthReconciliationResult:
    org_id = authorize_admin(user, scope)
    provider = await connection(db, name, org_id)
    rows = await token_rows(db, provider, org_id)
    source = next(
        (
            row
            for row in rows
            if row.encrypted_access_token and row.encrypted_refresh_token
        ),
        None,
    )
    if source is None:
        return OAuthReconciliationResult(provider_rows_updated=0, token_rows_updated=0)
    now = datetime.now(UTC)
    for row in rows:
        row.encrypted_access_token = source.encrypted_access_token
        row.encrypted_refresh_token = source.encrypted_refresh_token
        row.expires_at = source.expires_at
        row.scopes = list(source.scopes or [])
        row.status = "completed"
        row.status_message = None
        row.last_refresh_at = now
    if provider.organization_id == org_id:
        provider.status = "completed"
        provider.status_message = None
        provider.last_token_refresh = now
    await db.flush()
    await audit_admin(
        db,
        user,
        org_id,
        "oauth.reconcile",
        "oauth_provider",
        provider.id,
        {"source_token_id": str(source.id), "token_rows_updated": len(rows)},
    )
    return OAuthReconciliationResult(
        provider_rows_updated=int(provider.organization_id == org_id),
        token_rows_updated=len(rows),
    )


async def redact_execution(
    db: AsyncSession,
    user: UserPrincipal,
    execution_id: UUID,
    scope: str | UUID | None = None,
) -> ExecutionRedactionResult:
    org_id = authorize_admin(user, scope if scope is not None else "global")
    if scope is None:
        # Resolve scope inside the privileged action; never fetch sensitive
        # execution payloads into workspace code merely to discover its org.
        target = (
            await db.execute(
                select(Execution.organization_id)
                .where(Execution.id == execution_id)
                .with_for_update()
            )
        ).one_or_none()
        if target is None:
            raise AdminOperationError(404, "Execution not found")
        org_id = target[0]
    execution = (
        await db.execute(
            select(Execution)
            .where(
                Execution.id == execution_id,
                Execution.organization_id == org_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if execution is None:
        raise AdminOperationError(404, "Execution not found in requested scope")
    if execution.status not in {
        ExecutionStatus.SUCCESS,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMEOUT,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.COMPLETED_WITH_ERRORS,
    }:
        raise AdminOperationError(409, "Only terminal executions may be redacted")
    for field in ("parameters", "result", "variables", "execution_context"):
        setattr(execution, field, {"redacted": True})
    execution.error_message = None
    await db.flush()
    await audit_admin(
        db, user, org_id, "execution.redact_sensitive_fields", "execution", execution_id
    )
    return ExecutionRedactionResult(execution_id=str(execution_id), rows_updated=1)
