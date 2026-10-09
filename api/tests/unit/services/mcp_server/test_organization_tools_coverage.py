from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.tools import organizations


def _context(*, admin: bool = True) -> SimpleNamespace:
    return SimpleNamespace(is_platform_admin=admin)


@pytest.mark.asyncio
async def test_list_organizations_formats_rest_rows_and_reports_errors():
    org = {"id": str(uuid4()), "name": "Alpha", "domain": "alpha", "is_active": False}
    with patch.object(organizations, "call_rest", AsyncMock(return_value=(200, [org]))) as call:
        result = await organizations.list_organizations(_context())
    assert result.structured_content == {"organizations": [org], "count": 1}
    assert call.await_args.args[1:] == ("GET", "/api/organizations")
    with patch.object(organizations, "call_rest", AsyncMock(return_value=(503, {"detail": "unavailable"}))):
        failed = await organizations.list_organizations(_context())
    assert "HTTP 503" in failed.structured_content["error"]
    assert failed.structured_content["body"] == {"detail": "unavailable"}


@pytest.mark.asyncio
async def test_get_organization_validates_identifiers_and_preserves_rest_details():
    missing = await organizations.get_organization(_context())
    bad = await organizations.get_organization(_context(), organization_id="bad")
    assert "Either organization_id or domain" in missing.structured_content["error"]
    assert "Invalid organization_id" in bad.structured_content["error"]
    org = {"id": str(uuid4()), "name": "Midtown", "domain": "midtown", "settings": {"theme": "default"}}
    with patch.object(organizations, "call_rest", AsyncMock(return_value=(200, [org]))):
        found = await organizations.get_organization(_context(), domain="midtown")
        absent = await organizations.get_organization(_context(), domain="missing")
    assert found.structured_content == org
    assert "Organization not found" in absent.structured_content["error"]
    with patch.object(organizations, "call_rest", AsyncMock(return_value=(200, org))) as call:
        found = await organizations.get_organization(_context(), organization_id=org["id"])
    assert found.structured_content == org
    assert call.await_args.args[1:] == ("GET", f"/api/organizations/{org['id']}")
    with patch.object(organizations, "call_rest", AsyncMock(return_value=(404, {"detail": "missing"}))):
        absent = await organizations.get_organization(_context(), organization_id=org["id"])
    assert "Organization not found" in absent.structured_content["error"]


@pytest.mark.asyncio
async def test_create_organization_validates_and_generates_domain():
    missing = await organizations.create_organization(_context(), "")
    long_name = await organizations.create_organization(_context(), "x" * 256)
    long_domain = await organizations.create_organization(_context(), "Valid", domain="x" * 256)
    assert missing.structured_content["error"] == "name is required"
    assert "255 characters" in long_name.structured_content["error"]
    assert "255 characters" in long_domain.structured_content["error"]
    org = {"id": str(uuid4()), "name": "Midtown Technology Group", "domain": "midtown-technology-group"}
    with patch.object(organizations, "call_rest", AsyncMock(return_value=(201, org))) as call:
        created = await organizations.create_organization(_context(), org["name"])
    assert created.structured_content == {"success": True, **org}
    assert call.await_args.kwargs["json_body"] == {"name": org["name"], "domain": org["domain"]}
    with patch.object(organizations, "call_rest", AsyncMock(return_value=(409, {"detail": "already exists"}))):
        duplicate = await organizations.create_organization(_context(), "Existing", domain="existing")
    assert "HTTP 409" in duplicate.structured_content["error"]
    assert duplicate.structured_content["body"]["detail"] == "already exists"


@pytest.mark.asyncio
async def test_organization_tools_deny_regular_users_before_rest():
    with patch.object(organizations, "call_rest", AsyncMock()) as call:
        for result in [
            await organizations.list_organizations(_context(admin=False)),
            await organizations.get_organization(_context(admin=False), domain="midtown"),
            await organizations.create_organization(_context(admin=False), "Midtown"),
        ]:
            assert "Platform administrator privileges" in result.structured_content["error"]
        call.assert_not_awaited()


def test_ref_error_payload_shapes_known_ref_errors():
    from bifrost.refs import AmbiguousRefError, RefNotFoundError

    ambiguous = organizations._ref_error_payload(
        AmbiguousRefError("org", "midtown", [{"id": "1"}])
    )
    missing = organizations._ref_error_payload(RefNotFoundError("org", "missing"))
    generic = organizations._ref_error_payload(RuntimeError("boom"))

    assert ambiguous == {
        "kind": "org",
        "value": "midtown",
        "candidates": [{"id": "1"}],
    }
    assert missing == {"kind": "org", "value": "missing"}
    assert generic == {"detail": "boom"}


@pytest.mark.asyncio
async def test_update_and_delete_require_admin_and_refs():
    non_admin = _context(admin=False)

    denied_update = await organizations.update_organization(non_admin, "midtown")
    denied_delete = await organizations.delete_organization(non_admin, "midtown")
    missing_update = await organizations.update_organization(_context(), "")
    missing_delete = await organizations.delete_organization(_context(), "")

    assert "Platform administrator privileges" in denied_update.structured_content["error"]
    assert "Platform administrator privileges" in denied_delete.structured_content["error"]
    assert missing_update.structured_content["error"] == "organization_ref is required"
    assert missing_delete.structured_content["error"] == "organization_ref is required"
