"""Resolve a report against current vendor inventory and existing org mappings."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from html import escape
from typing import Any

from modules.report_api import VendorAPI, rows
from modules.report_identity import MARKER, dashboard_key, eligibility, mapped_client, report_key, ticket_fingerprint

LEDGER = "meraki_report_routing_ledger"
MAX_NETWORK_ORGANIZATIONS = 100
NETWORK_LOOKUP_CONCURRENCY = 4


async def resolve_report_org(ticket: dict, meraki: VendorAPI, mapped_org_ids: set[str] | None = None) -> str | None:
    key = report_key(ticket)
    if key is None:
        return None
    organizations = rows(await meraki.request("GET", "/organizations"), "organizations")
    if len(organizations) > 1000:
        raise RuntimeError("Meraki organization inventory exceeds limit.")
    if key[1] == "o":
        matches = [str(org["id"]) for org in organizations if dashboard_key(str(org.get("url") or "")) == key]
    else:
        # Dashboard shard narrows reads; only exact network URL identity authorizes a match.
        candidates = [
            org
            for org in organizations
            if (mapped_org_ids is None or str(org["id"]) in mapped_org_ids)
            and (dashboard_key(str(org.get("url") or "")) or (None,))[0] == key[0]
        ]
        if len(candidates) > MAX_NETWORK_ORGANIZATIONS:
            raise RuntimeError(
                f"Dashboard shard has {len(candidates)} mapped organizations; "
                f"network lookup limit is {MAX_NETWORK_ORGANIZATIONS}. No ticket changed."
            )
        semaphore = asyncio.Semaphore(NETWORK_LOOKUP_CONCURRENCY)

        async def find_network(org: dict) -> list[str]:
            async with semaphore:
                networks = rows(
                    await meraki.request("GET", f"/organizations/{org['id']}/networks", params={"perPage": 1000}),
                    "networks",
                )
            if len(networks) >= 1000:
                raise RuntimeError("Network inventory may be incomplete.")
            found = []
            for network in networks:
                if dashboard_key(str(network.get("url") or "")) == key:
                    if str(network.get("organizationId")) != str(org["id"]):
                        raise RuntimeError("Network organization identity conflict.")
                    found.append(str(org["id"]))
            return found

        # Every candidate must finish. TaskGroup cancels outstanding reads on
        # failure, so an early match never authorizes a partial-inventory write.
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(find_network(org)) for org in candidates]
        matches = [org_id for task in tasks for org_id in task.result()]
    return matches[0] if len(matches) == 1 else None


async def target_identity(halo: VendorAPI, client_id: int) -> dict:
    client = await halo.request("GET", f"/Client/{client_id}")
    if client.get("id") != client_id or client.get("inactive") is not False:
        raise RuntimeError("Target Halo client is not verifiably active.")
    sites = rows(
        await halo.request(
            "GET", "/Site", params={"client_id": client_id, "includeinactive": "false", "page_size": 500, "page_no": 1}
        ),
        "sites",
    )
    main_site_id = client.get("main_site_id")
    primary = [
        s
        for s in sites
        if s.get("client_id") == client_id
        and s.get("inactive") is not True
        and (s.get("id") == main_site_id if main_site_id else str(s.get("name") or "").casefold() == "primary address")
    ]
    if len(primary) != 1:
        raise RuntimeError("Target client needs one active configured main site or unique Primary Address site.")
    site_id = primary[0]["id"]
    users = rows(
        await halo.request(
            "GET",
            "/Users",
            params={
                "client_id": client_id,
                "site_id": site_id,
                "includeinactive": "false",
                "page_size": 500,
                "page_no": 1,
            },
        ),
        "users",
    )
    general = [
        u
        for u in users
        if u.get("site_id") == site_id
        and u.get("inactive") is not True
        and str(u.get("name") or "").casefold() == "general user"
    ]
    if len(general) != 1:
        raise RuntimeError("Target client needs exactly one General User at its primary site.")
    return {"client_id": client_id, "site_id": site_id, "user_id": general[0]["id"]}


async def route_ticket(
    *,
    halo_ticket_id: int,
    halo: VendorAPI,
    meraki: VendorAPI,
    meraki_mappings: list[dict],
    halo_mappings: list[dict],
    source_client_id: int,
    ledger: Any,
    apply: bool = False,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(UTC)
    ticket = await halo.request("GET", f"/Tickets/{halo_ticket_id}", params={"includedetails": "true"})
    if ticket.get("id") != halo_ticket_id:
        raise RuntimeError("Halo ticket identity mismatch.")
    # Halo's ticket GET can omit reportedby. The original inbound action is authoritative.
    actions = rows(
        await halo.request("GET", "/Actions", params={"ticket_id": halo_ticket_id, "page_size": 50, "page_no": 1}),
        "actions",
    )
    first = [
        a
        for a in actions
        if a.get("id") == 1 and a.get("ticket_id") == halo_ticket_id and a.get("outcome") == "First User Email"
    ]
    if len(first) != 1 or len(actions) >= 50:
        return {
            "ticket_reference": {"system": "halopsa", "id": str(halo_ticket_id)},
            "applied": False,
            "reason": "missing_original_email",
        }
    sender = str(first[0].get("emailfrom") or "").strip().lower()
    ticket["reportedby"] = sender
    reason = eligibility(ticket, source_client_id)
    result = {"ticket_reference": {"system": "halopsa", "id": str(halo_ticket_id)}, "applied": False, "reason": reason}
    if reason:
        return result
    org_id = await resolve_report_org(ticket, meraki, {str(row.get("entity_id")) for row in meraki_mappings})
    mapped = mapped_client(org_id, meraki_mappings, halo_mappings) if org_id else None
    if mapped is None:
        return {**result, "reason": "missing_or_ambiguous_mapping"}
    bifrost_org_id, client_id = mapped
    if client_id == source_client_id:
        return {**result, "reason": "already_correct_customer"}
    target = await target_identity(halo, client_id)
    result.update(reason="exact_match", target=target, bifrost_org_id=bifrost_org_id, meraki_org_id=org_id)
    if not apply:
        return result
    reservation = "halopsa:" + str(halo_ticket_id)
    prior = await ledger.get(LEDGER, reservation)
    if prior is not None:
        return {**result, "reason": "already_reserved_or_processed"}
    # Unique insert serializes duplicate webhooks. A failed/uncertain write remains reserved.
    await ledger.insert(
        LEDGER,
        {
            "state": "reserved",
            "ticket_reference": result["ticket_reference"],
            "before": {k: ticket.get(k) for k in ("client_id", "site_id", "user_id")},
            "target": target,
            "created_at": now.isoformat(),
        },
        id=reservation,
    )
    fresh = await halo.request("GET", f"/Tickets/{halo_ticket_id}", params={"includedetails": "true"})
    fresh["reportedby"] = sender
    if ticket_fingerprint(fresh) != ticket_fingerprint(ticket) or eligibility(fresh, source_client_id):
        return {**result, "reason": "ticket_changed_before_apply"}
    payload = {"id": halo_ticket_id, **target, "_dont_fire_automations": True, "sendemail": False, "send_survey": False}
    # Preserve Halo's version token when present; do not disable its update validation.
    if fresh.get("version_id") is not None:
        payload["version_id"] = fresh["version_id"]
    await halo.request("POST", "/Tickets", json=[payload])
    after = await halo.request("GET", f"/Tickets/{halo_ticket_id}")
    if any(after.get(k) != v for k, v in target.items()):
        raise RuntimeError("Halo customer correction readback failed; reservation retained.")
    await halo.request(
        "POST",
        "/Actions",
        json=[
            {
                "ticket_id": halo_ticket_id,
                "outcome": "Private Note",
                "hiddenfromuser": True,
                "sendemail": False,
                "send_survey": False,
                "_dont_fire_automations": True,
                "note_html": f"<p>{MARKER}: Customer corrected using exact Meraki dashboard identity and existing integration mappings. Previous customer: {int(source_client_id)}. New customer: {int(client_id)}. Meraki organization: {escape(str(org_id))}. Assigned to the customer's primary site and General User; report retained.</p>",
            }
        ],
    )
    await ledger.update(LEDGER, reservation, {"state": "verified", "verified_at": datetime.now(UTC).isoformat()})
    return {**result, "applied": True, "reason": "verified"}
