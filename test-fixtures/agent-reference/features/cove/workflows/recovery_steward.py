from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from features.cove.workflows.recovery_testing import (
    _sanitize_vm_name,
    _start_cove_one_time_hyperv_restore_impl,
    inspect_cove_recovery_restore_capacity,
)
from modules import cove

from bifrost import UserError, tool, workflow


def _fingerprint(preview: dict[str, Any]) -> str:
    decision = {
        "halo_ticket_id": preview.get("halo_ticket_id"),
        "device_id": preview.get("device_id"),
        "recovery_agent_id": preview.get("recovery_agent_id"),
        "vm_name": preview.get("vm_name"),
        "vhd_path": preview.get("vhd_path"),
        "cpu_count": preview.get("cpu_count"),
        "ram_size_mb": preview.get("ram_size_mb"),
        "selected_session": preview.get("selected_session"),
        "compatibility_device": preview.get("compatibility_device"),
    }
    encoded = json.dumps(decision, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validated_ticket_vm_name(vm_name: str, halo_ticket_id: int) -> str:
    ticket_id = int(halo_ticket_id)
    if ticket_id <= 0:
        raise UserError("halo_ticket_id must be a positive Halo ticket ID.")
    safe_name = _sanitize_vm_name(vm_name)
    tokens = {token.casefold() for token in safe_name.replace("_", "-").split("-") if token}
    if str(ticket_id).casefold() not in tokens and f"t{ticket_id}".casefold() not in tokens:
        raise UserError(
            f"vm_name must include Halo ticket {ticket_id} as a delimited token (for example, Client-T{ticket_id})."
        )
    return safe_name


def _rerun_fingerprint(preview: dict[str, Any], plan_device_id: int) -> str:
    return hashlib.sha256(f"{int(plan_device_id)}:{_fingerprint(preview)}".encode()).hexdigest()


def _existing_plan_recovery_action(existing: dict[str, Any]) -> str:
    status = str(existing.get("current_recovery_status") or "").casefold()
    if existing.get("manual_rerun_available") is True:
        if status not in {"completed", "completedwitherrors", "failed"}:
            raise UserError("Only a terminal recovery plan device may be proposed for rerun.")
        return "rerun"
    if status == "unresponsive":
        boot_status = str(
            existing.get("last_recovery_boot_status") or existing.get("last_boot_test_status") or ""
        ).casefold()
        if not (boot_status.startswith(("fail", "error")) or boot_status.endswith(("failure", "failed", "error"))):
            raise UserError("An Unresponsive plan may be replaced only when Cove also reports a failed boot.")
        return "replace"
    if status in {"failed", "completedwitherrors", "cancelled", "canceled", "stopped"}:
        return "replace"
    raise UserError("Cove has not marked this recovery plan as safely rerunnable or replaceable.")


def _existing_plan(capacity: dict[str, Any], *, device_id: int, plan_device_id: int) -> dict[str, Any]:
    match = next(
        (
            row
            for row in capacity.get("recent_restores", [])
            if str(row.get("backup_cloud_device_id")) == str(int(device_id))
            and str(row.get("plan_device_id")) == str(int(plan_device_id))
        ),
        None,
    )
    if not match:
        raise UserError(f"Recovery plan device {plan_device_id} was not found for Cove device {device_id}.")
    return match


def _agent_vhd_free_bytes(agent: dict[str, Any]) -> int | None:
    reported_free = agent.get("storage_free_size_bytes")
    if reported_free is not None:
        try:
            return max(int(reported_free), 0)
        except (TypeError, ValueError):
            pass

    vhd_drive = str(agent.get("vhd_drive") or "").rstrip("\\").upper()
    partition = next(
        (
            row
            for row in (agent.get("partitions_info") or [])
            if str(row.get("partition_name") or row.get("drive") or "").rstrip("\\").upper() == vhd_drive
        ),
        None,
    )
    if not partition:
        return None
    try:
        total_gb = float(partition.get("partition_size_gb") or 0)
        used_gb = float(partition.get("partition_used_gb") or 0)
    except (TypeError, ValueError):
        return None
    return int(max(total_gb - used_gb, 0) * 1024**3)


def _new_restore_capacity_decision(
    capacity: dict[str, Any], *, recovery_agent_id: int, preview: dict[str, Any]
) -> dict[str, Any]:
    agent_id = str(int(recovery_agent_id))
    agent = next((row for row in capacity.get("agents", []) if str(row.get("recovery_agent_id")) == agent_id), None)
    if not agent:
        return {
            "eligible": False,
            "reasons": ["recovery_agent_not_found"],
            "recovery_agent_id": int(recovery_agent_id),
        }

    reasons: list[str] = []
    if str(agent.get("state") or "").casefold() != "online":
        reasons.append("recovery_agent_not_online")
    active = int(capacity.get("active_restore_count_by_agent", {}).get(agent_id, 0))
    concurrency = int(agent.get("concurrency_limit") or 0)
    if concurrency <= 0 or active >= concurrency:
        reasons.append("recovery_agent_concurrency_exhausted")
    requested_ram_gb = float(preview.get("ram_size_mb") or 0) / 1024
    if float(agent.get("ram_size_gb") or 0) < requested_ram_gb:
        reasons.append("insufficient_recovery_agent_ram")

    selected_size = int((preview.get("compatibility_device") or {}).get("selected_size") or 0)
    free_bytes = _agent_vhd_free_bytes(agent)
    if free_bytes is None:
        reasons.append("vhd_partition_capacity_unavailable")
    elif selected_size <= 0 or free_bytes < int(selected_size * 1.25):
        reasons.append("insufficient_vhd_storage_headroom")
    return {
        "eligible": not reasons,
        "reasons": reasons,
        "recovery_agent_id": int(agent_id),
        "active_restore_count": active,
        "concurrency_limit": concurrency,
        "requested_ram_gb": requested_ram_gb,
        "available_ram_gb": agent.get("ram_size_gb"),
        "selected_size_bytes": selected_size,
        "vhd_free_bytes": free_bytes,
        "minimum_storage_headroom_ratio": 1.25,
    }


def _rerun_capacity_decision(
    capacity: dict[str, Any], existing: dict[str, Any], preview: dict[str, Any]
) -> dict[str, Any]:
    return _new_restore_capacity_decision(
        capacity,
        recovery_agent_id=int(existing.get("recovery_agent_id") or 0),
        preview=preview,
    )


@tool(
    id="0760d416-be3a-4a33-b540-5b6b0658075d",
    name="recovery_steward_inspect_capacity",
    description="Read-only inventory of recovery locations and active Cove restores.",
    category="Cove Data Protection",
    tags=["cove", "recovery", "agent-tool", "read-only"],
)
async def recovery_steward_inspect_capacity() -> dict[str, Any]:
    result = await inspect_cove_recovery_restore_capacity(agent_limit=100, dashboard_limit=100)
    return {"success": True, "observed_at": datetime.now(UTC).isoformat(), "capacity": result}


@tool(
    id="93b7f115-de11-444d-8309-7aee9bac2bec",
    name="recovery_steward_preview_restore",
    description="Read-only compatibility and restore-point preview with a stable approval fingerprint.",
    category="Cove Data Protection",
    tags=["cove", "recovery", "agent-tool", "read-only", "preview"],
)
async def recovery_steward_preview_restore(
    device_id: int,
    recovery_agent_id: int,
    vm_name: str,
    halo_ticket_id: int,
    cpu_count: int = 2,
    ram_size_mb: int = 4096,
    vhd_path: str = "E:\\",
) -> dict[str, Any]:
    safe_vm_name = _validated_ticket_vm_name(vm_name, halo_ticket_id)
    result = await _start_cove_one_time_hyperv_restore_impl(
        device_id=device_id,
        recovery_agent_id=recovery_agent_id,
        vm_name=safe_vm_name,
        apply=False,
        cpu_count=cpu_count,
        ram_size_mb=ram_size_mb,
        vhd_path=vhd_path,
    )
    preview = {**result["preview"], "halo_ticket_id": int(halo_ticket_id)}
    return {
        "success": True,
        "mutation_performed": False,
        "approval_fingerprint": _fingerprint(preview),
        "preview": preview,
        "next_action": (
            "Review the existing recovery plan device before rerun or removal."
            if preview.get("device_already_in_plan")
            else "A human may run the separate approved execution workflow with this exact fingerprint."
        ),
    }


@tool(
    id="ed3b2927-f95b-4888-846b-2b72e4430cd8",
    name="recovery_steward_preview_existing_plan_rerun",
    description="Read-only revalidation for rerunning or safely replacing one failed recovery plan device.",
    category="Cove Data Protection",
    tags=["cove", "recovery", "agent-tool", "read-only", "preview", "rerun"],
)
async def recovery_steward_preview_existing_plan_rerun(
    device_id: int,
    plan_device_id: int,
    halo_ticket_id: int,
) -> dict[str, Any]:
    capacity = await inspect_cove_recovery_restore_capacity(agent_limit=100, dashboard_limit=100)
    existing = _existing_plan(capacity, device_id=device_id, plan_device_id=plan_device_id)
    recovery_action = _existing_plan_recovery_action(existing)
    preview_result = await recovery_steward_preview_restore(
        device_id=device_id,
        recovery_agent_id=int(existing["recovery_agent_id"]),
        vm_name=str(existing["recovery_target_vm_name"]),
        halo_ticket_id=halo_ticket_id,
        cpu_count=int(existing.get("recovery_target_device_number_of_cpu") or 2),
        ram_size_mb=int(existing.get("recovery_target_device_ram_size_mb") or 4096),
        vhd_path=str(existing.get("recovery_target_vhd_path") or "E:\\"),
    )
    preview = preview_result["preview"]
    capacity_decision = _rerun_capacity_decision(capacity, existing, preview)
    if not capacity_decision["eligible"]:
        raise UserError(f"Existing plan rerun is not currently eligible: {', '.join(capacity_decision['reasons'])}.")
    return {
        "success": True,
        "mutation_performed": False,
        "recovery_action": recovery_action,
        "plan_device_id": int(plan_device_id),
        "existing_plan": existing,
        "approval_fingerprint": _rerun_fingerprint(preview, plan_device_id),
        "preview": preview,
        "capacity_decision": capacity_decision,
        "next_action": (
            "An authorized operator may run the approved existing-plan recovery workflow; it will remove and recreate the failed assignment."
            if recovery_action == "replace"
            else "An authorized operator may run the approved existing-plan recovery workflow; it will rerun this assignment."
        ),
    }


@workflow(
    id="47556b97-103b-4a60-b2f3-077d02434d16",
    name="execute_approved_recovery_steward_restore",
    description="Human approval boundary for one restore; re-previews and rejects changed evidence.",
    category="Cove Data Protection",
    tags=["cove", "recovery", "approval-required", "mutation"],
)
async def execute_approved_recovery_steward_restore(
    device_id: int,
    recovery_agent_id: int,
    vm_name: str,
    halo_ticket_id: int,
    approval_fingerprint: str,
    approved_by: str,
    approval_reason: str,
    cpu_count: int = 2,
    ram_size_mb: int = 4096,
    vhd_path: str = "E:\\",
) -> dict[str, Any]:
    if not approved_by.strip() or not approval_reason.strip():
        raise UserError("approved_by and approval_reason are required.")
    preview_result = await recovery_steward_preview_restore(
        device_id=device_id,
        recovery_agent_id=recovery_agent_id,
        vm_name=vm_name,
        halo_ticket_id=halo_ticket_id,
        cpu_count=cpu_count,
        ram_size_mb=ram_size_mb,
        vhd_path=vhd_path,
    )
    actual = preview_result["approval_fingerprint"]
    if preview_result["preview"].get("device_already_in_plan"):
        raise UserError("Device is already assigned to a recovery plan; this create-only workflow will not mutate it.")
    if not approval_fingerprint or approval_fingerprint != actual:
        raise UserError("Approval fingerprint does not match current restore evidence; preview and approve again.")
    capacity = await inspect_cove_recovery_restore_capacity(agent_limit=100, dashboard_limit=100)
    capacity_decision = _new_restore_capacity_decision(
        capacity,
        recovery_agent_id=recovery_agent_id,
        preview=preview_result["preview"],
    )
    if not capacity_decision["eligible"]:
        raise UserError(
            "Recovery location is no longer eligible: "
            f"{', '.join(capacity_decision['reasons'])}. Submit a new Halo start action to select current capacity."
        )
    result = await _start_cove_one_time_hyperv_restore_impl(
        device_id=device_id,
        recovery_agent_id=recovery_agent_id,
        vm_name=_validated_ticket_vm_name(vm_name, halo_ticket_id),
        apply=True,
        cpu_count=cpu_count,
        ram_size_mb=ram_size_mb,
        vhd_path=vhd_path,
    )
    return {
        **result,
        "capacity_decision": capacity_decision,
        "approval": {
            "approved_by": approved_by,
            "reason": approval_reason,
            "fingerprint": actual,
            "halo_ticket_id": int(halo_ticket_id),
            "executed_at": datetime.now(UTC).isoformat(),
        },
    }


@workflow(
    id="da9e960e-26fd-436e-b6da-a0ad8e69fbe9",
    name="execute_approved_recovery_steward_existing_plan_rerun",
    description="Approval boundary for rerunning or safely replacing one failed recovery plan device.",
    category="Cove Data Protection",
    tags=["cove", "recovery", "approval-required", "mutation", "rerun"],
)
async def execute_approved_recovery_steward_existing_plan_rerun(
    device_id: int,
    plan_device_id: int,
    halo_ticket_id: int,
    approval_fingerprint: str,
    approved_by: str,
    approval_reason: str,
    recovery_host_vm_absent_confirmed: bool = False,
) -> dict[str, Any]:
    if not approved_by.strip() or not approval_reason.strip():
        raise UserError("approved_by and approval_reason are required.")
    if not recovery_host_vm_absent_confirmed:
        raise UserError(
            "Recovery-host VM absence must be confirmed from live host evidence before rerunning an existing plan."
        )
    proposal = await recovery_steward_preview_existing_plan_rerun(
        device_id=device_id,
        plan_device_id=plan_device_id,
        halo_ticket_id=halo_ticket_id,
    )
    actual = proposal["approval_fingerprint"]
    if not approval_fingerprint or approval_fingerprint != actual:
        raise UserError("Approval fingerprint does not match current recovery evidence; preview and approve again.")

    session_time = str(proposal["preview"]["selected_session"]["timestamp"])
    recovery_action = str(proposal["recovery_action"])
    client = await cove.get_client(scope="global")
    try:
        if recovery_action == "rerun":
            started = await client.start_hyperv_on_demand_restore(
                plan_device_id=plan_device_id,
                backup_session_time=session_time,
            )
            readback = await client.get_restore_dashboard(
                backup_cloud_device_id=device_id,
                plan_device_id=plan_device_id,
            )
            result = {
                "success": True,
                "mutation_performed": True,
                "recovery_action": recovery_action,
                "plan_device_id": int(plan_device_id),
                "backup_session_time": session_time,
                "started": started,
                "readback": readback,
            }
        else:
            excluded = await client.exclude_recovery_plan_device(plan_device_id=plan_device_id)
            removal_readback = await client.get_restore_dashboard(
                backup_cloud_device_id=device_id,
                plan_device_id=plan_device_id,
            )
            if removal_readback.get("matched_plan_device") is not None:
                raise UserError(
                    "Cove accepted the removal request but still reports the old plan; wait for dashboard convergence before recreating it."
                )
            result = {
                "success": True,
                "mutation_performed": True,
                "recovery_action": recovery_action,
                "replaced_plan_device_id": int(plan_device_id),
                "excluded": excluded,
                "removal_readback": removal_readback,
            }
    finally:
        await client.close()

    if recovery_action == "replace":
        replacement = await _start_cove_one_time_hyperv_restore_impl(
            device_id=device_id,
            recovery_agent_id=int(proposal["existing_plan"]["recovery_agent_id"]),
            vm_name=_validated_ticket_vm_name(
                str(proposal["existing_plan"]["recovery_target_vm_name"]), halo_ticket_id
            ),
            apply=True,
            cpu_count=int(proposal["existing_plan"].get("recovery_target_device_number_of_cpu") or 2),
            ram_size_mb=int(proposal["existing_plan"].get("recovery_target_device_ram_size_mb") or 4096),
            vhd_path=str(proposal["existing_plan"].get("recovery_target_vhd_path") or "E:\\"),
        )
        result.update(replacement)

    return {
        **result,
        "approval": {
            "approved_by": approved_by,
            "reason": approval_reason,
            "fingerprint": actual,
            "halo_ticket_id": int(halo_ticket_id),
            "executed_at": datetime.now(UTC).isoformat(),
            "recovery_host_vm_absent_confirmed": True,
        },
    }
