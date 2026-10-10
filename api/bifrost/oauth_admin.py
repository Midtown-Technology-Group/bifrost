"""Privileged OAuth diagnostics and recovery through the public HTTP contract."""

from urllib.parse import quote

from .admin_models import (
    OAuthDiagnostics,
    OAuthReconciliationResult,
    OAuthRecoveryResult,
)
from .client import get_client, raise_for_status_with_detail


class OAuthAdmin:
    @staticmethod
    async def inspect(
        connection_name: str, *, scope: str = "global"
    ) -> OAuthDiagnostics:
        """Inspect token presence and metadata without returning credentials."""
        response = await get_client().get(
            f"/api/oauth/connections/{quote(connection_name, safe='')}/diagnostics",
            params={"scope": scope},
        )
        raise_for_status_with_detail(response)
        return OAuthDiagnostics.model_validate(response.json())

    @staticmethod
    async def recover(
        connection_name: str,
        *,
        scope: str = "global",
        refresh_token: str | None = None,
        code: str | None = None,
        redirect_uri: str | None = None,
    ) -> OAuthRecoveryResult:
        """Recover server-side. Omit credentials to refresh the stored token.

        Code exchange deliberately omits scope. Credential inputs are never returned.
        This write is not automatically retried after an uncertain response.
        """
        from ._context import register_secret

        register_secret(refresh_token)
        register_secret(code)
        response = await get_client().post(
            f"/api/oauth/connections/{quote(connection_name, safe='')}/recover",
            json={
                "scope": scope,
                "action": "exchange_code_without_scope"
                if code is not None
                else "refresh",
                "refresh_token": refresh_token,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        raise_for_status_with_detail(response)
        return OAuthRecoveryResult.model_validate(response.json())

    @staticmethod
    async def reconcile(
        connection_name: str, *, scope: str = "global"
    ) -> OAuthReconciliationResult:
        """Reconcile duplicate token rows within one connection and exact org scope."""
        response = await get_client().post(
            f"/api/oauth/connections/{quote(connection_name, safe='')}/reconcile",
            json={"scope": scope},
        )
        raise_for_status_with_detail(response)
        return OAuthReconciliationResult.model_validate(response.json())


oauth_admin = OAuthAdmin
