"""Finite access facade tests; no row admission, issuer endpoint or renewal."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.core.runtime_sdk_credentials import (
    GrantReference,
    GrantSnapshot,
    RuntimeSDKDenied,
    SelectedSDKPolicy,
    caller_digest,
    decode_runtime_sdk_access,
    grant_digest,
    operations_digest,
    source_digest,
)
from src.core.security import decode_token
from src.services.isolated_runtime_sdk_tokens import sign_finite_runtime_sdk_access
from tests.unit.core.test_runtime_sdk_credentials import (
    _caller,
    _policy,
    _snapshot,
    _source,
)


def inputs():
    now = datetime.now(UTC).replace(microsecond=0)
    caller, source = _caller(), _source()
    policy = SelectedSDKPolicy(operations=(_policy().operations[0],))
    data = _snapshot().model_dump()
    data.update(
        started_at=now - timedelta(seconds=1),
        issued_at=now,
        credential_deadline=now + timedelta(seconds=20),
        initial_access_expires_at=now + timedelta(seconds=20),
        caller_snapshot_digest=caller_digest(caller),
        source_digest=source_digest(source),
        operations_digest=operations_digest(policy),
    )
    snapshot = GrantSnapshot(**data)
    reference = GrantReference(
        grant_id=snapshot.id, grant_digest=grant_digest(snapshot)
    )
    return snapshot, caller, source, policy, reference, now


def test_finite_access_preserves_preimages_and_rejects_ordinary_application_auth():
    snapshot, caller, source, policy, reference, now = inputs()
    credential = sign_finite_runtime_sdk_access(
        snapshot, caller, source, policy, reference, now=now
    )
    assert set(type(credential).model_fields) == {"access_token", "expires_at"}
    assert credential.access_token not in repr(credential)
    claims = decode_runtime_sdk_access(credential.access_token)
    assert (
        claims.sub == str(snapshot.id) and claims.grant_digest == reference.grant_digest
    )
    assert claims.purpose == "workflow-runtime-sdk/v1"
    assert credential.expires_at <= snapshot.initial_access_expires_at
    assert decode_token(credential.access_token, expected_type="access") is None
    assert decode_token(credential.access_token, expected_type="refresh") is None


@pytest.mark.parametrize(
    "field", ["caller_snapshot_digest", "source_digest", "operations_digest"]
)
def test_matching_grant_digest_cannot_hide_changed_authority_preimages(field):
    snapshot, caller, source, policy, _, now = inputs()
    snapshot = snapshot.model_copy(update={field: "f" * 64})
    reference = GrantReference(
        grant_id=snapshot.id, grant_digest=grant_digest(snapshot)
    )
    with pytest.raises(RuntimeSDKDenied, match="finite runtime SDK signing denied"):
        sign_finite_runtime_sdk_access(
            snapshot, caller, source, policy, reference, now=now
        )


@pytest.mark.parametrize(
    "change",
    [
        "unbounded",
        "expired",
        "future-issued",
        "deadline-extension",
        "caller-flags",
        "effective-org",
        "install",
        "extra-operation",
        "operation-name",
        "wrong-reference",
    ],
)
def test_finite_signing_denies_scope_time_and_policy_expansion(change):
    snapshot, caller, source, policy, _, now = inputs()
    update = {}
    if change == "unbounded":
        update = {"timeout_seconds": 0, "credential_deadline": None}
    elif change == "expired":
        update = {"credential_deadline": now, "initial_access_expires_at": now}
    elif change == "future-issued":
        update = {"issued_at": now + timedelta(seconds=1)}
    elif change == "deadline-extension":
        update = {
            "credential_deadline": now + timedelta(seconds=60),
            "initial_access_expires_at": now + timedelta(seconds=60),
        }
    elif change == "caller-flags":
        update = {"caller_admin": 1}
    elif change == "effective-org":
        update = {"effective_organization_id": uuid4()}
    elif change == "install":
        update = {"solution_install_id": uuid4()}
    elif change == "extra-operation":
        policy = _policy()
        update = {"operations_digest": operations_digest(policy)}
    elif change == "operation-name":
        policy = SelectedSDKPolicy(
            operations=(
                policy.operations[0].model_copy(
                    update={"integration_name": "DifferentIntegration"}
                ),
            )
        )
    snapshot = snapshot.model_copy(update=update)
    reference = GrantReference(
        grant_id=uuid4() if change == "wrong-reference" else snapshot.id,
        grant_digest=grant_digest(snapshot),
    )
    with pytest.raises(RuntimeSDKDenied) as caught:
        sign_finite_runtime_sdk_access(
            snapshot, caller, source, policy, reference, now=now
        )
    assert caught.value.__suppress_context__
    assert "DifferentIntegration" not in str(caught.value)


def ingress_inputs():
    import json

    from src.services.isolated_runtime_sdk_tokens import verify_finite_integration_get

    snapshot, caller, source, policy, reference, now = inputs()
    credential = sign_finite_runtime_sdk_access(
        snapshot, caller, source, policy, reference, now=now
    )
    operation = policy.operations[0]
    request = {
        "name": operation.integration_name,
        "scope": str(operation.scope_organization_id),
        "solution": str(operation.solution_install_id),
        "oauth_scope": None,
    }
    return (
        verify_finite_integration_get,
        credential.access_token,
        json.dumps(request).encode(),
        snapshot,
        caller,
        source,
        policy,
        now,
    )


def test_finite_sdk_ingress_preserves_exact_policy_without_returning_bearer():
    verify, token, body, snapshot, caller, source, policy, now = ingress_inputs()
    intent = verify(token, body, snapshot, caller, source, policy, now=now)
    assert intent.grant_id == snapshot.id
    assert intent.grant_digest == grant_digest(snapshot)
    assert intent.integration_name == policy.operations[0].integration_name
    assert intent.organization_id == snapshot.effective_organization_id
    assert intent.solution_id == snapshot.solution_install_id
    assert token not in repr(intent)
    assert "access_token" not in type(intent).model_fields


@pytest.mark.parametrize(
    "change",
    [
        "empty",
        "oversize",
        "invalid-utf8",
        "non-object",
        "duplicate",
        "unknown",
        "wrong-name",
        "global",
        "default-scope",
        "other-org",
        "other-install",
        "alias-solution-id",
        "oauth-override",
        "oauth-false",
        "nested",
        "missing-scope",
        "noncanonical-uuid",
        "array-name",
    ],
)
def test_finite_sdk_ingress_rejects_scope_aliases_and_ambiguous_requests(change):
    import json

    verify, token, body, snapshot, caller, source, policy, now = ingress_inputs()
    request = json.loads(body)
    if change == "empty":
        body = b""
    elif change == "oversize":
        body = b" " * 8193
    elif change == "invalid-utf8":
        body = b"\xff"
    elif change == "non-object":
        body = b"null"
    elif change == "duplicate":
        body = b'{"name":"bad",' + body[1:]
    else:
        if change == "unknown":
            request["execution_id"] = str(snapshot.execution_id)
        elif change == "wrong-name":
            request["name"] = "Other"
        elif change == "global":
            request["scope"] = "global"
        elif change == "default-scope":
            request["scope"] = None
        elif change == "other-org":
            request["scope"] = str(uuid4())
        elif change == "other-install":
            request["solution"] = str(uuid4())
        elif change == "alias-solution-id":
            request["solution_id"] = request.pop("solution")
        elif change == "oauth-override":
            request["oauth_scope"] = "vendor.write"
        elif change == "oauth-false":
            request["oauth_scope"] = False
        elif change == "nested":
            request["scope"] = {"scope": request["scope"]}
        elif change == "missing-scope":
            del request["scope"]
        elif change == "noncanonical-uuid":
            request["scope"] = request["scope"].replace("-", "")
        elif change == "array-name":
            request["name"] = [request["name"]]
        body = json.dumps(request).encode()
    with pytest.raises(RuntimeSDKDenied, match="finite runtime SDK request denied"):
        verify(token, body, snapshot, caller, source, policy, now=now)


@pytest.mark.parametrize("change", ["caller", "source", "policy", "grant", "clock"])
def test_finite_sdk_ingress_rechecks_parent_preimages_and_finite_time(change):
    verify, token, body, snapshot, caller, source, policy, now = ingress_inputs()
    if change == "caller":
        caller = caller.model_copy(update={"roles": ("Other",)})
    elif change == "source":
        source = source.model_copy(
            update={"source_manifest_digest": "sha256:" + "f" * 64}
        )
    elif change == "policy":
        policy = SelectedSDKPolicy(operations=())
    elif change == "grant":
        snapshot = snapshot.model_copy(update={"execution_id": uuid4()})
    else:
        now = snapshot.initial_access_expires_at
    with pytest.raises(RuntimeSDKDenied):
        verify(token, body, snapshot, caller, source, policy, now=now)


@pytest.mark.parametrize("change", ["expiry", "issue-time"])
def test_finite_sdk_ingress_rejects_valid_signed_token_with_changed_clock(change):
    from src.core.runtime_sdk_credentials import sign_runtime_sdk_token

    verify, _, body, snapshot, caller, source, policy, now = ingress_inputs()
    extended = sign_runtime_sdk_token(
        GrantReference(grant_id=snapshot.id, grant_digest=grant_digest(snapshot)),
        issued_at=snapshot.issued_at
        - (timedelta(seconds=1) if change == "issue-time" else timedelta()),
        expires_at=snapshot.initial_access_expires_at
        + (timedelta(seconds=1) if change == "expiry" else timedelta()),
    )
    with pytest.raises(RuntimeSDKDenied):
        verify(extended.access_token, body, snapshot, caller, source, policy, now=now)


def test_finite_sdk_ingress_rejects_invalid_signature_without_leaking_token():
    verify, token, body, snapshot, caller, source, policy, now = ingress_inputs()
    invalid = "invalid-signature"
    with pytest.raises(RuntimeSDKDenied) as caught:
        verify(invalid, body, snapshot, caller, source, policy, now=now)
    assert invalid not in str(caught.value) and token not in str(caught.value)
    assert caught.value.__suppress_context__
