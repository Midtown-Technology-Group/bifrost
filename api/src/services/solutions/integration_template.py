"""Build a secret-scrubbed, portable skeleton of an Integration for a Solution
connection declaration. Carries the fill-out shape (config schema, OAuth provider
shape, data provider) but NEVER client_id/client_secret/tokens/mappings/org ids.
"""

from __future__ import annotations

from typing import Any

# Safe OAuthProvider fields to carry. Everything not listed is dropped — in
# particular client_id, encrypted_client_secret, organization_id, status*,
# tokens, last_token_refresh.
_SAFE_OAUTH_FIELDS = (
    "provider_name",
    "display_name",
    "oauth_flow_type",
    "authorization_url",
    "token_url",
    "audience",
    "token_url_defaults",
    "entity_id_source",
    "scopes",
    "redirect_uri",
)
_SAFE_TEMPLATE_FIELDS = (
    "name",
    "entity_id_name",
    "default_entity_id",
    "data_provider_id",
)
_SAFE_CONFIG_FIELDS = ("key", "type", "required", "description", "options", "position")


def scrub_integration_template(template: dict[str, Any]) -> dict[str, Any]:
    """Persisted declarations receive the same allowlist as a fresh capture."""
    if not isinstance(template, dict):
        raise TypeError("Integration template must be an object")
    schemas = template.get("config_schema") or []
    oauth = template.get("oauth")
    if not isinstance(schemas, list) or any(
        not isinstance(item, dict) for item in schemas
    ):
        raise ValueError("Integration config schema must contain objects")
    if oauth is not None and not isinstance(oauth, dict):
        raise ValueError("Integration OAuth template must be an object")
    return {
        **{
            key: value
            for key, value in template.items()
            if key in _SAFE_TEMPLATE_FIELDS
        },
        "config_schema": [
            {key: value for key, value in item.items() if key in _SAFE_CONFIG_FIELDS}
            for item in schemas
        ],
        "oauth": None
        if oauth is None
        else {key: value for key, value in oauth.items() if key in _SAFE_OAUTH_FIELDS},
    }


def build_integration_template(integration: Any) -> dict[str, Any]:
    config_schema = [
        {
            "key": s.key,
            "type": s.type,
            "required": bool(s.required),
            "description": s.description,
            "options": s.options,
            "position": s.position,
        }
        for s in (integration.config_schema or [])
    ]
    oauth = None
    prov = getattr(integration, "oauth_provider", None)
    if prov is not None:
        oauth = {f: getattr(prov, f, None) for f in _SAFE_OAUTH_FIELDS}
    return {
        "name": integration.name,
        "entity_id_name": getattr(integration, "entity_id_name", None),
        "default_entity_id": getattr(integration, "default_entity_id", None),
        "data_provider_id": (
            str(integration.list_entities_data_provider_id)
            if getattr(integration, "list_entities_data_provider_id", None)
            else None
        ),
        "config_schema": config_schema,
        "oauth": oauth,
    }
