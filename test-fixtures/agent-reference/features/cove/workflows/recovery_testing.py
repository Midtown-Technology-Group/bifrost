"""Cove one-time restore workflows for recovery testing."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from modules import cove

from bifrost import UserError, workflow

SENSITIVE_KEY_PARTS = ("password", "passphrase", "credential", "token", "visa", "secret", "key")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("***REDACTED***" if any(part in str(key).lower() for part in SENSITIVE_KEY_PARTS) else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _coerce_int(value: Any, *, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise UserError(f"{label} must be a numeric ID.") from exc
    if result <= 0:
        raise UserError(f"{label} must be greater than zero.")
    return result


def _storage_host(account: dict[str, Any], home_node_info: dict[str, Any], override: str | None = None) -> str | None:
    common = home_node_info.get("CommonInfo") if isinstance(home_node_info, dict) else {}
    host = (
        override
        or (common or {}).get("Host")
        or (common or {}).get("HttpGatewayHost")
        or account.get("StorageLocation")
        or account.get("StorageHost")
        or account.get("StorageNodeHost")
        or account.get("StorageNode")
        or account.get("Host")
    )
    return str(host).removesuffix(":443") if host else None


def _session_timestamp(session: dict[str, Any]) -> str | None:
    raw = (
        session.get("StartTime")
        or session.get("Timestamp")
        or session.get("Time")
        or session.get("BackupTime")
        or session.get("backup_session_time")
        or session.get("timestamp")
        or session.get("TS")
    )
    if raw in (None, ""):
        return None
    text = str(raw)
    if text.isdigit():
        return datetime.fromtimestamp(int(text), tz=UTC).isoformat().replace("+00:00", "Z")
    return text


def _unwrap_sessions(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    if isinstance(response, dict):
        sessions = response.get("sessions") or response.get("Sessions")
        if isinstance(sessions, list):
            return [item for item in sessions if isinstance(item, dict)]
        return [item for item in response.values() if isinstance(item, dict)]
    return []


def _pick_restore_session(
    sessions: list[dict[str, Any]], preferred_plugin_id: str = "FileSystem"
) -> dict[str, Any] | None:
    candidates = [
        {"timestamp": timestamp, "session": session}
        for session in sessions
        if (timestamp := _session_timestamp(session))
    ]
    candidates.sort(key=lambda item: str(item["timestamp"]), reverse=True)
    completed = [
        candidate for candidate in candidates if str(candidate["session"].get("Status") or "").casefold() == "completed"
    ]
    preferred = str(preferred_plugin_id or "").casefold()
    return (
        next(
            (
                candidate
                for candidate in completed
                if str(candidate["session"].get("PluginId") or "").casefold() == preferred
            ),
            None,
        )
        or (completed[0] if completed else None)
        or next(
            (
                candidate
                for candidate in candidates
                if str(candidate["session"].get("PluginId") or "").casefold() == preferred
            ),
            None,
        )
        or (candidates[0] if candidates else None)
    )


def _filesystem_session_from_account_statistics(account_statistics: dict[str, Any]) -> dict[str, Any] | None:
    timestamp = account_statistics.get("filesystem_last_successful_session_time")
    if timestamp in (None, ""):
        return None
    return {
        "Id": None,
        "PluginId": "FileSystem",
        "Status": "Completed",
        "StartTime": timestamp,
    }


def _compatibility_devices(response: dict[str, Any]) -> list[dict[str, Any]]:
    devices = (
        ((response.get("data") or {}).get("attributes") or {}).get("devices") if isinstance(response, dict) else []
    )
    return [device for device in devices or [] if isinstance(device, dict)]


def _device_summary(device: dict[str, Any] | None) -> dict[str, Any] | None:
    if not device:
        return None
    return {
        "device_id": device.get("device_id"),
        "device_name": device.get("device_name"),
        "machine_name": device.get("machine_name"),
        "partner_id": device.get("partner_id"),
        "partner_name": device.get("partner_name"),
        "selected_size": device.get("selected_size"),
        "is_compatible": device.get("is_compatible"),
        "backup_location_matches_recovery_region": device.get("backup_location_matches_recovery_region"),
        "has_device_password": bool(device.get("device_password")),
    }


def _resource_attrs(item: dict[str, Any]) -> dict[str, Any]:
    attrs = item.get("attributes") if isinstance(item, dict) else {}
    return attrs if isinstance(attrs, dict) else {}


def _recovery_agent_summary(item: dict[str, Any]) -> dict[str, Any]:
    attrs = _resource_attrs(item)
    relationships = item.get("relationships") if isinstance(item, dict) else {}
    agent_rel = (relationships or {}).get("recovery_agent") or (relationships or {}).get("agent")
    agent_rel_data = (agent_rel or {}).get("data") if isinstance(agent_rel, dict) else {}
    recovery_agent_id = (
        attrs.get("recovery_agent_id")
        or attrs.get("agent_id")
        or attrs.get("id")
        or (agent_rel_data or {}).get("id")
        or item.get("id")
    )
    return {
        "id": item.get("id"),
        "recovery_agent_id": recovery_agent_id,
        "name": attrs.get("name") or attrs.get("agent_name"),
        "state": attrs.get("agent_state") or attrs.get("state"),
        "partner_id": attrs.get("partner_id"),
        "partner_name": attrs.get("partner_name"),
        "materialized_path": attrs.get("materialized_path") or attrs.get("partner_materialized_path"),
        "storage_status": attrs.get("storage_status"),
        "storage_free_size_bytes": attrs.get("storage_free_size_bytes"),
        "storage_total_size_bytes": attrs.get("storage_total_size_bytes"),
        "last_seen_timestamp": attrs.get("last_seen_timestamp") or attrs.get("last_activity_timestamp"),
        "last_activity_time": attrs.get("last_activity_time"),
        "concurrency_limit": attrs.get("concurrency_limit"),
        "assigned_devices_number": attrs.get("assigned_devices_number"),
        "ram_size_gb": attrs.get("ram_size_gb"),
        "cpu_info": attrs.get("cpu_info"),
        "vhd_drive": attrs.get("vhd_drive"),
        "vhd_path": attrs.get("vhd_path"),
        "vhd_drive_used_size_percentage": attrs.get("vhd_drive_used_size_percentage"),
        "partitions_info": attrs.get("partitions_info"),
        "raw_attribute_keys": sorted(attrs.keys()),
    }


def _dashboard_restore_summary(item: dict[str, Any]) -> dict[str, Any]:
    attrs = _resource_attrs(item)
    return {
        "id": item.get("id"),
        "plan_device_id": attrs.get("plan_device_id"),
        "recovery_agent_id": attrs.get("agent_id"),
        "recovery_agent_name": attrs.get("recovery_agent_name"),
        "recovery_agent_state": attrs.get("recovery_agent_state"),
        "backup_cloud_device_id": attrs.get("backup_cloud_device_id"),
        "backup_cloud_device_name": attrs.get("backup_cloud_device_name"),
        "backup_cloud_device_machine_name": attrs.get("backup_cloud_device_machine_name"),
        "backup_cloud_partner_id": attrs.get("backup_cloud_partner_id"),
        "backup_cloud_partner_name": attrs.get("backup_cloud_partner_name"),
        "current_recovery_status": attrs.get("current_recovery_status"),
        "last_recovery_status": attrs.get("last_recovery_status"),
        "last_recovery_boot_status": attrs.get("last_recovery_boot_status"),
        "last_boot_test_status": attrs.get("last_boot_test_status"),
        "failover_state": attrs.get("failover_state"),
        "failover_vm_state": attrs.get("failover_vm_state"),
        "last_recovery_errors_count": attrs.get("last_recovery_errors_count"),
        "last_recovery_restored_files_count": attrs.get("last_recovery_restored_files_count"),
        "last_recovery_restored_size_bytes": attrs.get("last_recovery_restored_size_bytes"),
        "last_recovery_restored_size_user": attrs.get("last_recovery_restored_size_user"),
        "last_recovery_selected_files_count": attrs.get("last_recovery_selected_files_count"),
        "last_recovery_selected_size_bytes": attrs.get("last_recovery_selected_size_bytes"),
        "last_recovery_selected_size_user": attrs.get("last_recovery_selected_size_user"),
        "last_recovery_duration_sec": attrs.get("last_recovery_duration_sec"),
        "last_recovery_duration_user": attrs.get("last_recovery_duration_user"),
        "recovery_session_progress": attrs.get("recovery_session_progress"),
        "manual_rerun_available": attrs.get("manual_rerun_available"),
        "last_backup_session_timestamp": attrs.get("last_backup_session_timestamp"),
        "start_restore_timestamp": attrs.get("start_restore_timestamp"),
        "complete_restore_timestamp": attrs.get("complete_restore_timestamp"),
        "recovery_target_vm_name": attrs.get("recovery_target_vm_name"),
        "recovery_target_hyperv_settings_vm_name": attrs.get("recovery_target_hyperv_settings_vm_name"),
        "recovery_target_device_number_of_cpu": attrs.get("recovery_target_device_number_of_cpu"),
        "recovery_target_device_ram_size_mb": attrs.get("recovery_target_device_ram_size_mb"),
        "recovery_target_vhd_path": attrs.get("recovery_target_vhd_path"),
        "raw_attribute_keys": sorted(attrs.keys()),
    }


def _history_event_summary(item: dict[str, Any]) -> dict[str, Any]:
    attrs = _resource_attrs(item)
    return {
        "id": item.get("id"),
        "event_type": attrs.get("event_type"),
        "event_timestamp": attrs.get("event_timestamp"),
        "recovery_agent_id": attrs.get("recovery_agent_id"),
        "backup_cloud_device_id": attrs.get("backup_cloud_device_id"),
        "backup_cloud_device_name": attrs.get("backup_cloud_device_name"),
        "backup_cloud_partner_name": attrs.get("backup_cloud_partner_name"),
        "plan_device_id": attrs.get("plan_device_id"),
        "recovery_target_vm_name": attrs.get("recovery_target_vm_name"),
        "message": attrs.get("message") or attrs.get("description") or attrs.get("error_message"),
        "status": attrs.get("status"),
        "raw_attributes": attrs,
    }


@workflow(
    id="443ff14d-ca9e-4f0f-9200-b2981c69f05a",
    name="inspect_cove_recovery_restore_capacity",
    description="Read DRAAS recovery agents and active restore dashboard rows before starting one-time restores.",
    category="Cove Data Protection",
    tags=["cove", "recovery", "restore", "draas", "capacity", "read-only"],
)
async def inspect_cove_recovery_restore_capacity(
    root_partner_id: int | None = None,
    states: list[str] | None = None,
    agent_limit: int = 100,
    dashboard_limit: int = 100,
) -> dict[str, Any]:
    """Read recovery-location agents and recent restore dashboard rows."""

    client = await cove.get_client(scope="global")
    try:
        agents_payload = await client.list_recovery_agents(
            partner_id=root_partner_id,
            states=states or ["ONLINE", "OFFLINE", "STORAGE_NOT_CONFIGURED"],
            limit=max(1, min(int(agent_limit or 100), 500)),
        )
        dashboard_payload = await client.get_restore_dashboard(
            root_partner_id=root_partner_id,
            limit=max(1, min(int(dashboard_limit or 100), 500)),
        )
    finally:
        await client.close()

    agents = [_recovery_agent_summary(item) for item in agents_payload.get("data", [])]
    restores = [_dashboard_restore_summary(item) for item in dashboard_payload.get("data", [])]
    active_statuses = {"Scheduled", "InProgress", "Running", "Starting", "Queued", "Restoring"}
    active_restores = [
        row
        for row in restores
        if str(row.get("current_recovery_status") or "") in active_statuses
        or row.get("complete_restore_timestamp") in (None, "")
    ]
    active_by_agent: dict[str, int] = {}
    for row in active_restores:
        agent_id = str(row.get("recovery_agent_id") or "")
        if agent_id:
            active_by_agent[agent_id] = active_by_agent.get(agent_id, 0) + 1

    return {
        "success": True,
        "read_only": True,
        "agent_count": len(agents),
        "restore_row_count": len(restores),
        "active_restore_count": len(active_restores),
        "active_restore_count_by_agent": active_by_agent,
        "agents": agents,
        "active_restores": active_restores,
        "recent_restores": restores,
    }


@workflow(
    id="8b44c889-9057-40af-8b4d-433a694defaa",
    name="inspect_cove_recovery_agent_history",
    description="Read recent DRAAS recovery-agent history events for restore troubleshooting.",
    category="Cove Data Protection",
    tags=["cove", "recovery", "restore", "draas", "history", "read-only"],
)
async def inspect_cove_recovery_agent_history(
    recovery_agent_id: int,
    device_id: int | None = None,
    plan_device_id: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Read recent DRAAS recovery-agent history and optionally filter locally."""

    safe_agent_id = _coerce_int(recovery_agent_id, label="recovery_agent_id")
    client = await cove.get_client(scope="global")
    try:
        payload = await client.list_recovery_agent_history(
            safe_agent_id,
            limit=max(1, min(int(limit or 100), 500)),
        )
    finally:
        await client.close()

    events = [_history_event_summary(item) for item in payload.get("data", [])]
    filtered = events
    if device_id is not None:
        filtered = [event for event in filtered if str(event.get("backup_cloud_device_id")) == str(int(device_id))]
    if plan_device_id is not None:
        filtered = [event for event in filtered if str(event.get("plan_device_id")) == str(int(plan_device_id))]

    return {
        "success": True,
        "read_only": True,
        "recovery_agent_id": safe_agent_id,
        "event_count": len(events),
        "filtered_count": len(filtered),
        "events": filtered,
    }


DEFAULT_SMALL_RESTORE_CANDIDATES = [
    {
        "device_id": 5164628,
        "label": "Hiple-DCVM",
        "company": "Hiple Family Dentistry",
        "halo_last_verification": "2025-11-14T00:00:00",
    },
    {
        "device_id": 3140686,
        "label": "AdvancedDental-DC1",
        "company": "Advanced Dental",
        "halo_last_verification": "1999-01-01T00:00:00",
    },
    {
        "device_id": 3130258,
        "label": "Bluffton-WCM-DC2016",
        "company": "Bluffton Dental Clinic",
        "halo_last_verification": "1999-01-01T00:00:00",
    },
    {
        "device_id": 3386367,
        "label": "Blast-SERVER",
        "company": "Blast MEDIA",
        "halo_last_verification": "1999-01-01T00:00:00",
    },
    {
        "device_id": 5320118,
        "label": "Welch-GFD-Server",
        "company": "Bryan T. Welch DDS",
        "halo_last_verification": "2022-03-07T00:00:00",
    },
    {
        "device_id": 3472769,
        "label": "Hovey-SERVER2022",
        "company": "Cynthia L. Hovey DDS",
        "halo_last_verification": "2022-03-15T00:00:00",
    },
    {
        "device_id": 3133627,
        "label": "AdvancedDental-PHFServer",
        "company": "Advanced Dental",
        "halo_last_verification": "1999-01-01T00:00:00",
    },
    {
        "device_id": 3140713,
        "label": "NewSmile-SERVER2019",
        "company": "A New Smile Family Dentistry",
        "halo_last_verification": "2025-09-19T00:00:00",
    },
    {
        "device_id": 4638555,
        "label": "Adamas-PMServer",
        "company": "Adamas Healthcare Consulting",
        "halo_last_verification": "1999-01-01T00:00:00",
    },
]


DEFAULT_RECOVERY_RESTORE_LOCATIONS = [
    {"dashboard_agent_id": 45261, "recovery_agent_id": 45663, "name": "509A4C5529D6", "vhd_path": "D:\\"},
    {"dashboard_agent_id": 45262, "recovery_agent_id": 45664, "name": "MTG-BACKUP", "vhd_path": "E:\\"},
    {"dashboard_agent_id": 45254, "recovery_agent_id": 45656, "name": "T130-SPARE", "vhd_path": "F:\\"},
]


def _sanitize_vm_name(value: str) -> str:
    chars = [char if char.isalnum() or char in {"-", "_"} else "-" for char in str(value or "")]
    name = "".join(chars).strip("-_")
    return name[:80] or "Cove-restore-test"


def _active_restore_agent_tokens(active_restores: list[dict[str, Any]]) -> set[str]:
    tokens: set[str] = set()
    for row in active_restores:
        for key in ("recovery_agent_id", "recovery_agent_name"):
            value = row.get(key)
            if value not in (None, ""):
                tokens.add(str(value).casefold())
    return tokens


@workflow(
    id="f12f2337-2d0e-41e2-9944-ed5453118dfd",
    name="start_cove_one_time_restore_fanout",
    description="Legacy read-only fanout preview; apply is retired in favor of ticket-bound Recovery Steward approval.",
    category="Cove Data Protection",
    tags=["cove", "recovery", "restore", "draas", "hyperv", "fanout", "read-only", "legacy"],
)
async def start_cove_one_time_restore_fanout(
    recovery_locations: list[dict[str, Any]] | None = None,
    candidate_devices: list[dict[str, Any]] | None = None,
    apply: bool = False,
    max_selected_size_bytes: int = 120_000_000_000,
    vm_name_suffix: str | None = None,
    cpu_count: int = 2,
    ram_size_mb: int = 4096,
    vhd_path: str = "E:\\",
) -> dict[str, Any]:
    """Start at most one small restore per idle recovery location from inside Bifrost."""

    if apply:
        raise UserError(
            "Direct fanout apply is retired. Preview here, then use the ticket-bound Cove Recovery Steward "
            "approval workflow for one restore at a time."
        )

    capacity = await inspect_cove_recovery_restore_capacity(agent_limit=100, dashboard_limit=100)
    active_tokens = _active_restore_agent_tokens(capacity["active_restores"])
    suffix = vm_name_suffix or datetime.now(UTC).strftime("%Y%m%d")
    locations = recovery_locations or DEFAULT_RECOVERY_RESTORE_LOCATIONS
    candidates = candidate_devices or DEFAULT_SMALL_RESTORE_CANDIDATES
    used_devices: set[int] = set()
    selected: list[dict[str, Any]] = []
    skipped_locations: list[dict[str, Any]] = []

    for location in locations:
        recovery_agent_id = _coerce_int(location.get("recovery_agent_id"), label="recovery_agent_id")
        dashboard_agent_id = _coerce_int(
            location.get("dashboard_agent_id") or recovery_agent_id,
            label="dashboard_agent_id",
        )
        location_name = str(location.get("name") or recovery_agent_id)
        location_vhd_path = str(location.get("vhd_path") or vhd_path)
        if (
            str(recovery_agent_id).casefold() in active_tokens
            or str(dashboard_agent_id).casefold() in active_tokens
            or location_name.casefold() in active_tokens
        ):
            skipped_locations.append(
                {
                    "recovery_agent_id": recovery_agent_id,
                    "recovery_agent_name": location_name,
                    "reason": "active_restore_already_present",
                }
            )
            continue

        best: dict[str, Any] | None = None
        failures: list[dict[str, Any]] = []
        for candidate in candidates:
            device_id = _coerce_int(candidate.get("device_id"), label="candidate device_id")
            if device_id in used_devices:
                continue
            label = str(candidate.get("label") or candidate.get("company") or device_id)
            vm_name = _sanitize_vm_name(f"{label}-{location_name}-restore-test-{suffix}")
            try:
                preview = await _start_cove_one_time_hyperv_restore_impl(
                    device_id=device_id,
                    recovery_agent_id=dashboard_agent_id,
                    vm_name=vm_name,
                    apply=False,
                    cpu_count=cpu_count,
                    ram_size_mb=ram_size_mb,
                    vhd_path=location_vhd_path,
                )
            except Exception as exc:
                failures.append(
                    {
                        "device_id": device_id,
                        "label": label,
                        "error": str(exc),
                    }
                )
                continue

            preview_body = preview["preview"]
            selected_size = int((preview_body.get("compatibility_device") or {}).get("selected_size") or 0)
            if selected_size > int(max_selected_size_bytes):
                failures.append(
                    {
                        "device_id": device_id,
                        "label": label,
                        "selected_size": selected_size,
                        "error": "selected_size_exceeds_limit",
                    }
                )
                continue
            row = {
                "candidate": candidate,
                "device_id": device_id,
                "label": label,
                "recovery_agent_id": recovery_agent_id,
                "dashboard_agent_id": dashboard_agent_id,
                "recovery_agent_name": location_name,
                "vm_name": vm_name,
                "selected_size": selected_size,
                "selected_session": preview_body.get("selected_session"),
                "preview": preview_body,
            }
            if best is None or selected_size < int(best.get("selected_size") or 0):
                best = row

        if not best:
            skipped_locations.append(
                {
                    "recovery_agent_id": recovery_agent_id,
                    "recovery_agent_name": location_name,
                    "reason": "no_compatible_small_candidate",
                    "failures": failures[:10],
                }
            )
            continue

        used_devices.add(int(best["device_id"]))
        if apply:
            started = await _start_cove_one_time_hyperv_restore_impl(
                device_id=int(best["device_id"]),
                recovery_agent_id=recovery_agent_id,
                vm_name=str(best["vm_name"]),
                apply=True,
                cpu_count=cpu_count,
                ram_size_mb=ram_size_mb,
                vhd_path=location_vhd_path,
            )
            best["restore"] = started
        selected.append(best)

    return _redact(
        {
            "success": True,
            "apply": bool(apply),
            "capacity_execution": capacity,
            "selected_count": len(selected),
            "skipped_location_count": len(skipped_locations),
            "selected": selected,
            "skipped_locations": skipped_locations,
        }
    )


async def _start_cove_one_time_hyperv_restore_impl(
    device_id: int,
    recovery_agent_id: int,
    vm_name: str,
    apply: bool = False,
    compatibility_search: str | None = None,
    root_partner_id: int | None = None,
    notification_emails: list[str] | None = None,
    cpu_count: int = 2,
    ram_size_mb: int = 4096,
    vhd_path: str = "E:\\",
    preferred_plugin_id: str = "FileSystem",
    storage_host_override: str | None = None,
) -> dict[str, Any]:
    """Preview or start one Cove Hyper-V on-demand restore."""

    safe_device_id = _coerce_int(device_id, label="device_id")
    safe_agent_id = _coerce_int(recovery_agent_id, label="recovery_agent_id")
    safe_vm_name = str(vm_name or "").strip()
    if not safe_vm_name:
        raise UserError("vm_name is required.")

    client = await cove.get_client(scope="global")
    try:
        account_bundle = await client.get_account_info_for_restore(safe_device_id)
        account = account_bundle["account"]
        inventory_account = account_bundle.get("inventory_account") or {}
        home_node_info = account_bundle["home_node_info"]
        account_name = str(
            account.get("Name") or account.get("AccountName") or account.get("DeviceName") or safe_device_id
        )
        search = compatibility_search or account_name

        compatibility = await client.search_restore_compatible_devices(
            recovery_agent_id=safe_agent_id,
            search=search,
            partner_id=root_partner_id,
        )
        devices = _compatibility_devices(compatibility)
        compatible_device = next(
            (device for device in devices if str(device.get("device_id")) == str(safe_device_id)), None
        )
        if not compatible_device:
            raise UserError(
                f"Cove device {safe_device_id} was not returned by the DRAAS compatibility search '{search}'."
            )
        if not compatible_device.get("device_password"):
            raise UserError(f"Cove device {safe_device_id} did not include a DRAAS device_password.")

        storage_host = _storage_host(account, home_node_info, storage_host_override)
        statistics_session = _filesystem_session_from_account_statistics(account_bundle.get("account_statistics") or {})
        if statistics_session:
            sessions = [statistics_session]
            session_source = "management_account_statistics"
        else:
            if not storage_host:
                raise UserError(f"Cove device {safe_device_id} did not expose a storage host for session enumeration.")
            session_token = inventory_account.get("Token") or account.get("Token") or account.get("Password")
            if not session_token:
                raise UserError(f"Cove device {safe_device_id} did not expose a storage session token.")
            sessions_response = await client.enumerate_storage_sessions(
                token=str(session_token),
                password=str(account.get("Password") or "") or None,
                account=account_name,
                storage_host=storage_host,
                account_id=safe_device_id,
            )
            sessions = _unwrap_sessions(sessions_response)
            session_source = "storage_reporting_service"
        selected_session = _pick_restore_session(sessions, preferred_plugin_id=preferred_plugin_id)
        if not selected_session:
            raise UserError(f"No usable backup session was found for Cove device {safe_device_id}.")

        preview = {
            "device_id": safe_device_id,
            "account_name": account_name,
            "storage_host": storage_host,
            "recovery_agent_id": safe_agent_id,
            "vm_name": safe_vm_name,
            "cpu_count": int(cpu_count),
            "ram_size_mb": int(ram_size_mb),
            "vhd_path": vhd_path,
            "compatibility_search": search,
            "compatibility_candidate_count": len(devices),
            "compatibility_device": _device_summary(compatible_device),
            "selected_session": {
                "timestamp": selected_session["timestamp"],
                "plugin_id": selected_session["session"].get("PluginId"),
                "status": selected_session["session"].get("Status"),
                "id": selected_session["session"].get("Id") or selected_session["session"].get("ID"),
                "start_time": selected_session["session"].get("StartTime"),
                "end_time": selected_session["session"].get("EndTime"),
                "source": session_source,
            },
            "session_candidate_count": len(sessions),
            "has_draas_device_password": bool(compatible_device.get("device_password")),
            "will_generate_credentials_on_apply": True,
        }

        dashboard = await client.get_restore_dashboard(
            root_partner_id=root_partner_id,
            backup_cloud_device_id=safe_device_id,
            limit=100,
        )
        existing_plan_devices = [
            _dashboard_restore_summary(item)
            for item in dashboard.get("data", [])
            if str(_resource_attrs(item).get("backup_cloud_device_id")) == str(safe_device_id)
        ]
        preview["existing_plan_devices"] = existing_plan_devices
        preview["device_already_in_plan"] = bool(existing_plan_devices)

        if not apply:
            result = {
                "success": True,
                "apply": False,
                "would_create_restore": not existing_plan_devices,
                "preview": preview,
            }
            del account_bundle, account, home_node_info, compatibility, devices
            del compatible_device, sessions, selected_session
            return result

        if existing_plan_devices:
            existing_ids = ", ".join(
                str(item.get("plan_device_id") or item.get("id")) for item in existing_plan_devices
            )
            raise UserError(
                f"Cove device {safe_device_id} is already assigned to recovery plan device(s) {existing_ids}; "
                "review the existing plan and use its supported rerun or removal path before creating another."
            )

        passphrase = await client.generate_reinstallation_passphrase(safe_device_id)
        created = await client.create_hyperv_on_demand_restore_device(
            device_id=safe_device_id,
            device_name=str(compatible_device.get("device_name") or account_name),
            device_password=str(compatible_device["device_password"]),
            credentials=passphrase,
            recovery_agent_id=safe_agent_id,
            vm_name=safe_vm_name,
            vhd_path=vhd_path,
            cpu_count=int(cpu_count),
            ram_size_mb=int(ram_size_mb),
            notification_emails=notification_emails or [],
        )
        plan_device_id = ((created.get("data") or {}).get("id")) if isinstance(created, dict) else None
        if not plan_device_id:
            raise UserError("Cove restore plan device creation did not return a plan device ID.")

        started = await client.start_hyperv_on_demand_restore(
            plan_device_id=plan_device_id,
            backup_session_time=str(selected_session["timestamp"]),
        )
        dashboard = await client.get_restore_dashboard(
            root_partner_id=root_partner_id,
            backup_cloud_device_id=safe_device_id,
            plan_device_id=plan_device_id,
        )
        result = _redact(
            {
                "success": True,
                "apply": True,
                "preview": preview,
                "created_plan_device_id": str(plan_device_id),
                "created": created,
                "started": started,
                "readback": dashboard,
            }
        )
        del account_bundle, account, home_node_info, compatibility, devices
        del compatible_device, sessions, selected_session, passphrase
        del created, started, dashboard
    finally:
        await client.close()

    return result


@workflow(
    id="34cc1a77-d8e3-4ad2-9e36-cd4b590b94e7",
    name="start_cove_one_time_hyperv_restore",
    description="Legacy single-device restore preview; apply is retired in favor of Recovery Steward approval.",
    category="Cove Data Protection",
    tags=["cove", "recovery", "restore", "draas", "hyperv", "testing", "read-only", "legacy"],
)
async def start_cove_one_time_hyperv_restore(
    device_id: int,
    recovery_agent_id: int,
    vm_name: str,
    apply: bool = False,
    compatibility_search: str | None = None,
    root_partner_id: int | None = None,
    notification_emails: list[str] | None = None,
    cpu_count: int = 2,
    ram_size_mb: int = 4096,
    vhd_path: str = "E:\\",
    preferred_plugin_id: str = "FileSystem",
    storage_host_override: str | None = None,
) -> dict[str, Any]:
    """Preview or start one Cove Hyper-V on-demand restore."""

    if apply:
        raise UserError(
            "Direct restore apply is retired. Use Cove Recovery Steward: Preview Restore and "
            "Cove Recovery Steward: Execute Approved Restore with a Halo ticket ID."
        )

    return await _start_cove_one_time_hyperv_restore_impl(
        device_id=device_id,
        recovery_agent_id=recovery_agent_id,
        vm_name=vm_name,
        apply=apply,
        compatibility_search=compatibility_search,
        root_partner_id=root_partner_id,
        notification_emails=notification_emails,
        cpu_count=cpu_count,
        ram_size_mb=ram_size_mb,
        vhd_path=vhd_path,
        preferred_plugin_id=preferred_plugin_id,
        storage_host_override=storage_host_override,
    )
