"""Exact dashboard identity and ticket eligibility checks. No name matching."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

SENDER = "reports-mailer@meraki.com"
MARKER = "BIFROST_MERAKI_REPORT_ROUTING_V1"


def dashboard_key(url: str) -> tuple[str, str, str] | None:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not re.fullmatch(r"n\d+\.dashboard\.meraki\.com", host):
            return None
        if parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
        match = re.match(r"/(?:[^/]+/)?(o|n)/([A-Za-z0-9_-]+)(?:/|$)", parsed.path)
        return (host, match[1], match[2]) if match else None
    except ValueError:
        return None


class ReportLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.keys: set[tuple[str, str, str]] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                # Decode Microsoft's wrapper locally. Never fetch an email-provided URL.
                wrapper = urlsplit(value)
                if wrapper.scheme == "https" and re.fullmatch(
                    r"[a-z0-9-]+\.safelinks\.protection\.outlook\.com", wrapper.hostname or ""
                ):
                    targets = parse_qs(wrapper.query).get("url", [])
                    if len(targets) != 1:
                        continue
                    value = targets[0]
                key = dashboard_key(value)
                if key and urlsplit(value).path.endswith("/manage/security/summary"):
                    self.keys.add(key)


def report_key(ticket: dict) -> tuple[str, str, str] | None:
    parser = ReportLinks()
    parser.feed(str(ticket.get("details_html") or ""))
    return next(iter(parser.keys)) if len(parser.keys) == 1 else None


def eligibility(ticket: dict, source_client_id: int) -> str | None:
    if ticket.get("client_id") != source_client_id:
        return "customer_already_assigned"
    if (
        ticket.get("status_id") != 1
        or ticket.get("hasbeenclosed")
        or ticket.get("deleted")
        or ticket.get("merged_into_id")
    ):
        return "ticket_not_new"
    response_date = str(ticket.get("first_responsedate") or "")
    if (
        ticket.get("reviewed")
        or ticket.get("locked")
        or (response_date and not response_date.startswith(("0001-", "1900-")))
    ):
        return "ticket_already_triaged"
    if str(ticket.get("reportedby") or "").strip().lower() != SENDER:
        return "sender_not_allowed"
    if not re.fullmatch(r"MX security report for (?:organization|network) '.+' : .+", str(ticket.get("summary") or "")):
        return "unsupported_report"
    if report_key(ticket) is None:
        return "missing_or_ambiguous_dashboard_identity"
    return None


def mapped_client(meraki_org_id: str, meraki_mappings: list[dict], halo_mappings: list[dict]) -> tuple[str, int] | None:
    matches = [row for row in meraki_mappings if str(row.get("entity_id")) == meraki_org_id]
    if len(matches) != 1 or not matches[0].get("organization_id"):
        return None
    org_id = str(matches[0]["organization_id"])
    targets = [row for row in halo_mappings if str(row.get("organization_id")) == org_id]
    if len(targets) != 1:
        return None
    try:
        client_id = int(targets[0]["entity_id"])
    except (ValueError, TypeError, KeyError):
        return None
    if client_id <= 0:
        return None
    # A duplicate reverse mapping is also ambiguous customer authority.
    if sum(str(row.get("entity_id")) == str(client_id) for row in halo_mappings) != 1:
        return None
    return org_id, client_id


def ticket_fingerprint(ticket: dict) -> dict:
    return {
        key: ticket.get(key)
        for key in (
            "id",
            "client_id",
            "site_id",
            "user_id",
            "status_id",
            "agent_id",
            "team_id",
            "last_update",
            "version_id",
            "summary",
            "details_html",
            "reportedby",
            "reviewed",
            "locked",
        )
    }
