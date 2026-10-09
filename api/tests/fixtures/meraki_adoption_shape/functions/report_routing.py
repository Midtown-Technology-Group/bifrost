"""Meraki report customer routing and read-only inspection."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from modules.report_api import VendorAPI, halo_connection, halo_get
from modules.report_identity import mapped_client, report_key
from modules.report_router import resolve_report_org, route_ticket, target_identity

from bifrost import config, integrations, tables, workflow


@workflow(
    name="Meraki Reports: Inspect Ticket Routing Evidence",
    description="Read one Halo ticket and expose only Meraki link paths and routing fields.",
    category="Meraki",
    effects=[
        {"kind": "integration.read", "target": "halopsa"},
        {"kind": "integration.read", "target": "meraki"},
        {"kind": "network.read"},
    ],
    enforced_bounds={
        "max_duration_seconds": 180,
        "max_external_calls": 512,
        "max_records_read": 110000,
        "max_output_bytes": 32768,
    },
)
async def inspect_report_routing(halo_ticket_id: int) -> dict[str, Any]:
    async with asyncio.timeout(170):
        ticket = await halo_get(f"/Tickets/{int(halo_ticket_id)}", {"includedetails": "true"})
        integration = await integrations.get("Meraki")
        base, token = await halo_connection()
        async with httpx.AsyncClient(timeout=15) as client:
            meraki = VendorAPI(
                client,
                "https://api.meraki.com/api/v1",
                {"X-Cisco-Meraki-API-Key": integration.config["api_key"]},
                max_calls=101,
            )

            def mappings(values: Any) -> list[dict]:
                return [
                    row
                    if isinstance(row, dict)
                    else {"organization_id": row.organization_id, "entity_id": row.entity_id}
                    for row in values or []
                ]

            meraki_mappings = mappings(await integrations.list_mappings("Meraki"))
            org_id = await resolve_report_org(ticket, meraki, {str(row.get("entity_id")) for row in meraki_mappings})
            mapped = (
                mapped_client(org_id, meraki_mappings, mappings(await integrations.list_mappings("HaloPSA")))
                if org_id
                else None
            )
            target = (
                await target_identity(VendorAPI(client, base, {"Authorization": "Bearer " + token}), mapped[1])
                if mapped
                else None
            )
        return {
            "halo_ticket_id": halo_ticket_id,
            "meraki_org_id": org_id,
            "mapped_target": target,
            "ticket": {
                key: ticket.get(key)
                for key in (
                    "id",
                    "summary",
                    "client_id",
                    "site_id",
                    "user_id",
                    "reportedby",
                    "status_id",
                    "dateoccurred",
                    "last_update",
                )
            },
            "dashboard_identity": report_key(ticket),
        }


@workflow(
    name="Meraki Reports: Inspect Organization Identity",
    description="Read Meraki organization URLs and integration mapping coverage.",
    category="Meraki",
    effects=[{"kind": "integration.read", "target": "meraki"}, {"kind": "network.read"}],
    enforced_bounds={
        "max_duration_seconds": 60,
        "max_external_calls": 1,
        "max_records_read": 2000,
        "max_output_bytes": 262144,
    },
)
async def inspect_report_organizations() -> dict[str, Any]:
    integration = await integrations.get("Meraki")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            "https://api.meraki.com/api/v1/organizations",
            headers={"X-Cisco-Meraki-API-Key": integration.config["api_key"]},
        )
        response.raise_for_status()
    rows = response.json()
    mappings = await integrations.list_mappings("Meraki") or []
    return {
        "organizations": [{k: row.get(k) for k in ("id", "name", "url")} for row in rows],
        "mapping_count": len(mappings),
    }


@workflow(
    name="Meraki Reports: Correct Halo Ticket Customer",
    description="Correct one new Meraki report ticket only after an exact dashboard identity and unique existing customer mappings are verified. Defaults to preview.",
    category="Meraki",
    effects=[
        {"kind": "integration.read", "target": "halopsa"},
        {"kind": "integration.read", "target": "meraki"},
        {"kind": "integration.write", "target": "halopsa"},
        {"kind": "network.read"},
        {"kind": "network.write"},
    ],
    enforced_bounds={
        "max_duration_seconds": 180,
        "max_external_calls": 512,
        "max_records_read": 110000,
        "max_records_written": 4,
        "max_output_bytes": 32768,
    },
)
async def correct_report_customer(
    payload: dict[str, Any] | None = None, halo_ticket_id: int | None = None, apply: bool = False
) -> dict[str, Any]:
    """Handle the existing Halo new-ticket webhook or preview an explicit Halo ticket."""
    if payload is not None:
        if payload.get("event") != "new ticket logged":
            return {"applied": False, "reason": "unsupported_event"}
        ticket = payload.get("ticket")
        if not isinstance(ticket, dict) or not isinstance(ticket.get("id"), int):
            return {"applied": False, "reason": "missing_halo_ticket_id"}
        if halo_ticket_id is not None and halo_ticket_id != ticket["id"]:
            raise ValueError("Conflicting Halo ticket identities.")
        halo_ticket_id = ticket["id"]
        # Fast filter only; all authority comes from a fresh Halo read below.
        if str(ticket.get("reportedby") or "").lower() != "reports-mailer@meraki.com":
            return {"applied": False, "reason": "sender_not_allowed"}
    if not isinstance(halo_ticket_id, int) or isinstance(halo_ticket_id, bool) or halo_ticket_id <= 0:
        raise ValueError("A positive halo_ticket_id is required.")
    async with asyncio.timeout(170):
        source_client_id = int(await config.get("meraki_report_source_client_id", default=0))
        if source_client_id <= 0:
            raise RuntimeError("Configure meraki_report_source_client_id before enabling routing.")
        base, token = await halo_connection()
        meraki_integration = await integrations.get("Meraki")
        if not meraki_integration or not meraki_integration.config.get("api_key"):
            raise RuntimeError("Meraki integration is unavailable.")

        def mapping_rows(values: Any) -> list[dict]:
            return [
                {"organization_id": getattr(row, "organization_id", None), "entity_id": getattr(row, "entity_id", None)}
                if not isinstance(row, dict)
                else row
                for row in values or []
            ]

        meraki_mappings = mapping_rows(await integrations.list_mappings("Meraki"))
        halo_mappings = mapping_rows(await integrations.list_mappings("HaloPSA"))
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            halo = VendorAPI(client, base, {"Authorization": "Bearer " + token}, max_calls=10)
            meraki = VendorAPI(
                client,
                "https://api.meraki.com/api/v1",
                {"X-Cisco-Meraki-API-Key": meraki_integration.config["api_key"]},
                max_calls=101,
            )
            return await route_ticket(
                halo_ticket_id=halo_ticket_id,
                halo=halo,
                meraki=meraki,
                meraki_mappings=meraki_mappings,
                halo_mappings=halo_mappings,
                source_client_id=source_client_id,
                ledger=tables,
                apply=apply,
            )
