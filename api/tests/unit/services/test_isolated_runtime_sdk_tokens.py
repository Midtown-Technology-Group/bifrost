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
