"""
Cove Data Protection (N-able Backup) API client.

Authentication: reseller partner name + API username + API password
Protocol: JSON-RPC (not REST!)
Default base URL: https://api.backup.management/jsonapi
Docs: https://documentation.n-able.com/covedataprotection/USERGUIDE/documentation/Content/service-management/json-api/home.htm

Supported config on the "Cove Data Protection" integration:
- partner_name: reseller partner name passed to Login.partner
- username: API username
- password: API password
- base_url: optional endpoint override; accepts either the host root or /jsonapi
- cove_username / cove_password: tolerated legacy aliases for username/password

Note: This is a JSON-RPC API, not REST. All requests POST to /jsonapi.
The visa token is a top-level field on every request/response body (not inside
params). Many responses have a double-nested result: body.result.result - this
module unwraps that.
"""

from __future__ import annotations

import contextlib
import uuid
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit

import httpx

# ---------------------------------------------------------------------------
# Column code mapping for EnumerateAccountStatistics
# Full reference: https://documentation.n-able.com/covedataprotection/USERGUIDE/documentation/Content/service-management/json-api/API-column-codes.htm
# ---------------------------------------------------------------------------

COLUMN_CODES: dict[str, str] = {
    # Primary device properties
    "I0": "device_id",
    "I1": "device_name",
    "I2": "device_name_alias",
    "I3": "password",
    "I4": "creation_date",
    "I5": "expiration_date",
    "I8": "customer",
    "I9": "product_id",
    "I10": "product",
    "I15": "email",
    "I39": "retention_units",
    "I54": "profile_id",
    # Installation details
    "I16": "os_version",
    "I17": "client_version",
    "I18": "computer_name",
    "I19": "internal_ips",
    "I21": "mac_address",
    "I24": "time_offset",
    "I32": "os_type",
    "I44": "computer_manufacturer",
    "I45": "computer_model",
    "I46": "installation_id",
    "I47": "installation_mode",
    "I74": "unattended_account_id",
    "I75": "first_installation_flag",
    # Storage info
    "I11": "storage_location",
    "I14": "used_storage_bytes",
    "I26": "cabinet_storage_efficiency",
    "I27": "total_cabinets_count",
    "I28": "efficient_cabinets_0_25",
    "I29": "efficient_cabinets_26_50",
    "I30": "efficient_cabinets_50_75",
    "I31": "used_virtual_storage_bytes",
    "I36": "storage_status",
    # Feature usage
    "I78": "active_data_sources",
    "I33": "seeding_mode",
    "I35": "lsv_enabled",
    "I37": "lsv_status",
    # Data source statistics (F codes)
    "F00": "last_session_status",
    "F01": "last_session_selected_count",
    "F02": "last_session_processed_count",
    "F03": "last_session_selected_size_bytes",
    "F04": "last_session_processed_size_bytes",
    "F05": "last_session_sent_size_bytes",
    "F06": "last_session_errors_count",
    "F07": "protected_size_bytes",
    "F08": "color_bar_28_days",
    "F09": "last_successful_session_time",
    "F10": "pre_recent_selected_count",
    "F11": "pre_recent_selected_size_bytes",
    "F12": "session_duration_seconds",
    "F13": "last_session_license_items_count",
    "F14": "retention",
    "F15": "last_session_time",
    "F16": "last_successful_session_status",
    "F17": "last_completed_session_status",
    "F18": "last_completed_session_time",
    "F19": "last_session_verification_data",
    "F20": "last_session_user_mailboxes_count",
    "F21": "last_session_shared_mailboxes_count",
    # Datasource-qualified session evidence used for restore-point selection.
    "D01F09": "filesystem_last_successful_session_time",
    # Company information
    "I63": "company_name",
    "I64": "address",
    "I65": "zip_code",
    "I66": "country",
    "I67": "city",
    "I68": "phone_number",
    "I69": "fax_number",
    "I70": "contract_name",
    "I71": "group_name",
    "I72": "demo",
    "I73": "edu",
    "I76": "max_allowed_version",
    # Miscellaneous
    "I6": "last_backup_time",
    "I12": "device_group_name",
    "I13": "own_user_name",
    "I20": "external_ips",
    "I22": "dashboard_frequency",
    "I23": "dashboard_language",
    "I34": "anti_crypto_enabled",
    "I38": "archived_size",
    "I40": "activity_description",
    "I41": "hyper_v_vm_count",
    "I42": "esx_vm_count",
    "I43": "encryption_status",
    "I48": "restore_email",
    "I49": "restore_dashboard_frequency",
    "I50": "restore_dashboard_language",
    "I55": "profile_version",
    "I56": "profile",
    "I57": "sku",
    "I58": "sku_previous_month",
    "I59": "account_type",
    "I60": "proxy_type",
    "I62": "most_recent_restore_plugin",
    "I77": "customer_reference",
    "I80": "recovery_testing",
    "I81": "physicality",
    "I82": "has_passphrase",
}

# Columns to request by default for enumerate_devices - covers the most useful fields
DEFAULT_DEVICE_COLUMNS = [
    "I0",  # device_id
    "I1",  # device_name
    "I18",  # computer_name
    "I4",  # creation_date
    "I6",  # last_backup_time
    "I14",  # used_storage_bytes
    "I16",  # os_version
    "I32",  # os_type (1=workstation, 2=server)
    "I78",  # active_data_sources
    "I36",  # storage_status
    "I35",  # lsv_enabled
    "I80",  # recovery_testing
    "F00",  # last_session_status
    "F09",  # last_successful_session_time
    "F06",  # last_session_errors_count
]

PROFILE_DATASOURCE_ALIASES: dict[str, tuple[str, ...]] = {
    "filesystem": ("ServerFileSystem", "WorkstationFileSystem"),
    "serverfilesystem": ("ServerFileSystem",),
    "workstationfilesystem": ("WorkstationFileSystem",),
    "systemstate": ("SystemState",),
    "networkshares": ("NetworkShares",),
    "mssql": ("MsSql",),
    "vssmssql": ("MsSql",),
    "exchange": ("Exchange",),
    "vmware": ("VMWare",),
    "vsshyperv": ("HyperV",),
    "hyperv": ("HyperV",),
    "sharepoint": ("SharePoint",),
    "vsssharepoint": ("SharePoint",),
    "oracle": ("Oracle",),
    "mysql": ("MySql",),
}


def flatten_settings(settings: list[dict] | None) -> dict:
    """
    Convert Cove's Settings array into a flat dict with readable keys.

    Input:  [{"I1": "my-pc"}, {"I14": "1234567"}]
    Output: {"device_name": "my-pc", "used_storage_bytes": "1234567"}

    Unknown codes are passed through as-is.
    """
    if not settings:
        return {}
    result = {}
    for entry in settings:
        for code, value in entry.items():
            key = COLUMN_CODES.get(code, code)
            result[key] = value
    return result


def _unwrap(raw: Any) -> Any:
    """
    Unwrap Cove's double-nested result structure.

    _rpc_call returns body["result"], but many endpoints wrap their actual
    payload in a second "result" key: {"result": [...], "totalStatistics": null}
    """
    if isinstance(raw, dict) and "result" in raw:
        return raw["result"]
    return raw


def _split_exclusion_filter(value: str | None) -> list[str]:
    """Split Cove's pipe-delimited exclusion filter string into masks."""
    if value in (None, ""):
        return []
    return [item.strip() for item in str(value).split("|") if item.strip()]


def _join_exclusion_filter(filters: list[str] | None) -> str | None:
    """Join exclusion filter masks into Cove's pipe-delimited string."""
    normalized: list[str] = []
    seen: set[str] = set()
    for item in filters or []:
        text = str(item or "").strip()
        if not text:
            continue
        folded = text.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        normalized.append(text)
    if not normalized:
        return None
    return "|".join(normalized)


def _normalize_profile_datasource_name(datasource: str) -> tuple[str, ...]:
    """Map user-facing datasource names onto schema-backed profile datasource enums."""
    text = str(datasource or "").strip()
    if not text:
        raise RuntimeError("datasource is required.")
    key = "".join(ch for ch in text if ch.isalnum()).casefold()
    if key in PROFILE_DATASOURCE_ALIASES:
        return PROFILE_DATASOURCE_ALIASES[key]
    return (text,)


def _apply_exclusion_filter_update(
    profile_info: dict[str, Any],
    *,
    datasource: str,
    add_filters: list[str] | None = None,
    remove_filters: list[str] | None = None,
    set_filters: list[str] | None = None,
    clear: bool = False,
) -> dict[str, Any]:
    """Return a locally mutated profile payload with updated exclusion filters."""
    if clear and set_filters:
        raise RuntimeError("clear and set_filters are mutually exclusive.")
    if not clear and not any((add_filters, remove_filters, set_filters)):
        raise RuntimeError("Provide add_filters, remove_filters, set_filters, or clear=True.")

    targets = set(_normalize_profile_datasource_name(datasource))
    working_copy = deepcopy(profile_info)
    profile_data = working_copy.get("ProfileData") or {}
    backup_settings = list(profile_data.get("BackupDataSourceSettings") or [])
    matched = False

    for setting in backup_settings:
        if setting.get("DataSource") not in targets:
            continue
        matched = True
        current_filters = _split_exclusion_filter(setting.get("ExclusionFilter"))
        current_by_key = {item.casefold(): item for item in current_filters}

        if clear:
            next_filters: list[str] = []
        elif set_filters is not None:
            next_filters = [item for item in (set_filters or []) if str(item or "").strip()]
        else:
            for item in add_filters or []:
                text = str(item or "").strip()
                if text:
                    current_by_key[text.casefold()] = text
            for item in remove_filters or []:
                text = str(item or "").strip()
                if text:
                    current_by_key.pop(text.casefold(), None)
            next_filters = list(current_by_key.values())

        setting["ExclusionFilter"] = _join_exclusion_filter(next_filters)

    if not matched:
        profile_id = working_copy.get("Id") or working_copy.get("id")
        raise RuntimeError(f"Datasource '{datasource}' was not found in account profile {profile_id}.")

    profile_data["BackupDataSourceSettings"] = backup_settings
    working_copy["ProfileData"] = profile_data
    return working_copy


class CoveClient:
    """
    Cove Data Protection (N-able Backup) API client using JSON-RPC.

    Usage:
        client = CoveClient(username="...", password="...")
        await client.login()
        partners = await client.enumerate_partners(parentPartnerId=2674794)
        devices = await client.enumerate_devices(partner_id=2674794)
    """

    BASE_URL = "https://api.backup.management/jsonapi"

    # Reseller partner ID used as the default root for enumeration.
    # Updated from the validated local vendor note on 2026-03-25.
    ROOT_PARTNER_ID = 1738720

    def __init__(
        self,
        username: str,
        password: str,
        partner_name: str,
        base_url: str | None = None,
        root_partner_id: int | None = None,
        partner_id: int | None = None,
        psa_billing_api_key: str | None = None,
        psa_billing_sfdc_account_id: str | None = None,
        psa_billing_base_url: str | None = None,
    ):
        self.username = username
        self.password = password
        self.partner_name = partner_name
        self.base_url = self._normalize_base_url(base_url)
        self.root_partner_id = root_partner_id or self.ROOT_PARTNER_ID
        self.partner_id = partner_id
        self.psa_billing_api_key = psa_billing_api_key
        self.psa_billing_sfdc_account_id = psa_billing_sfdc_account_id
        self.psa_billing_base_url = (
            psa_billing_base_url or "https://cove-billing.boomi.com/ws/rest/V1/PSA_API"
        ).rstrip("/")
        self._visa_token: str | None = None
        self._client: httpx.AsyncClient | None = None

    @classmethod
    def _normalize_base_url(cls, base_url: str | None) -> str:
        normalized = (base_url or cls.BASE_URL).strip().rstrip("/")
        if normalized.endswith("/jsonapi"):
            return normalized
        return f"{normalized}/jsonapi"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=60.0,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    async def _rpc_call(self, method: str, params: dict | None = None) -> Any:
        """
        Make a JSON-RPC call and return body["result"].

        The visa token is sent as a top-level field on the request body.
        Each response also carries a fresh visa which we capture automatically.
        """
        client = await self._get_client()

        payload: dict = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": str(uuid.uuid4()),
        }

        if self._visa_token:
            payload["visa"] = self._visa_token

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://backup.management",
            "Referer": "https://backup.management/",
        }
        if self._visa_token:
            headers["Authorization"] = f"Bearer {self._visa_token}"
        response = await client.post(self.base_url, json=payload, headers=headers)
        if not response.is_success:
            body_text = response.text[:1000] if response.text else ""
            raise Exception(f"Cove HTTP {response.status_code} [{method}]: {body_text}")

        body = response.json()

        if "error" in body:
            error = body["error"]
            raise Exception(f"Cove API error [{method}]: {error.get('message', error)}")

        # Refresh visa from every response to keep the chain alive
        if "visa" in body:
            self._visa_token = body["visa"]

        return body.get("result")

    async def login(self) -> None:
        """
        Authenticate and store the visa token.

        The visa is a top-level field on the Login response body, so we handle
        this call directly rather than going through _rpc_call.
        """
        client = await self._get_client()
        payload = {
            "jsonrpc": "2.0",
            "method": "Login",
            "params": {
                "partner": self.partner_name,
                "username": self.username,
                "password": self.password,
            },
            "id": str(uuid.uuid4()),
        }
        response = await client.post(
            self.base_url,
            json=payload,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://backup.management",
                "Referer": "https://backup.management/",
            },
        )
        if not response.is_success:
            body_text = response.text[:1000] if response.text else ""
            raise Exception(f"Cove HTTP {response.status_code} [Login]: {body_text}")
        body = response.json()

        if "error" in body:
            error = body["error"]
            raise Exception(f"Cove login error: {error.get('message', error)}")

        self._visa_token = body.get("visa")
        if not self._visa_token:
            raise ValueError(f"No visa token in login response. Keys returned: {list(body.keys())}")

        login_result = _unwrap(body.get("result"))
        if isinstance(login_result, dict):
            partner_id = login_result.get("PartnerId") or login_result.get("partnerId")
            if partner_id is not None:
                with contextlib.suppress(TypeError, ValueError):
                    self.root_partner_id = int(partner_id)

    async def _ensure_logged_in(self) -> None:
        if not self._visa_token:
            await self.login()

    def _rest_api_base_url(self) -> str:
        if self.base_url.endswith("/jsonapi"):
            return self.base_url[: -len("/jsonapi")]
        return self.base_url.rstrip("/")

    async def _draas_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """
        Call Cove's DRAAS REST endpoints with the JSON-RPC visa as a bearer token.

        The browser UI stores the same token shape in localStorage["bm_visa"].
        Keeping this inside the normal Cove client avoids persisting or passing
        operator browser tokens through workflow inputs.
        """
        await self._ensure_logged_in()
        if not self._visa_token:
            raise RuntimeError("Cove DRAAS request requires an authenticated visa token.")

        client = await self._get_client()
        url = f"{self._rest_api_base_url()}/{path.lstrip('/')}"
        response = await client.get(
            url,
            params=params or {},
            headers={
                "Authorization": f"Bearer {self._visa_token}",
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://backup.management",
                "Referer": "https://backup.management/",
            },
        )
        if not response.is_success:
            body_text = response.text[:1000] if response.text else ""
            raise Exception(f"Cove DRAAS HTTP {response.status_code} [{path}]: {body_text}")
        return response.json() if response.content else None

    async def _draas_post(self, path: str, payload: dict[str, Any]) -> Any:
        await self._ensure_logged_in()
        if not self._visa_token:
            raise RuntimeError("Cove DRAAS request requires an authenticated visa token.")

        client = await self._get_client()
        response = await client.post(
            f"{self._rest_api_base_url()}/{path.lstrip('/')}",
            json=payload,
            headers={
                "Authorization": f"Bearer {self._visa_token}",
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/vnd.api+json",
                "Origin": "https://backup.management",
                "Referer": "https://backup.management/",
            },
        )
        if not response.is_success:
            body_text = response.text[:1000] if response.text else ""
            raise Exception(f"Cove DRAAS HTTP {response.status_code} [{path}]: {body_text}")
        return response.json() if response.content else None

    @staticmethod
    def _storage_reporting_url(storage_host: str) -> str:
        raw_host = str(storage_host or "").strip().rstrip("/")
        if not raw_host:
            raise ValueError("Cove storage host is required.")
        parsed = urlsplit(raw_host if "://" in raw_host else f"https://{raw_host}")
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Cove storage host must be an HTTPS hostname without credentials, path, query, or fragment."
            )
        if parsed.port not in (None, 443):
            raise ValueError("Cove storage host must use HTTPS port 443.")
        hostname = parsed.hostname.rstrip(".").casefold()
        if not hostname.endswith(".cloudbackup.management"):
            raise ValueError("Cove storage host must be a Cove-managed cloudbackup.management node.")
        return f"https://{hostname}/jsonapi"

    async def _storage_rpc_call(self, method: str, params: dict[str, Any], *, storage_host: str) -> Any:
        await self._ensure_logged_in()
        client = await self._get_client()
        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": "jsonrpc"}
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://backup.management",
            "Referer": "https://backup.management/",
        }
        if self._visa_token:
            headers["Authorization"] = f"Bearer {self._visa_token}"
        response = await client.post(
            self._storage_reporting_url(storage_host),
            json=payload,
            headers=headers,
        )
        if not response.is_success:
            body_text = response.text[:1000] if response.text else ""
            raise Exception(f"Cove storage HTTP {response.status_code} [{method}]: {body_text}")
        body = response.json()
        if body.get("visa"):
            self._visa_token = body["visa"]
        if "error" in body:
            error = body["error"]
            raise Exception(f"Cove storage API error [{method}]: {error.get('message', error)}")
        return _unwrap(body.get("result"))

    async def _psa_billing_get(self, path: str) -> Any:
        if not self.psa_billing_api_key:
            raise RuntimeError("Cove PSA billing API key is not configured.")
        client = await self._get_client()
        response = await client.post(
            f"{self.psa_billing_base_url}/{path.lstrip('/')}",
            headers={
                "x-api-key": self.psa_billing_api_key,
                "Accept": "application/json",
            },
        )
        if not response.is_success:
            body_text = response.text[:1000] if response.text else ""
            raise Exception(f"Cove PSA billing HTTP {response.status_code} [{path}]: {body_text}")
        return response.json()

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # -------------------------------------------------------------------------
    # DRAAS / Recovery Locations
    # -------------------------------------------------------------------------

    async def list_recovery_agents(
        self,
        *,
        partner_id: int | None = None,
        states: list[str] | None = None,
        offset: int = 0,
        limit: int = 100,
        sort: str = "name",
    ) -> dict[str, Any]:
        """List DRAAS recovery location agents visible to the authenticated partner."""
        effective_states = states or ["ONLINE", "OFFLINE", "STORAGE_NOT_CONFIGURED"]
        effective_partner_id = partner_id or self.root_partner_id
        return await self._draas_get(
            "/draas/actual-statistics/v1/dashboard/recovery-agents/",
            params={
                "filter[agent_state.in]": ",".join(effective_states),
                "filter[materialized_path.contains]": f"/{effective_partner_id}/",
                "offset": str(offset),
                "limit": str(limit),
                "sort": sort,
            },
        )

    async def list_recovery_agent_history(
        self,
        recovery_agent_id: str | int,
        *,
        offset: int = 0,
        limit: int = 100,
        sort: str = "-event_timestamp",
    ) -> dict[str, Any]:
        """List recent recovery-location history events for a DRAAS recovery agent."""
        return await self._draas_get(
            "/draas/actual-statistics/v1/history/recovery-agents/",
            params={
                "filter[recovery_agent_id.eq]": str(recovery_agent_id),
                "offset": str(offset),
                "limit": str(limit),
                "sort": sort,
            },
        )

    async def search_restore_compatible_devices(
        self,
        *,
        recovery_agent_id: int,
        search: str,
        partner_id: int | None = None,
        plan_type: str = "OTR_TO_HYPERV",
        start_record_number: int = 0,
        records_count: int = 50,
    ) -> dict[str, Any]:
        """Search DRAAS-compatible devices for a recovery agent."""
        escaped = str(search or "").replace("'", "''")
        return await self._draas_post(
            "/draas/web-front/v1/device-compatibility-checks/requests/",
            {
                "data": {
                    "type": "DeviceCompatibilityCheckRequest",
                    "attributes": {
                        "selection_partner_id": int(partner_id or self.root_partner_id),
                        "plan_type": plan_type,
                        "start_record_number": int(start_record_number),
                        "records_count": int(records_count),
                        "filter_by": (
                            "AT == 1 AND OP != 'Documents' AND "
                            f"(AN =~ '*{escaped}*' OR AR =~ '*{escaped}*' OR "
                            f"MN =~ '*{escaped}*' OR OP =~ '*{escaped}*')"
                        ),
                        "order_by": "AN ASC",
                        "totals": ["COUNT(1)"],
                        "agent_id": int(recovery_agent_id),
                    },
                },
            },
        )

    async def generate_reinstallation_passphrase(self, account_id: int) -> str:
        """Generate a fresh one-time reinstall passphrase for a Cove account."""
        await self._ensure_logged_in()
        raw = await self._rpc_call("GenerateReinstallationPassphrase", {"accountId": int(account_id)})
        result = _unwrap(raw)
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            for key in ("Passphrase", "passphrase", "Token", "token", "Password", "password"):
                value = result.get(key)
                if value:
                    return str(value)
        raise RuntimeError("Cove did not return a usable reinstall passphrase.")

    async def enumerate_storage_sessions(
        self,
        *,
        token: str,
        password: str | None = None,
        account: str,
        storage_host: str,
        account_id: int | None = None,
    ) -> dict[str, Any]:
        """Enumerate backup sessions from Cove storage for restore-point selection."""
        candidates = [str(token)]
        if password:
            candidates.append(str(password))
        if account_id is not None:
            candidates.append(str(int(account_id)))
        if account:
            candidates.append(str(account))
        last_error: Exception | None = None
        for account_token in dict.fromkeys(candidates):
            try:
                return await self._storage_rpc_call(
                    "EnumerateSessions",
                    {
                        "accountToken": account_token,
                        "filter": {"OnlyLastSessions": False},
                        "range": {"Offset": 0, "Size": 1000},
                    },
                    storage_host=storage_host,
                )
            except Exception as exc:
                last_error = exc
                if "doesn&apos;t exist" not in str(exc) and "doesn't exist" not in str(exc):
                    raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("Cove session enumeration requires an account token candidate.")

    async def create_hyperv_on_demand_restore_device(
        self,
        *,
        device_id: int,
        device_name: str,
        device_password: str,
        credentials: str,
        recovery_agent_id: int,
        vm_name: str,
        vhd_path: str = "E:\\",
        cpu_count: int = 2,
        ram_size_mb: int = 4096,
        notification_emails: list[str] | None = None,
        allow_recovery_only_in_matching_region: bool = True,
        override_existing_vm: bool = False,
        read_only_mode: bool = True,
    ) -> dict[str, Any]:
        """Create a one-time Hyper-V restore plan device."""
        emails = [str(email).strip() for email in (notification_emails or []) if str(email or "").strip()]
        return await self._draas_post(
            "/draas/web-front/v1/recovery-plan-devices/",
            {
                "data": {
                    "type": "RecoveryPlanDevice",
                    "attributes": {
                        "device_id": str(int(device_id)),
                        "device_name": device_name,
                        "device_password": device_password,
                        "credentials": credentials,
                        "allow_recovery_only_in_matching_region": allow_recovery_only_in_matching_region,
                        "successful_restore_notification_emails": emails,
                        "failed_restore_notification_emails": emails,
                        "boot_frequency": -1,
                        "plan_type": "OTR_TO_HYPERV",
                        "recovery_target": {
                            "vhd_path": vhd_path,
                            "type": "Hyper-V on Demand",
                            "enable_replication_service": False,
                            "local_speed_vault": False,
                            "lsv_type": "LOCAL",
                            "lsv_path": "",
                            "lsv_user": "",
                            "lsv_password": "",
                            "vm_virtual_switch": "",
                            "subnet_mask": "",
                            "gateway": "",
                            "dns_server": "",
                            "vm_address": "",
                            "network_share_settings": None,
                            "device_number_of_cpu": int(cpu_count),
                            "device_ram_size_mb": int(ram_size_mb),
                            "system_drive_only": False,
                            "read_only_mode": read_only_mode,
                            "self_hosted_on_demand_settings": {},
                            "credentials": credentials,
                            "hyperv_settings": {"vm_name": vm_name},
                            "override_existing_vm": override_existing_vm,
                        },
                    },
                    "relationships": {
                        "recovery_agent": {"data": {"type": "RecoveryAgent", "id": int(recovery_agent_id)}}
                    },
                }
            },
        )

    async def start_hyperv_on_demand_restore(self, *, plan_device_id: str | int, backup_session_time: str) -> Any:
        """Start a one-time Hyper-V restore for an existing restore plan device."""
        return await self._draas_post(
            "/draas/web-front/v1/start-restore/hyperv-on-demand/",
            {
                "data": {
                    "type": "StartRestore",
                    "attributes": {"plan_device_id": str(plan_device_id), "backup_session_time": backup_session_time},
                }
            },
        )

    async def exclude_recovery_plan_device(self, *, plan_device_id: str | int) -> Any:
        """Remove one restore plan device from Cove's recovery dashboard."""
        return await self._draas_post(
            "/draas/web-front/v1/recovery-plan-devices/exclude-device/",
            {
                "data": {
                    "type": "DeviceIdentifier",
                    "attributes": {"plan_device_id": str(plan_device_id)},
                }
            },
        )

    async def get_restore_dashboard(
        self,
        *,
        root_partner_id: int | None = None,
        backup_cloud_device_id: int | None = None,
        plan_device_id: int | str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Read DRAAS restore dashboard rows for restore status readback."""
        params: dict[str, str] = {
            "offset": "0",
            "limit": str(limit),
            "fields": "",
            "sort": "-start_restore_timestamp",
            "filter[type.in]": "AZURE,ESXI_ON_DEMAND,SELF_HOSTED_ON_DEMAND",
        }
        if root_partner_id or self.root_partner_id:
            params["filter[partner_materialized_path.contains]"] = f"/{int(root_partner_id or self.root_partner_id)}/"
        if backup_cloud_device_id is not None:
            params["filter[backup_cloud_device_id.eq]"] = str(int(backup_cloud_device_id))
        result = await self._draas_get("/draas/actual-statistics/v1/dashboard/", params=params)
        if plan_device_id is not None:
            rows = result.get("data") if isinstance(result, dict) else []
            result = {
                **(result if isinstance(result, dict) else {}),
                "matched_plan_device": next(
                    (
                        item
                        for item in rows or []
                        if str((item.get("attributes") or {}).get("plan_device_id")) == str(plan_device_id)
                    ),
                    None,
                ),
            }
        return result

    # -------------------------------------------------------------------------
    # PSA billing
    # -------------------------------------------------------------------------

    def _psa_account_id(self, account_id: str | None = None) -> str:
        effective = account_id or self.psa_billing_sfdc_account_id
        if not effective:
            raise RuntimeError("Cove PSA billing SFDC account ID is not configured.")
        return effective

    async def list_psa_invoices_ready(self, billing_period: str, sfdc_account_id: str | None = None) -> Any:
        return await self._psa_billing_get(f"InvoicesReady/{self._psa_account_id(sfdc_account_id)}/{billing_period}")

    async def list_psa_billable_services(
        self,
        billing_period: str,
        contract_id: str | int,
        sfdc_account_id: str | None = None,
    ) -> Any:
        return await self._psa_billing_get(
            f"PSABillableServices/{self._psa_account_id(sfdc_account_id)}/{billing_period}/{contract_id}"
        )

    async def list_psa_usage_details_devices(
        self,
        billing_period: str,
        contract_id: str | int,
        service_id: str | int,
        sfdc_account_id: str | None = None,
    ) -> Any:
        return await self._psa_billing_get(
            f"PSAUsageDetailsDevices/{self._psa_account_id(sfdc_account_id)}/{billing_period}/{contract_id}/{service_id}"
        )

    async def get_psa_device_usage_details(
        self,
        billing_period: str,
        contract_id: str | int,
        service_id: str | int,
        sfdc_account_id: str | None = None,
    ) -> Any:
        return await self.list_psa_usage_details_devices(
            billing_period,
            contract_id,
            service_id,
            sfdc_account_id=sfdc_account_id,
        )

    # -------------------------------------------------------------------------
    # Partners (Customers)
    # -------------------------------------------------------------------------

    async def enumerate_partners(
        self,
        parent_partner_id: int | None = None,
        fields: list[int] | None = None,
        fetch_recursively: bool = True,
    ) -> list[dict]:
        """
        List customer partners under the given parent.

        Args:
            parent_partner_id: Defaults to the authenticated reseller partner.
            fields: UInt64 field bitmask values. Defaults to [64], the validated
                    value required for Name population in EnumeratePartners.
            fetch_recursively: Whether to include nested children.

        Returns:
            List of partner dicts with readable keys.
        """
        await self._ensure_logged_in()
        raw = await self._rpc_call(
            "EnumeratePartners",
            {
                "parentPartnerId": parent_partner_id or self.root_partner_id,
                "fields": fields if fields is not None else [64],
                "fetchRecursively": fetch_recursively,
            },
        )
        return _unwrap(raw) or []

    async def get_partner(self, partner_id: int) -> dict:
        """Get a specific partner by numeric ID."""
        await self._ensure_logged_in()
        raw = await self._rpc_call("GetPartnerInfoById", {"partnerId": partner_id})
        return _unwrap(raw) or {}

    async def get_partner_by_uid(self, uid: str) -> dict:
        """Get a partner by their UUID (Guid field)."""
        await self._ensure_logged_in()
        raw = await self._rpc_call("GetPartnerInfoByUid", {"partnerUid": uid})
        return _unwrap(raw) or {}

    async def create_customer(
        self,
        name: str,
        parent_partner_id: int | None = None,
    ) -> dict:
        """
        Create a new customer partner.

        Args:
            name: Customer name
            parent_partner_id: Parent partner ID. Defaults to ROOT_PARTNER_ID.

        Returns:
            Created partner dict with Id, Name, etc.
        """
        await self._ensure_logged_in()
        raw = await self._rpc_call(
            "AddPartner",
            {
                "partnerInfo": {
                    "ParentId": parent_partner_id or self.ROOT_PARTNER_ID,
                    "Name": name,
                    "Level": "EndCustomer",
                    "ServiceType": "AllInclusive",
                    "State": "InTrial",
                    "Country": "US",
                }
            },
        )
        result = _unwrap(raw)
        # AddPartner returns the new partner ID as an int — fetch full info
        if isinstance(result, int):
            return await self.get_partner(result)
        return result or {}

    async def update_customer(
        self,
        *,
        partner_id: int | None = None,
        name: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> dict:
        """
        Update an existing customer partner.

        Uses ModifyPartner and then fetches the updated partner record.
        """
        await self._ensure_logged_in()
        target_partner_id = partner_id or self.partner_id
        if target_partner_id is None:
            raise RuntimeError("Cove partner ID is not available. Configure a partner mapping first.")

        partner_info: dict[str, Any] = {"Id": int(target_partner_id)}
        if name not in (None, ""):
            partner_info["Name"] = name
        if extra_fields:
            partner_info.update(extra_fields)
        if len(partner_info) == 1:
            raise RuntimeError("Provide name or extra_fields to update the Cove customer.")

        await self._rpc_call("ModifyPartner", {"partnerInfo": partner_info})
        return await self.get_partner(int(target_partner_id))

    @staticmethod
    def normalize_partner(partner: dict) -> dict[str, str | None]:
        """Normalize a Cove partner payload to the fields Bifrost mapping needs."""
        partner_id = partner.get("Id") or partner.get("PartnerId") or partner.get("id")
        name = partner.get("Name") or partner.get("name")
        level = partner.get("Level") or partner.get("level")
        state = partner.get("State") or partner.get("state")
        guid = partner.get("Guid") or partner.get("guid") or partner.get("Uid") or partner.get("uid")
        normalized = {
            "id": str(partner_id) if partner_id is not None else None,
            "name": name or None,
            "level": level or None,
            "state": state or None,
        }
        if guid:
            normalized["guid"] = str(guid)
        return normalized

    async def get_partner_installer_info(self, partner_id: int | None = None) -> dict[str, Any]:
        """
        Get partner info with installer/enrollment details.

        Returns partner details including the Guid which can be used
        for unattended installations. The partner's Guid serves as the
        installation token for enrolling devices to this customer.

        Args:
            partner_id: Target partner ID. Defaults to the mapped partner_id.

        Returns:
            Dict with partner details including 'guid' for installer enrollment.
        """
        await self._ensure_logged_in()
        target_id = partner_id or self.partner_id
        if target_id is None:
            raise RuntimeError("Cove partner ID is not available. Configure a partner mapping first.")
        return await self.get_partner(int(target_id))

    def build_installer_download_url(self, partner_guid: str, platform: str = "windows") -> str:
        """
        Build the Cove Backup Manager installer download URL.

        Args:
            partner_guid: The partner's Guid (from get_partner_installer_info)
            platform: Target platform (windows, mac, linux)

        Returns:
            Direct download URL for the installer
        """
        base_url = "https://cdn.cloudbackup.management/maxdownloads/mxb-windows-x64.exe"
        if platform.lower() in ("mac", "macos", "darwin"):
            base_url = "https://cdn.cloudbackup.management/maxdownloads/mxb-macos-x64.pkg"
        elif platform.lower() in ("linux", "ubuntu", "debian"):
            base_url = "https://cdn.cloudbackup.management/maxdownloads/mxb-linux-x64.run"

        # The partner GUID is used as the enrollment token
        return f"{base_url}?partner={partner_guid}"

    def build_unattended_install_command(
        self,
        partner_guid: str,
        device_name: str | None = None,
        silent: bool = True,
    ) -> str:
        """
        Build a PowerShell command for unattended Cove installation.

        Args:
            partner_guid: The partner's Guid for enrollment
            device_name: Optional device name override
            silent: Whether to run silently (default True)

        Returns:
            PowerShell command string for installation
        """
        installer_url = self.build_installer_download_url(partner_guid, "windows")
        silent_flag = " -silent" if silent else ""
        device_flag = f' -device-name "{device_name}"' if device_name else ""

        return f"""
$ErrorActionPreference = 'Stop'
$Tls12 = [Net.SecurityProtocolType]::Tls12
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor $Tls12
$InstallerUrl = "{installer_url}"
$InstallerPath = Join-Path $env:TEMP "cove-backup-installer.exe"

try {{
    # Download installer
    Invoke-WebRequest -Uri $InstallerUrl -OutFile $InstallerPath -UseBasicParsing

    # Install with enrollment
    & $InstallerPath{silent_flag}{device_flag}

    # The installer can exit without surfacing an enrollment failure. Require
    # the installed controller service before reporting deployment success.
    $BackupService = $null
    for ($Attempt = 0; $Attempt -lt 30 -and -not $BackupService; $Attempt++) {{
        $BackupService = Get-Service -Name "Backup Service Controller" -ErrorAction SilentlyContinue
        if (-not $BackupService) {{ Start-Sleep -Seconds 2 }}
    }}
    if (-not $BackupService) {{
        throw "Cove installer exited without creating the Backup Service Controller service"
    }}
}}
finally {{
    Remove-Item $InstallerPath -Force -ErrorAction SilentlyContinue
}}
"""

    # -------------------------------------------------------------------------
    # Devices
    # -------------------------------------------------------------------------

    async def enumerate_devices(
        self,
        partner_id: int | None = None,
        columns: list[str] | None = None,
        start: int = 0,
        count: int = 500,
    ) -> list[dict]:
        """
        List backup devices, returning clean dicts with readable field names.

        Uses EnumerateAccountStatistics with the full column code set.
        Settings are flattened from [{code: value}] into {readable_name: value}.

        Args:
            partner_id: Filter to a specific partner. Defaults to ROOT_PARTNER_ID.
            columns: Column codes to fetch. Defaults to DEFAULT_DEVICE_COLUMNS.
            start: Pagination start record.
            count: Max records to return.

        Returns:
            List of device dicts. Each has account_id, partner_id, flags,
            plus all requested columns as readable keys.
        """
        await self._ensure_logged_in()
        raw = await self._rpc_call(
            "EnumerateAccountStatistics",
            {
                "query": {
                    "PartnerId": partner_id or self.ROOT_PARTNER_ID,
                    "StartRecordNumber": start,
                    "RecordsCount": count,
                    "Columns": columns or DEFAULT_DEVICE_COLUMNS,
                }
            },
        )

        records = _unwrap(raw) or []

        devices = []
        for record in records:
            device = {
                "account_id": record.get("AccountId"),
                "partner_id": record.get("PartnerId"),
                "flags": record.get("Flags") or [],
            }
            device.update(flatten_settings(record.get("Settings")))
            devices.append(device)

        return devices

    async def enumerate_accounts(self, partner_id: int | None = None) -> list[dict[str, Any]]:
        """List Cove accounts/devices using the official account inventory surface."""
        await self._ensure_logged_in()
        raw = await self._rpc_call(
            "EnumerateAccounts",
            {"partnerId": partner_id or self.root_partner_id},
        )
        return _unwrap(raw) or []

    async def get_device(self, device_id: int) -> dict:
        """Get a specific device by ID."""
        await self._ensure_logged_in()
        raw = await self._rpc_call("GetDeviceInfo", {"deviceId": device_id})
        return _unwrap(raw) or {}

    @staticmethod
    def normalize_device(device: dict[str, Any]) -> dict[str, Any]:
        """Shape a Cove device payload for operator-facing workflows."""
        return {
            "account_id": str(device.get("account_id")) if device.get("account_id") is not None else None,
            "partner_id": str(device.get("partner_id")) if device.get("partner_id") is not None else None,
            "profile_id": str(device.get("profile_id")) if device.get("profile_id") not in (None, "") else None,
            "device_name": device.get("device_name") or None,
            "computer_name": device.get("computer_name") or None,
            "os_type": device.get("os_type") or None,
            "active_data_sources": device.get("active_data_sources") or None,
            "retention": device.get("retention") or None,
            "retention_units": device.get("retention_units") or None,
            "archived_size": device.get("archived_size") or None,
            "last_backup_time": device.get("last_backup_time") or None,
            "storage_status": device.get("storage_status") or None,
        }

    async def get_account_info(self, account_id: int) -> dict[str, Any]:
        """Get Cove account metadata, including the assigned account profile ID."""
        await self._ensure_logged_in()
        raw = await self._rpc_call("GetAccountInfoById", {"accountId": account_id})
        return _unwrap(raw) or {}

    async def get_account_info_for_restore(self, account_id: int) -> dict[str, Any]:
        """Get account metadata plus storage home-node hints needed for restore session lookup."""
        await self._ensure_logged_in()
        raw = await self._rpc_call("GetAccountInfoById", {"accountId": int(account_id)})
        account = _unwrap(raw) or {}
        inventory_account: dict[str, Any] = {}
        partner_id = account.get("PartnerId") or account.get("partnerId")
        if partner_id is not None:
            inventory = await self.enumerate_accounts(int(partner_id))
            inventory_account = next(
                (
                    item
                    for item in inventory
                    if str(item.get("Id") or item.get("ID") or item.get("id")) == str(int(account_id))
                ),
                {},
            )
        account_statistics: dict[str, Any] = {}
        if partner_id is not None:
            statistics = await self.enumerate_devices(
                int(partner_id),
                columns=["I0", "I1", "D01F09"],
                count=500,
            )
            account_statistics = next(
                (
                    item
                    for item in statistics
                    if str(item.get("account_id") or item.get("device_id")) == str(int(account_id))
                ),
                {},
            )
        return {
            "account": account,
            "inventory_account": inventory_account,
            "account_statistics": account_statistics,
            "home_node_info": raw.get("homeNodeInfo") if isinstance(raw, dict) else {},
        }

    async def enumerate_account_profiles(self, partner_id: int | None = None) -> list[dict[str, Any]]:
        """List account profiles for a partner."""
        await self._ensure_logged_in()
        raw = await self._rpc_call(
            "EnumerateAccountProfiles",
            {"partnerId": partner_id or self.root_partner_id},
        )
        return _unwrap(raw) or []

    async def get_account_profile(self, profile_id: int) -> dict[str, Any]:
        """Fetch a full account profile record."""
        await self._ensure_logged_in()
        raw = await self._rpc_call("GetAccountProfileInfo", {"accountProfileId": profile_id})
        return _unwrap(raw) or {}

    async def update_account_profile(self, profile_info: dict[str, Any]) -> dict[str, Any]:
        """Persist a modified account profile and return the refreshed record."""
        await self._ensure_logged_in()
        await self._rpc_call("ModifyAccountProfile", {"accountProfileInfo": profile_info})
        profile_id = profile_info.get("Id") or profile_info.get("id")
        if profile_id in (None, ""):
            return profile_info
        return await self.get_account_profile(int(profile_id))

    async def enumerate_products(
        self,
        partner_id: int | None = None,
        *,
        skip_default_features: bool = False,
    ) -> list[dict[str, Any]]:
        """List products for a partner."""
        await self._ensure_logged_in()
        raw = await self._rpc_call(
            "EnumerateProducts",
            {
                "partnerId": partner_id or self.root_partner_id,
                "skipDefaultFeatures": skip_default_features,
            },
        )
        return _unwrap(raw) or []

    async def get_product_info(self, product_id: int) -> dict[str, Any]:
        """Fetch one Cove product by ID."""
        await self._ensure_logged_in()
        raw = await self._rpc_call("GetProductInfo", {"productId": product_id})
        return _unwrap(raw) or {}

    async def set_account_product(self, account_ids: list[int], product_id: int) -> dict[str, Any]:
        """Assign a Cove product to one or more accounts."""
        await self._ensure_logged_in()
        raw = await self._rpc_call(
            "ModifyAccountsBatch",
            {
                "accountIds": [int(account_id) for account_id in account_ids],
                "accountInfo": {"ProductId": int(product_id)},
            },
        )
        return _unwrap(raw) or {}

    async def set_account_partner(self, account_ids: list[int], partner_id: int) -> dict[str, Any]:
        """Move one or more Cove accounts to an explicit customer partner."""
        await self._ensure_logged_in()
        results: list[dict[str, Any]] = []
        for account_id in account_ids:
            normalized_account_id = int(account_id)
            account = await self.get_account_info(normalized_account_id)
            account_name = account.get("Name") or account.get("name")
            if not account_name:
                raise ValueError(f"Cove account {normalized_account_id} did not return a device name.")

            raw = await self._rpc_call(
                "ModifyAccount",
                {
                    "accountInfo": {
                        "Name": str(account_name),
                        "PartnerId": int(partner_id),
                    }
                },
            )
            results.append({"account_id": normalized_account_id, "result": _unwrap(raw)})
        return {"results": results}

    @staticmethod
    def normalize_product(product: dict[str, Any]) -> dict[str, Any]:
        """Shape Cove product metadata and classify Enhanced Retention Policy products."""
        features = product.get("Features") or product.get("features") or []
        feature_names: set[str] = set()
        feature_values: dict[str, Any] = {}
        for feature in features:
            if isinstance(feature, dict):
                name = str(feature.get("Name") or feature.get("name") or feature.get("Key") or feature.get("key") or "")
                value = feature.get("Value") or feature.get("value")
            elif isinstance(feature, (list, tuple)) and feature:
                name = str(feature[0])
                value = feature[1] if len(feature) > 1 else None
            else:
                continue
            if not name:
                continue
            feature_names.add(name)
            feature_values[name] = value

        retention = {
            "intra_daily": feature_values.get("IntraDailyRetention"),
            "daily": feature_values.get("DailyRetention"),
            "weekly": feature_values.get("WeeklyRetention"),
            "monthly": feature_values.get("MonthlyRetention"),
            "yearly": feature_values.get("YearlyRetention"),
        }
        enhanced_feature_names = {"IntraDailyRetention", "WeeklyRetention", "MonthlyRetention", "YearlyRetention"}
        product_id = product.get("Id") or product.get("id")
        partner_id = product.get("PartnerId") or product.get("partner_id") or product.get("partnerId")
        return {
            "id": str(product_id) if product_id not in (None, "") else None,
            "partner_id": str(partner_id) if partner_id not in (None, "") else None,
            "name": product.get("Name") or product.get("name"),
            "features": features,
            "feature_names": sorted(feature_names),
            "feature_values": feature_values,
            "retention": retention,
            "is_enhanced_retention_policy": bool(enhanced_feature_names & feature_names),
        }

    @staticmethod
    def summarize_profile_datasource_settings(
        profile_info: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return normalized backup datasource settings for an account profile."""
        profile_data = (profile_info or {}).get("ProfileData") or {}
        settings = profile_data.get("BackupDataSourceSettings") or []
        summary: list[dict[str, Any]] = []
        for item in settings:
            selection_items = item.get("SelectionCollection") or []
            summary.append(
                {
                    "datasource": item.get("DataSource"),
                    "policy": item.get("Policy"),
                    "selection_modification": item.get("SelectionModification"),
                    "selection_collection": [
                        selection.get("Selection")
                        for selection in selection_items
                        if selection.get("Selection") not in (None, "")
                    ],
                    "exclusion_filter": item.get("ExclusionFilter") or None,
                    "exclusion_filters": _split_exclusion_filter(item.get("ExclusionFilter")),
                }
            )
        return summary

    @staticmethod
    def normalize_account_profile(profile_info: dict[str, Any]) -> dict[str, Any]:
        """Shape account profile metadata for operator-facing workflows."""
        return {
            "id": str(profile_info.get("Id")) if profile_info.get("Id") is not None else None,
            "partner_id": (str(profile_info.get("PartnerId")) if profile_info.get("PartnerId") is not None else None),
            "name": profile_info.get("Name") or None,
            "version": profile_info.get("Version"),
            "datasource_settings": CoveClient.summarize_profile_datasource_settings(profile_info),
        }

    async def update_profile_exclusion_filters(
        self,
        *,
        profile_id: int,
        datasource: str,
        add_filters: list[str] | None = None,
        remove_filters: list[str] | None = None,
        set_filters: list[str] | None = None,
        clear: bool = False,
    ) -> dict[str, Any]:
        """
        Update exclusion filters for one datasource inside an account profile.

        This uses the official schema-backed profile surface:
        AccountProfileInfo.ProfileData.BackupDataSourceSettings[*].ExclusionFilter
        """
        profile_info = await self.get_account_profile(profile_id)
        mutated = _apply_exclusion_filter_update(
            profile_info,
            datasource=datasource,
            add_filters=add_filters,
            remove_filters=remove_filters,
            set_filters=set_filters,
            clear=clear,
        )
        return await self.update_account_profile(mutated)

    @staticmethod
    def preview_profile_exclusion_filters(
        profile_info: dict[str, Any],
        *,
        datasource: str,
        add_filters: list[str] | None = None,
        remove_filters: list[str] | None = None,
        set_filters: list[str] | None = None,
        clear: bool = False,
    ) -> dict[str, Any]:
        """Return a locally projected profile payload for preview mode."""
        return _apply_exclusion_filter_update(
            profile_info,
            datasource=datasource,
            add_filters=add_filters,
            remove_filters=remove_filters,
            set_filters=set_filters,
            clear=clear,
        )

    async def add_device_to_recovery_testing(
        self,
        device_id: int,
        email: str | None = None,
    ) -> dict:
        """Add a device to recovery testing."""
        await self._ensure_logged_in()
        params: dict = {"deviceId": device_id}
        if email:
            params["notificationEmail"] = email
        return await self._rpc_call("EnableRecoveryTesting", params) or {}

    # -------------------------------------------------------------------------
    # Storage Vaults
    # -------------------------------------------------------------------------

    async def enumerate_storage_vaults(self) -> list[dict]:
        """List available storage vaults."""
        await self._ensure_logged_in()
        raw = await self._rpc_call("EnumerateStorageVaults", {})
        return _unwrap(raw) or []


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


async def get_client(scope: str | None = None) -> CoveClient:
    """
    Get a CoveClient configured from the 'Cove Data Protection' Bifrost integration.

    Usage:
        from modules.cove import get_client

        client = await get_client()
        partners = await client.enumerate_partners()
        devices = await client.enumerate_devices()
    """
    from bifrost import integrations

    integration = await integrations.get("Cove Data Protection", scope=scope)
    if not integration:
        raise RuntimeError("Integration 'Cove Data Protection' not found in Bifrost")

    cfg = integration.config or {}
    partner_name = cfg.get("partner_name") or cfg.get("partner")
    username = cfg.get("username") or cfg.get("cove_username")
    password = cfg.get("password") or cfg.get("cove_password")
    base_url = cfg.get("base_url")
    psa_billing_api_key = cfg.get("psa_billing_api_key") or cfg.get("billing_api_key")
    psa_billing_sfdc_account_id = cfg.get("psa_billing_sfdc_account_id") or cfg.get("sfdc_account_id")
    psa_billing_base_url = cfg.get("psa_billing_base_url") or cfg.get("billing_base_url")

    if not partner_name or not username or not password:
        raise RuntimeError(
            "Integration 'Cove Data Protection' is missing partner_name, username/cove_username, "
            "or password/cove_password. "
            f"Found keys: {list(cfg.keys())}"
        )

    partner_id = getattr(integration, "entity_id", None)
    root_partner_id = cfg.get("root_partner_id")
    return CoveClient(
        username=username,
        password=password,
        partner_name=partner_name,
        base_url=base_url,
        root_partner_id=int(root_partner_id) if root_partner_id else None,
        partner_id=int(partner_id) if partner_id is not None else None,
        psa_billing_api_key=psa_billing_api_key,
        psa_billing_sfdc_account_id=psa_billing_sfdc_account_id,
        psa_billing_base_url=psa_billing_base_url,
    )
