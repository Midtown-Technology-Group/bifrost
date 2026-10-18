"""The permission catalog: every permission domain with where and how the
platform checks it, derived from the access list.

Titles, names, areas and guidance live on ``PERMISSION_DOMAINS``; what the routes
actually check (actions, scope, enforcement) comes from ``ACCESS_LIST`` so
the catalog cannot drift from it.
"""

from __future__ import annotations

from src.models.contracts.access_list import AccessEntry, CurrentGate
from src.models.contracts.permissions import (
    PERMISSION_DOMAINS,
    PRIVILEGED_PERMISSIONS,
    PermissionCatalogEntry,
    domain_actions,
    domain_display_names,
    parse_permission,
)
from src.services.access_list import ACCESS_LIST


# These dedicated MTG services load current database grants and decide the
# actual target via bridge_permission_allowed. Their dependency floor stays
# authenticated/none (control-key alternatives exist); it must not be relabeled
# as the generic route evaluator. Keep this exact route registry, rather than
# claiming every future route naming a bridge permission is already enforced.
_BRIDGE_ROW_CHECKS = {
    ("POST", "/api/agents/{agent_id}/promote"): "agents.promote",
    **{key: "devices.readwrite" for key in (
        ("POST", "/api/devices"), ("GET", "/api/devices"),
        ("GET", "/api/devices/{device_id}"),
        ("POST", "/api/devices/{device_id}/rotate-key"),
        ("POST", "/api/devices/{device_id}/disable"),
        ("POST", "/api/devices/{device_id}/enable"),
        ("POST", "/api/device-control-keys"), ("GET", "/api/device-control-keys"),
        ("GET", "/api/device-control-keys/{key_id}"),
        ("POST", "/api/device-control-keys/{key_id}/rotate"),
        ("POST", "/api/device-control-keys/{key_id}/revoke"),
    )},
    **{key: "devices.execute" for key in (
        ("POST", "/api/devices/{device_id}/jobs"),
        ("GET", "/api/devices/{device_id}/jobs"),
        ("GET", "/api/devices/{device_id}/jobs/{job_id}"),
        ("GET", "/api/devices/{device_id}/jobs/{job_id}/logs"),
        ("POST", "/api/devices/{device_id}/jobs/{job_id}/cancel"),
        ("POST", "/api/devices/{device_id}/peer-sessions"),
        ("POST", "/api/devices/{device_id}/peer-sessions/{session_job_id}/revoke"),
        ("GET", "/api/devices/{device_id}/peer-sessions/{session_job_id}/revoke/{job_id}"),
    )},
}


def _action_of(permission: str) -> str:
    parsed = parse_permission(permission)
    return f"{parsed.action}.all" if parsed.extended else parsed.action


def build_catalog(access_list: list[AccessEntry] = ACCESS_LIST) -> list[PermissionCatalogEntry]:
    """One entry per permission domain, sorted by area then title.

    Every permission an entry names counts, including one that only widens a
    personal or own-agent entry to other people's items: a role can hold it.
    """
    entries_by_domain: dict[str, list[AccessEntry]] = {}
    for entry in access_list:
        if entry.permission is None:
            continue
        domain = parse_permission(entry.permission).domain
        entries_by_domain.setdefault(domain, []).append(entry)

    catalog: list[PermissionCatalogEntry] = []
    for domain, info in PERMISSION_DOMAINS.items():
        entries = entries_by_domain.get(domain, [])
        privileged = sorted(
            p for p in PRIVILEGED_PERMISSIONS if parse_permission(p).domain == domain
        )
        actions = {_action_of(e.permission) for e in entries if e.permission is not None}
        actions.update(_action_of(p) for p in privileged)
        boundaries = {e.boundary for e in entries}
        if boundaries == {"organization"}:
            scope = "per_organization"
        elif boundaries == {"platform"}:
            scope = "platform_wide"
        else:
            scope = "varies"
        catalog.append(
            PermissionCatalogEntry(
                domain=domain,
                title=info.title,
                area=info.area,
                description=info.description,
                who_should_hold=info.who_should_hold,
                actions=sorted(actions, key=domain_actions(domain).index),
                names=domain_display_names(domain),
                privileged=privileged,
                scope=scope,
                enforced=any(
                    e.current_gate == CurrentGate.EVALUATOR
                    or _BRIDGE_ROW_CHECKS.get(e.key) == e.permission
                    for e in entries
                ),
            )
        )
    return sorted(catalog, key=lambda c: (c.area, c.title))
