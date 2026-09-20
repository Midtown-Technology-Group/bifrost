"""Azure App Service managed-identity subject-token adapter for Google WIF.

The Azure Identity SDK owns the App Service endpoint/header contract and token
refresh behavior. This executable wrapper translates its token into the
response schema consumed by google.auth.default().
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

RESOURCE_ENV = "BIFROST_GOOGLE_WIF_AZURE_RESOURCE"
CLIENT_ID_ENV = "AZURE_CLIENT_ID"


class AppServiceIdentityError(RuntimeError):
    """The App Service identity endpoint did not yield a usable token."""


@dataclass(frozen=True)
class AppServiceToken:
    token: str
    expiration_time: int | None = None


def fetch_appservice_token(
    *,
    resource: str,
    client_id: str | None = None,
) -> AppServiceToken:
    """Acquire an Entra token for the configured Google federation resource."""
    if not os.environ.get("IDENTITY_ENDPOINT", "").strip():
        raise AppServiceIdentityError("IDENTITY_ENDPOINT is not set")
    if not os.environ.get("IDENTITY_HEADER", "").strip():
        raise AppServiceIdentityError("IDENTITY_HEADER is not set")
    if not resource.strip():
        raise AppServiceIdentityError("managed-identity resource is not set")

    selected_client_id = client_id or os.environ.get(CLIENT_ID_ENV, "").strip() or None
    if not selected_client_id:
        raise AppServiceIdentityError('managed-identity client ID is not set')
    try:
        from azure.identity import ManagedIdentityCredential

        with ManagedIdentityCredential(client_id=selected_client_id) as credential:
            access_token = credential.get_token(resource.strip().rstrip("/") + "/.default")
    except Exception as exc:
        raise AppServiceIdentityError("managed-identity token request failed") from exc

    token = str(getattr(access_token, "token", "") or "")
    if not token:
        raise AppServiceIdentityError("managed-identity response had no access token")
    expires_on = getattr(access_token, "expires_on", None)
    try:
        expiration_time = int(expires_on)
    except (TypeError, ValueError) as exc:
        raise AppServiceIdentityError("managed-identity response had invalid expiry") from exc
    return AppServiceToken(token=token, expiration_time=expiration_time)


def executable_response(token: AppServiceToken) -> dict[str, object]:
    """Build the Google external-account executable-source response."""
    response: dict[str, object] = {
        "version": 1,
        "success": True,
        "token_type": "urn:ietf:params:oauth:token-type:jwt",
        "id_token": token.token,
    }
    if token.expiration_time is not None:
        response["expiration_time"] = token.expiration_time
    return response


def main() -> int:
    """Emit the executable-source response expected by google-auth."""
    try:
        token = fetch_appservice_token(resource=os.environ.get(RESOURCE_ENV, ""))
        print(json.dumps(executable_response(token), separators=(",", ":")))
        return 0
    except AppServiceIdentityError as exc:
        print(json.dumps({"version": 1, "success": False, "code": "identity_endpoint_error", "message": str(exc)}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    sys.exit(main())




