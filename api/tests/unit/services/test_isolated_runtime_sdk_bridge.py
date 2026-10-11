"""Private bridge bytes/response fencing; not live owner/runtime acceptance."""

import hashlib
import json
import os
from uuid import UUID

import pytest

from src.services.isolated_runtime_sdk_bridge import (
    _accepted_response,
    _request_bytes,
    _start_ticks,
)
from src.services.isolated_runtime_sdk_tokens import FiniteIntegrationGetIntent


def intent() -> FiniteIntegrationGetIntent:
    return FiniteIntegrationGetIntent(
        grant_id=UUID("00000000-0000-0000-0000-000000000001"),
        grant_digest="a" * 64,
        integration_name="Fixture",
        organization_id=UUID("00000000-0000-0000-0000-000000000002"),
        solution_id=UUID("00000000-0000-0000-0000-000000000003"),
    )


def test_private_intent_canonical_bytes_contain_no_token_or_lifecycle_fence():
    raw = _request_bytes(intent())
    assert raw == (
        b'{"grant_digest":"'
        + b"a" * 64
        + b'","grant_id":"00000000-0000-0000-0000-000000000001",'
        b'"integration_name":"Fixture","organization_id":"00000000-0000-0000-0000-000000000002",'
        b'"solution_id":"00000000-0000-0000-0000-000000000003"}'
    )


@pytest.mark.parametrize(
    "change", ["denied", "hash", "unknown", "integer", "duplicate"]
)
def test_private_reply_cannot_upgrade_or_rebind_admission(change):
    request = _request_bytes(intent())
    response = {"admitted": True, "request_sha256": hashlib.sha256(request).hexdigest()}
    assert _accepted_response(json.dumps(response).encode(), request)
    if change == "denied":
        response["admitted"] = False
    elif change == "hash":
        response["request_sha256"] = "b" * 64
    elif change == "unknown":
        response["renewal"] = True
    elif change == "integer":
        response["admitted"] = 1
    else:
        with pytest.raises(ValueError):
            _accepted_response(
                json.dumps(response).replace("{", '{"admitted":false,', 1).encode(),
                request,
            )
        return
    assert not _accepted_response(json.dumps(response).encode(), request)


def test_original_process_identity_uses_actual_kernel_start_ticks():
    ticks = _start_ticks(os.getpid())
    assert ticks.isascii() and ticks.isdigit() and int(ticks) > 0
