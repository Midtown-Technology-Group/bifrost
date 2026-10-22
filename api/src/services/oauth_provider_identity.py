"""Conservative identity checks for reusing global OAuth providers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class OAuthProviderIdentityConflict(ValueError):
    """A global provider cannot be safely reused for another integration."""


OAUTH_IDENTITY_FIELDS = (
    "oauth_flow_type",
    "authorization_url",
    "token_url",
    "audience",
    "token_url_defaults",
    "entity_id_source",
    "scopes",
    "redirect_uri",
    "provider_metadata",
)

OAUTH_PORTABLE_FIELDS = ("display_name", *OAUTH_IDENTITY_FIELDS)


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _comparable(field: str, value: Any) -> Any:
    if field == "scopes" and isinstance(value, list):
        return tuple(sorted(value))
    return value


def assert_oauth_provider_reusable(
    provider: Any,
    desired: Mapping[str, Any],
    *,
    provider_name: str,
    integration_name: str,
) -> None:
    """Reject bound or structurally incompatible natural-key reuse.

    Empty fields on either side are compatible: a portable declaration may omit
    environment-owned details, and an empty shell may need those details filled.
    Credentials are intentionally absent from ``desired`` and are never compared.
    """
    if provider.integration_id is not None:
        raise OAuthProviderIdentityConflict(
            f"Global OAuth provider {provider_name!r} is already bound to another "
            f"integration and cannot be attached to {integration_name!r}"
        )

    for field in OAUTH_IDENTITY_FIELDS:
        if field not in desired:
            continue
        current_value = getattr(provider, field, None)
        desired_value = desired[field]
        if _is_blank(current_value) or _is_blank(desired_value):
            continue
        if _comparable(field, current_value) != _comparable(field, desired_value):
            raise OAuthProviderIdentityConflict(
                f"Global OAuth provider {provider_name!r} has incompatible "
                f"{field!r} configuration and cannot be attached to "
                f"{integration_name!r}"
            )


def merged_portable_oauth_values(
    provider: Any, desired: Mapping[str, Any]
) -> dict[str, Any]:
    """Fill blank portable fields while preserving configured provider values."""
    merged: dict[str, Any] = {}
    for field in OAUTH_PORTABLE_FIELDS:
        if field not in desired:
            continue
        current_value = getattr(provider, field, None)
        desired_value = desired[field]
        merged[field] = (
            current_value
            if not _is_blank(current_value) or _is_blank(desired_value)
            else desired_value
        )
    return merged
