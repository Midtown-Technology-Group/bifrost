"""An undeployed audit example stays read-only and distinguishes site evidence."""

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest


@pytest.fixture
def tracker():
    root = Path(__file__).resolve().parents[1]
    bifrost = ModuleType("bifrost")
    bifrost.workflow = lambda function: function
    bifrost.tables = SimpleNamespace(query=AsyncMock(return_value=[]))
    modules = ModuleType("modules")
    modules.ninjaone = SimpleNamespace(list_devices_detaileds=AsyncMock(return_value=[]), list_network_interfaces=AsyncMock(return_value=[]))
    spec = importlib.util.spec_from_file_location("isolated_site_tracker", root / "docs/audits/fixtures/721-ninja-site-tracker.proposed.py")
    assert spec
    assert spec.loader
    value = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"bifrost": bifrost, "modules": modules}):
        spec.loader.exec_module(value)
    return value


def device(identity=1, *, org=1, loc=10, ip="192.0.2.1", server=True):
    return {"id": identity, "organizationId": org, "locationId": loc, "publicIP": ip,
            "nodeClass": "WINDOWS_SERVER" if server else "WINDOWS_WORKSTATION", "systemName": f"fixture-{identity}"}


@pytest.mark.asyncio
async def test_transport_retries_only_reads_and_propagates_terminal_or_logic_errors(tracker):
    sleep = AsyncMock()
    with patch.object(tracker.asyncio, "sleep", sleep):
        read = AsyncMock(side_effect=[httpx.ReadError("transient"), "observed"])
        assert await tracker._retry(read) == "observed"
        sleep.assert_awaited_once_with(1.0)
        for error in [httpx.ReadError("terminal"), ValueError("logic")]:
            read = AsyncMock(side_effect=error)
            with pytest.raises(type(error)):
                await tracker._retry(read, attempts=1)
            read.assert_awaited_once()
        with pytest.raises(ValueError, match="at least 1"):
            await tracker._retry(read, attempts=0)


@pytest.mark.asyncio
async def test_bounded_table_pagination_and_cloud_adapter_evidence(tracker):
    tracker.tables.query.side_effect = [
        [{"id": str(i), "data": {"baseline_ip": "192.0.2.1"}} for i in range(500)],
        SimpleNamespace(documents=[SimpleNamespace(id="last", data=None), {"data": {}}]),
    ]
    rows, calls = await tracker._load_state()
    assert len(rows) == 501
    assert rows["last"] == {}
    assert calls == 2
    assert [call.kwargs["offset"] for call in tracker.tables.query.await_args_list] == [0, 500]
    tracker.ninjaone.list_network_interfaces.return_value = {"results": [
        {"deviceId": 1, "adapterName": "Amazon Elastic Network"},
        {"deviceId": 2, "interfaceName": "Hyper-V"},
        {"adapterName": "Azure"},
    ]}
    assert await tracker._cloud_device_ids() == {1}
    tracker.ninjaone.list_devices_detaileds.return_value = None
    assert await tracker._load_devices() == []


def test_fingerprints_require_server_or_dwell_evidence_and_exclude_shared_egress(tracker):
    old = (datetime.now(UTC) - timedelta(days=20)).isoformat()
    rows = [device(), device(2, ip="192.0.2.2"), device(3, org=2, ip="192.0.2.2"),
            device(4, ip="192.0.2.3"), device(5, ip=None), device(6, org=None)]
    state = {str(i): {"baseline_ip": "192.0.2.4", "ninja_org_id": 1, "home_location_id": 10, "current_ip_since": old} for i in range(2)}
    state["invalid"] = {}
    state["other"] = {"baseline_ip": "192.0.2.9", "ninja_org_id": 9, "home_location_id": 10, "current_ip_since": old}
    result = tracker._build_fingerprints(rows, state, {4}, 7, 2, {1, 2})
    assert set(result["registry"]) == {"192.0.2.1", "192.0.2.4"}
    assert result["registry"]["192.0.2.4"]["derivation"] == ["dwell"]
    assert result["excluded_shared_ips"] == ["192.0.2.2"]
    assert tracker._age_days("invalid") is None
    assert tracker._parse_iso(None) is None
    assert tracker._parse_iso("2026-01-01T00:00:00").tzinfo is not None


@pytest.mark.parametrize("case,expected", [
    ("home", "home"), ("same-client", "moved_same_client"),
    ("other-client", "moved_different_client"), ("unknown", "roaming_unknown"),
    ("old-absence", "off_all_sites_over_threshold"),
])
def test_classification_preserves_prior_state_without_claiming_unknown_site_evidence(tracker, case, expected):
    now = datetime(2026, 10, 7, tzinfo=UTC)
    old = (now - timedelta(days=30)).isoformat()
    row = device(server=False)
    prior = {"current_ip": row["publicIP"], "current_ip_since": old, "off_all_sites_since": old, "prev_ip": "192.0.2.0"}
    registry = {} if case in {"unknown", "old-absence"} else {row["publicIP"]: {"org": 2 if case == "other-client" else 1, "sites": ["1:20" if case == "same-client" else "1:10"]}}
    if case == "unknown":
        prior = {"current_ip": "192.0.2.99"}
    classification, next_state = tracker._classify(row, prior, registry, now.isoformat(), 14, 14)
    assert classification == expected
    assert prior.get("last_classification") is None
    assert next_state["device_id"] == row["id"]
    if case == "unknown":
        assert next_state["prev_ip"] == "192.0.2.99"
        assert next_state["matched_site"] is None


@pytest.mark.asyncio
async def test_workflows_return_preview_evidence_and_write_mode_stops_before_reads(tracker):
    rows = [device(), device(2, loc=20, server=False), device(3, org=2), device(4, ip=None)]
    tracker.ninjaone.list_devices_detaileds.return_value = rows
    result = await tracker.ninja_build_site_fingerprints(org_ids=[1], detect_cloud=False)
    assert result["known_site_ip_count"] == 1
    assert result["by_derivation"] == {"server_anchor": 1}
    preview = await tracker.ninja_scan_device_site_moves(org_ids=[1], detect_cloud=False)
    assert preview["dry_run"] is True
    assert preview["devices_scanned"] == 2
    assert preview["summary"] == {"home": 1, "moved_same_client": 1}
    assert preview["notable_count"] == 1
    tracker.ninjaone.list_devices_detaileds.reset_mock()
    with pytest.raises(ValueError, match="read-only"):
        await tracker.ninja_scan_device_site_moves(dry_run=False)
    tracker.ninjaone.list_devices_detaileds.assert_not_awaited()
