from __future__ import annotations

import importlib.util
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from uuid import uuid4

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "scheduler_fixture_server.py"
spec = importlib.util.spec_from_file_location("scheduler_fixture_server", MODULE_PATH)
assert spec and spec.loader
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def serve_fixture():
    server = fixture.ReferenceFixtureServer(("127.0.0.1", 0), fixture.FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def post_form(base_url: str, data: dict[str, str]):
    body = urlencode(data).encode()
    request = Request(
        f"{base_url}/oauth/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read())


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def test_authorize_redirect_requires_fixture_client_and_pkce():
    for server in serve_fixture():
        base_url = f"http://127.0.0.1:{server.server_port}"
        redirect_uri = "http://client:80/api/mcp/oauth/callback"
        authorize_url = (
            f"{base_url}/oauth/authorize?"
            + urlencode(
                {
                    "client_id": "scheduler-fixture-client",
                    "response_type": "code",
                    "state": "opaque-state",
                    "redirect_uri": redirect_uri,
                    "code_challenge": "challenge",
                    "code_challenge_method": "S256",
                }
            )
        )
        opener = build_opener(NoRedirect)
        try:
            opener.open(authorize_url, timeout=5)
        except HTTPError as exc:
            assert exc.code == 302
            final_url = exc.headers["Location"]
        else:  # pragma: no cover - failure branch
            raise AssertionError("fixture authorize endpoint did not redirect")
        parsed = urlparse(final_url)
        query = parse_qs(parsed.query)
        assert final_url.startswith(redirect_uri)
        assert query["code"] == ["scheduler-fixture-code"]
        assert query["state"] == ["opaque-state"]
        assert query["iss"] == ["http://scheduler-fixtures:8080"]


def test_authorize_rejects_wrong_client():
    for server in serve_fixture():
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            urlopen(
                f"{base_url}/oauth/authorize?client_id=wrong&response_type=code&state=s&"
                "redirect_uri=http://client:80/api/mcp/oauth/callback&"
                "code_challenge=challenge&code_challenge_method=S256",
                timeout=5,
            )
        except HTTPError as exc:
            assert exc.code == 400
            assert json.loads(exc.read()) == {"error": "invalid_request"}
        else:  # pragma: no cover - failure branch
            raise AssertionError("fixture accepted wrong OAuth client")


def test_authorization_code_exchange_requires_exact_code_client_secret_and_pkce():
    for server in serve_fixture():
        base_url = f"http://127.0.0.1:{server.server_port}"
        status, payload = post_form(
            base_url,
            {
                "grant_type": "authorization_code",
                "code": "scheduler-fixture-code",
                "client_id": "scheduler-fixture-client",
                "client_secret": "scheduler-fixture-secret",
                "redirect_uri": "http://client:80/api/mcp/oauth/callback",
                "code_verifier": "x" * 32,
            },
        )
        assert status == 200
        assert payload["access_token"] == "scheduler-fixture-access-authorized"
        assert payload["refresh_token"] == "scheduler-fixture-refresh"
        assert payload["scope"] == "fixture.read"


def test_authorization_code_exchange_rejects_wrong_code():
    for server in serve_fixture():
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            post_form(
                base_url,
                {
                    "grant_type": "authorization_code",
                    "code": "wrong",
                    "client_id": "scheduler-fixture-client",
                    "client_secret": "scheduler-fixture-secret",
                    "redirect_uri": "http://client:80/api/mcp/oauth/callback",
                    "code_verifier": "x" * 32,
                },
            )
        except HTTPError as exc:
            assert exc.code == 400
            payload = json.loads(exc.read())
            assert payload["error"] == "invalid_grant"
        else:  # pragma: no cover - failure branch
            raise AssertionError("fixture accepted wrong authorization code")


def test_refresh_token_exchange_still_uses_existing_contract():
    for server in serve_fixture():
        base_url = f"http://127.0.0.1:{server.server_port}"
        status, payload = post_form(
            base_url,
            {
                "grant_type": "refresh_token",
                "refresh_token": "scheduler-fixture-refresh",
                "client_id": "scheduler-fixture-client",
                "client_secret": "scheduler-fixture-secret",
            },
        )
        assert status == 200
        assert payload["access_token"] == "scheduler-fixture-access-refreshed"
        assert payload["refresh_token"] == "scheduler-fixture-refresh"
        assert payload["scope"] == "fixture.read"


def test_authorize_rejects_redirect_header_line_breaks():
    for server in serve_fixture():
        base_url = f"http://127.0.0.1:{server.server_port}"
        for line_break in ("\r", "\n", "\r\n"):
            query = urlencode({
                "client_id": "scheduler-fixture-client",
                "response_type": "code",
                "state": "opaque-state",
                "redirect_uri": f"http://client/{line_break}X-Injected: value/api/mcp/oauth/callback",
                "code_challenge": "challenge",
                "code_challenge_method": "S256",
            })
            try:
                build_opener(NoRedirect).open(f"{base_url}/oauth/authorize?{query}", timeout=5)
            except HTTPError as exc:
                assert exc.code == 400
                assert exc.headers.get("X-Injected") is None
                assert exc.headers.get("Location") is None
                assert json.loads(exc.read()) == {"error": "invalid_request"}
            else:
                raise AssertionError("fixture accepted a redirect with a header line break")


def _reference_form(marker: str) -> dict[str, str]:
    return {
        "grant_type": "client_credentials",
        "client_id": fixture.FIXTURE_OAUTH_CLIENT_ID,
        "client_secret": fixture.FIXTURE_OAUTH_CLIENT_SECRET,
        "scope": fixture.FIXTURE_OAUTH_SCOPE,
        "audience": marker,
    }


def _receipt(base_url: str, key: str):
    with urlopen(f"{base_url}/__reference/oauth-receipts/{key}", timeout=5) as response:
        return json.loads(response.read())


def test_reference_receipts_count_actual_http_success_and_failure_without_sensitive_data(capsys):
    key, other = uuid4().hex, uuid4().hex
    for server in serve_fixture():
        base_url = f"http://127.0.0.1:{server.server_port}"
        assert _receipt(base_url, key) == {"attempted": 0, "statuses": {}, "exhausted": False}
        marker = fixture.REFERENCE_AUDIENCE_PREFIX + key
        status, _ = post_form(base_url, _reference_form(marker))
        assert status == 200
        wrong = _reference_form(marker)
        wrong["client_secret"] = "private-reference-secret-marker"
        with pytest.raises(HTTPError) as error:
            post_form(base_url, wrong)
        assert error.value.code == 400
        assert _receipt(base_url, key) == {
            "attempted": 2, "statuses": {"200": 1, "400": 1}, "exhausted": False,
        }
        assert _receipt(base_url, other) == {"attempted": 0, "statuses": {}, "exhausted": False}
        # Receipts contain only finite counters. The server suppresses request logs.
        receipt = json.dumps(_receipt(base_url, key))
        assert "private-reference-secret-marker" not in receipt
        assert fixture.FIXTURE_MCP_ACCESS_TOKEN not in receipt
        captured = capsys.readouterr()
        assert "private-reference-secret-marker" not in captured.out + captured.err


@pytest.mark.parametrize("suffix", ["", "A" * 32, "a" * 31, "a" * 33, "a" * 32 + "/bad"])
def test_reference_receipts_reject_invalid_markers_without_echo(suffix):
    marker = fixture.REFERENCE_AUDIENCE_PREFIX + suffix
    for server in serve_fixture():
        base_url = f"http://127.0.0.1:{server.server_port}"
        with pytest.raises(HTTPError) as error:
            post_form(base_url, _reference_form(marker))
        assert error.value.code == 400
        assert json.loads(error.value.read()) == {"error": "invalid_reference_marker"}
        with pytest.raises(HTTPError) as error:
            _receipt(base_url, suffix)
        assert error.value.code == 400
        assert json.loads(error.value.read()) == {"error": "invalid_reference_marker"}


def test_reference_receipts_are_thread_safe_and_available_before_response():
    key = uuid4().hex
    for server in serve_fixture():
        base_url = f"http://127.0.0.1:{server.server_port}"
        form = _reference_form(fixture.REFERENCE_AUDIENCE_PREFIX + key)
        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(lambda _, base_url=base_url, form=form: post_form(base_url, form)[0], range(24)))
        assert statuses == [200] * 24
        assert _receipt(base_url, key) == {"attempted": 24, "statuses": {"200": 24}, "exhausted": False}


def test_reference_receipt_capacity_is_exact_without_eviction_or_reset():
    for server in serve_fixture():
        # Own this fresh server's ledger; fill exactly its immutable capacity.
        for index in range(1024):
            key = f"{index:032x}"
            slot, rejection = server.reference_receipts.admit(key)
            assert rejection is None
            server.reference_receipts.record(key, slot, 200)
        base_url = f"http://127.0.0.1:{server.server_port}"
        extra = f"{1024:032x}"
        with pytest.raises(HTTPError) as error:
            post_form(base_url, _reference_form(fixture.REFERENCE_AUDIENCE_PREFIX + extra))
        assert error.value.code == 503
        assert json.loads(error.value.read()) == {"error": "reference_receipt_capacity"}
        assert _receipt(base_url, extra) == {"attempted": 0, "statuses": {}, "exhausted": False}
        # An existing scenario still works; capacity did not evict the first key.
        first = "0" * 32
        assert post_form(base_url, _reference_form(fixture.REFERENCE_AUDIENCE_PREFIX + first))[0] == 200
        assert _receipt(base_url, first) == {"attempted": 2, "statuses": {"200": 2}, "exhausted": False}
        with pytest.raises(HTTPError) as error:
            urlopen(f"{base_url}/__reference/oauth-receipts/reset", timeout=5)
        assert error.value.code == 400


def _fill_attempts(server, key: str, count: int):
    # Synthetic owned unit setup, not proof these were provider HTTP exchanges.
    for _ in range(count):
        slot, rejection = server.reference_receipts.admit(key)
        assert rejection is None
        server.reference_receipts.record(key, slot, 200)


def test_reference_attempt_bound_retains_evidence_and_permanent_http_exhaustion():
    key = uuid4().hex
    for server in serve_fixture():
        _fill_attempts(server, key, 65535)
        base_url = f"http://127.0.0.1:{server.server_port}"
        assert _receipt(base_url, key) == {
            "attempted": 65535, "statuses": {"200": 65535}, "exhausted": False,
        }
        for _ in range(2):
            with pytest.raises(HTTPError) as error:
                post_form(base_url, _reference_form(fixture.REFERENCE_AUDIENCE_PREFIX + key))
            assert error.value.code == 503
            assert json.loads(error.value.read()) == {"error": "reference_receipt_exhausted"}
            assert _receipt(base_url, key) == {
                "attempted": 65535, "statuses": {"200": 65535}, "exhausted": True,
            }


def test_reference_concurrent_http_admission_cannot_exceed_attempt_bound():
    key = uuid4().hex
    for server in serve_fixture():
        _fill_attempts(server, key, 65533)
        base_url = f"http://127.0.0.1:{server.server_port}"
        form = _reference_form(fixture.REFERENCE_AUDIENCE_PREFIX + key)

        def exchange(base_url=base_url, form=form):
            try:
                return post_form(base_url, form)[0]
            except HTTPError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=4) as pool:
            statuses = list(pool.map(lambda _, exchange=exchange: exchange(), range(4)))
        assert sorted(statuses) == [200, 200, 503, 503]
        assert _receipt(base_url, key) == {
            "attempted": 65535, "statuses": {"200": 65535}, "exhausted": True,
        }


def test_reference_statuses_are_strict_bounded_and_recorded_once_per_admitted_slot():
    key = uuid4().hex
    ledger = fixture.ReferenceOAuthReceipts()
    slot, rejection = ledger.admit(key)
    assert rejection is None
    for status in (True, 200.0, "200", 99, 600):
        with pytest.raises(ValueError, match="^invalid_reference_status$"):
            ledger.record(key, slot, status)
    ledger.record(key, slot, 200)
    with pytest.raises(ValueError, match="^reference_slot_not_pending$"):
        ledger.record(key, slot, 200)
    assert ledger.snapshot(key) == {"attempted": 1, "statuses": {"200": 1}, "exhausted": False}
    # All possible status keys are finite HTTP integers; no arbitrary input keys.
    for status in range(100, 600):
        slot, rejection = ledger.admit(key)
        assert rejection is None
        ledger.record(key, slot, status)
    snapshot = ledger.snapshot(key)
    assert snapshot["attempted"] == 501
    assert len(snapshot["statuses"]) == 500


def test_reference_already_admitted_slot_finishes_after_exhaustion():
    key = uuid4().hex
    ledger = fixture.ReferenceOAuthReceipts()
    for _ in range(65535):
        slot, rejection = ledger.admit(key)
        assert rejection is None
    assert ledger.admit(key) == (None, "reference_receipt_exhausted")
    ledger.record(key, slot, 200)
    assert ledger.snapshot(key) == {
        "attempted": 65535, "statuses": {"200": 1}, "exhausted": True,
    }
