#!/usr/bin/env python3
"""Temporary, loopback-only diagnostic proof for the task-owned debug stack.

Read {url, email, password} only from stdin. Never print response bodies,
identifiers, credentials, keys or tokens. The parent must destroy its disposable
debug stack after retaining this script's SHA-bound restricted JSON receipt.
This script is removed before the final PR.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import secrets
import struct
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4


GRANTS = ["agents.promote", "devices.execute", "devices.readwrite"]


class SmokeFailure(Exception):
    def __init__(self, step: str, status: int | None = None) -> None:
        self.step = step
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SmokeFailure("redirect_refused", code)


class Proof:
    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")
        self.http = build_opener(NoRedirect())
        self.deadline = time.monotonic() + 240
        self.checks: list[dict] = []
        self.cleanup: dict[str, int] = {}

    def request(
        self,
        step,
        method,
        path,
        *,
        token=None,
        body=None,
        headers=None,
        form=None,
        expect=(200,),
    ):
        if not path.startswith("/") or path.startswith("//"):
            raise SmokeFailure("nonlocal_path_refused")
        request_headers = {"Accept": "application/json", **(headers or {})}
        data = None
        if token:
            request_headers["Authorization"] = f"Bearer {token}"
        if form is not None:
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
            data = urlencode(form).encode()
        elif body is not None:
            request_headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        request = Request(
            self.url + path, data=data, headers=request_headers, method=method
        )
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise SmokeFailure("diagnostic_deadline")
        try:
            with self.http.open(request, timeout=min(20, remaining)) as response:
                status = response.status
                raw = response.read(4_000_001)
        except HTTPError as response:
            status = response.code
            raw = response.read(4_000_001)
        except URLError:
            raise SmokeFailure(step) from None
        self.checks.append(
            {"check": step, "status": status, "passed": status in expect}
        )
        if status not in expect:
            raise SmokeFailure(step, status)
        if len(raw) > 4_000_000:
            raise SmokeFailure(step + "_response_limit")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError):
            raise SmokeFailure(step + "_invalid_json", status) from None

    def condition(self, name, passed):
        self.checks.append({"check": name, "passed": bool(passed)})
        if not passed:
            raise SmokeFailure(name)

    def login(self, email, password, *, owned_fresh, name):
        login = self.request(
            name + "_login",
            "POST",
            "/auth/login",
            form={"username": email, "password": password},
        )
        if (
            login.get("access_token")
            and not login.get("mfa_required")
            and not login.get("mfa_setup_required")
        ):
            return login["access_token"]
        if (
            not owned_fresh
            or not login.get("mfa_setup_required")
            or not login.get("mfa_token")
        ):
            raise SmokeFailure(name + "_existing_mfa_human_step")
        mfa_token = login["mfa_token"]
        setup = self.request(
            name + "_fresh_mfa_setup", "POST", "/auth/mfa/setup", token=mfa_token
        )
        secret = setup["secret"].replace(" ", "").upper()
        secret_bytes = base64.b32decode(secret + "=" * ((-len(secret)) % 8))
        digest = hmac.new(
            secret_bytes, struct.pack(">Q", int(time.time()) // 30), hashlib.sha1
        ).digest()
        offset = digest[-1] & 15
        code = str(
            (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF)
            % 1_000_000
        ).zfill(6)
        verified = self.request(
            name + "_fresh_mfa_verify",
            "POST",
            "/auth/mfa/verify",
            token=mfa_token,
            body={"code": code},
        )
        self.condition(name + "_session_issued", bool(verified.get("access_token")))
        return verified["access_token"]

    def grant(self, admin, role_id, grants, step):
        path = f"/api/roles/{role_id}/bridge-grants"
        snapshot = self.request(step + "_snapshot", "GET", path, token=admin)
        body = {
            "permissions": grants,
            "expected_permissions": snapshot["permissions"],
            "expected_version": snapshot["version"],
        }
        saved = self.request(step + "_save", "PUT", path, token=admin, body=body)
        self.condition(
            step + "_exact_set", sorted(saved["permissions"]) == sorted(grants)
        )
        return body, saved


def guard(args, supplied):
    branch = os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME")
    if os.environ.get("GITHUB_ACTIONS") != "true" or branch != args.diagnostic_branch:
        raise SmokeFailure("diagnostic_ci_branch_guard")
    if not args.diagnostic_branch.startswith("codex/upstream-rbac-sync-20261017"):
        raise SmokeFailure("task_branch_guard")
    parsed = urlsplit(supplied["url"])
    try:
        loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        loopback = False
    if (
        not loopback
        or parsed.scheme not in {"http", "https"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise SmokeFailure("literal_loopback_url_guard")
    if not supplied.get("email") or not supplied.get("password"):
        raise SmokeFailure("missing_debug_credentials")


def exercise(proof, supplied, args):
    resources = {
        "org": None,
        "role": None,
        "user": None,
        "device": None,
        "keys": [],
        "jobs": [],
    }
    admin = None
    tag = "rbac-debug-" + uuid4().hex[:12]
    try:
        schema = proof.request("public_schema", "GET", "/openapi.json")
        proof.condition(
            "typed_public_contracts",
            all(
                name in schema["components"]["schemas"]
                for name in (
                    "RoleBridgeGrantsUpdate",
                    "DeviceCreate",
                    "ControlKeyCreate",
                    "DeviceJobCreate",
                )
            ),
        )
        admin = proof.login(
            supplied["email"],
            supplied["password"],
            owned_fresh=args.allow_fresh_admin_mfa_setup,
            name="debug_admin",
        )
        authorization = proof.request(
            "admin_current_authorization", "GET", "/auth/authorization", token=admin
        )
        proof.condition(
            "current_platform_wildcard", authorization["is_platform_admin"] is True
        )
        resources["org"] = proof.request(
            "create_synthetic_org",
            "POST",
            "/api/organizations",
            token=admin,
            body={"name": tag},
            expect=(201,),
        )["id"]
        resources["role"] = proof.request(
            "create_synthetic_role",
            "POST",
            "/api/roles",
            token=admin,
            body={"name": tag},
            expect=(201,),
        )["id"]
        role_id = resources["role"]
        path = f"/api/roles/{role_id}/bridge-grants"
        original, saved = proof.grant(admin, role_id, GRANTS, "editor_roundtrip")
        proof.request(
            "stale_current_set_version_refused",
            "PUT",
            path,
            token=admin,
            body=original,
            expect=(409,),
        )
        proof.request(
            "unknown_grant_refused",
            "PUT",
            path,
            token=admin,
            body={
                "permissions": ["roles.readwrite"],
                "expected_permissions": saved["permissions"],
                "expected_version": saved["version"],
            },
            expect=(422,),
        )
        proof.request("anonymous_editor_refused", "GET", path, expect=(401, 403))
        listing = proof.request("roles_list", "GET", "/api/roles", token=admin)
        proof.condition(
            "role_list_has_no_legacy_json",
            all("permissions" not in row for row in listing),
        )
        proof.request(
            "legacy_role_json_refused",
            "PATCH",
            f"/api/roles/{role_id}",
            token=admin,
            body={"permissions": {"can_execute_devices": True}},
            expect=(422,),
        )

        created = proof.request(
            "create_synthetic_device",
            "POST",
            "/api/devices",
            token=admin,
            body={"display_name": tag, "organization_id": resources["org"]},
            expect=(201,),
        )
        resources["device"] = created["device"]["id"]
        enrolled = proof.request(
            "enroll_synthetic_device",
            "POST",
            "/api/devices/enroll",
            body={"enrollment_token": created["enrollment_token"]},
        )
        proof.condition("synthetic_device_active", enrolled["status"] == "active")
        proof.request(
            "enrollment_replay_refused",
            "POST",
            "/api/devices/enroll",
            body={"enrollment_token": created["enrollment_token"]},
            expect=(401,),
        )

        user_email = tag + "@example.test"
        password = secrets.token_urlsafe(24) + "aA1!"
        user = proof.request(
            "create_synthetic_person_without_email_send",
            "POST",
            "/api/users",
            token=admin,
            body={
                "email": user_email,
                "name": tag,
                "organization_id": resources["org"],
            },
            expect=(201,),
        )
        resources["user"] = user["id"]
        invite_token = parse_qs(urlsplit(user["registration_url"]).query).get(
            "token", []
        )
        proof.condition("public_invite_token_available", len(invite_token) == 1)
        proof.request(
            "consume_synthetic_invite",
            "POST",
            "/auth/register-from-invite",
            body={"token": invite_token[0], "password": password, "name": tag},
        )
        ordinary = proof.login(
            user_email, password, owned_fresh=True, name="synthetic_person"
        )
        user_auth = proof.request(
            "ordinary_authorization", "GET", "/auth/authorization", token=ordinary
        )
        proof.condition(
            "synthetic_person_is_ordinary",
            user_auth["is_platform_admin"] is False
            and user_auth["home_organization_id"] == resources["org"],
        )
        proof.request(
            "assign_home_bridge_role",
            "POST",
            f"/api/roles/{role_id}/users",
            token=admin,
            body={"user_ids": [resources["user"]]},
            expect=(204,),
        )
        proof.request(
            "ordinary_editor_refused", "GET", path, token=ordinary, expect=(403,)
        )
        device_id = resources["device"]
        job_body = {
            "script_name": tag,
            "script_content": "# Synthetic unclaimed debug job; no device agent is connected.\n",
            "timeout_seconds": 30,
            "max_output_bytes": 1024,
        }
        key_body = {
            "name": tag,
            "organization_id": resources["org"],
            "device_ids": [device_id],
        }
        for name, grants, manage, execute in [
            ("neither", [], False, False),
            ("manage_only", ["devices.readwrite"], True, False),
            ("execute_only", ["devices.execute"], False, True),
            ("both", ["devices.readwrite", "devices.execute"], True, True),
        ]:
            proof.grant(admin, role_id, grants, name)
            proof.request(
                name + "_registry",
                "GET",
                "/api/devices",
                token=ordinary,
                expect=(200,) if manage else (403,),
            )
            job = proof.request(
                name + "_job_create",
                "POST",
                f"/api/devices/{device_id}/jobs",
                token=ordinary,
                body=job_body,
                expect=(201,) if execute else (403,),
            )
            if execute:
                job_id = job["id"]
                resources["jobs"].append(job_id)
                detail = proof.request(
                    name + "_job_detail",
                    "GET",
                    f"/api/devices/{device_id}/jobs/{job_id}",
                    token=ordinary,
                )
                proof.condition(
                    name + "_script_body_read",
                    detail["script_content"] == job_body["script_content"],
                )
                proof.request(
                    name + "_job_logs",
                    "GET",
                    f"/api/devices/{device_id}/jobs/{job_id}/logs",
                    token=ordinary,
                )
                cancelled = proof.request(
                    name + "_job_cancel",
                    "POST",
                    f"/api/devices/{device_id}/jobs/{job_id}/cancel",
                    token=ordinary,
                )
                proof.condition(
                    name + "_cancel_converged", cancelled["status"] == "cancelled"
                )
            key = proof.request(
                name + "_key_issue",
                "POST",
                "/api/device-control-keys",
                token=ordinary,
                body=key_body,
                expect=(201,) if manage and execute else (403,),
            )
            if manage and execute:
                key_id = key["control_key"]["id"]
                resources["keys"].append(key_id)
                raw = key["key"]
                metadata = proof.request(
                    "control_key_metadata",
                    "GET",
                    f"/api/device-control-keys/{key_id}",
                    token=ordinary,
                )
                proof.condition(
                    "metadata_has_no_secret",
                    "key" not in metadata and "key_hash" not in metadata,
                )
                rotated = proof.request(
                    "control_key_rotate",
                    "POST",
                    f"/api/device-control-keys/{key_id}/rotate",
                    token=ordinary,
                )
                proof.condition("rotation_changes_raw_key", rotated["key"] != raw)
                proof.request(
                    "old_raw_key_refused",
                    "GET",
                    f"/api/devices/{device_id}",
                    headers={"X-Bifrost-Control-Key": raw},
                    expect=(401,),
                )
                proof.request(
                    "fresh_rotated_key_accepted",
                    "GET",
                    f"/api/devices/{device_id}",
                    headers={"X-Bifrost-Control-Key": rotated["key"]},
                )
                proof.request(
                    "empty_key_allowlist_refused",
                    "POST",
                    "/api/device-control-keys",
                    token=ordinary,
                    body={"name": tag, "device_ids": []},
                    expect=(422,),
                )
        proof.request(
            "revoke_home_membership",
            "DELETE",
            f"/api/roles/{role_id}/users/{resources['user']}",
            token=admin,
            expect=(204,),
        )
        proof.request(
            "revoked_membership_same_session_registry_refused",
            "GET",
            "/api/devices",
            token=ordinary,
            expect=(403,),
        )
        proof.request(
            "revoked_membership_same_session_job_refused",
            "POST",
            f"/api/devices/{device_id}/jobs",
            token=ordinary,
            body=job_body,
            expect=(403,),
        )
    finally:
        if admin:
            cleanups = []
            cleanups += [
                (
                    "jobs_cancelled",
                    "POST",
                    f"/api/devices/{resources['device']}/jobs/{job}/cancel",
                    (200,),
                )
                for job in resources["jobs"]
            ]
            cleanups += [
                (
                    "keys_revoked",
                    "POST",
                    f"/api/device-control-keys/{key}/revoke",
                    (200,),
                )
                for key in resources["keys"]
            ]
            if resources["device"]:
                cleanups.append(
                    (
                        "devices_disabled",
                        "POST",
                        f"/api/devices/{resources['device']}/disable",
                        (200,),
                    )
                )
            if resources["user"]:
                cleanups.append(
                    (
                        "users_deleted",
                        "DELETE",
                        f"/api/users/{resources['user']}",
                        (204,),
                    )
                )
            if resources["role"]:
                cleanups.append(
                    (
                        "roles_deleted",
                        "DELETE",
                        f"/api/roles/{resources['role']}",
                        (204,),
                    )
                )
            if resources["org"]:
                cleanups.append(
                    (
                        "organizations_disabled",
                        "DELETE",
                        f"/api/organizations/{resources['org']}",
                        (204,),
                    )
                )
            failed = False
            for kind, method, path, expected in cleanups:
                try:
                    proof.request(
                        "cleanup_" + kind, method, path, token=admin, expect=expected
                    )
                    proof.cleanup[kind] = proof.cleanup.get(kind, 0) + 1
                except SmokeFailure:
                    failed = True
            if failed:
                raise SmokeFailure("synthetic_cleanup_incomplete")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostic-branch", required=True)
    parser.add_argument("--allow-fresh-admin-mfa-setup", action="store_true")
    args = parser.parse_args()
    receipt = {
        "passed": False,
        "checks": [],
        "cleanup": {},
        "limitations": [
            "Synthetic debug stack only; no real device agent or script execution",
            "No split-org, foreign allowlist, promotion, namespace or complete migration rehearsal proof",
            "Devices, cancelled jobs and audit records require parent debug-stack teardown for physical removal",
        ],
    }
    proof = None
    try:
        supplied = json.loads(sys.stdin.read(16_385))
        guard(args, supplied)
        receipt["candidate_sha"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        receipt["script_sha256"] = hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest()
        proof = Proof(supplied["url"])
        exercise(proof, supplied, args)
        receipt["passed"] = True
    except SmokeFailure as exc:
        receipt["failure"] = {"check": exc.step, "status": exc.status}
    except Exception as exc:
        # Exception text can contain request URLs, credentials or response data.
        receipt["failure"] = {"check": "unexpected_failure", "type": type(exc).__name__}
    if proof:
        receipt["checks"] = proof.checks
        receipt["cleanup"] = proof.cleanup
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
