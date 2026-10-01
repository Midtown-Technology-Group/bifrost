"""Private wire/digest contracts, without a runtime or public auth hook."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from pydantic import ValidationError
from src.config import get_settings
from src.core.runtime_sdk_credentials import (
    GRANT_FIELDS,
    MAX_TIMESTAMP_US,
    RUNTIME_SDK_AUDIENCE,
    AcceptedManifestIdentity,
    AuthorizedCallerSnapshot,
    CommittedWorkflowStart,
    GrantReference,
    GrantSnapshot,
    RuntimeSDKDenied,
    SDKOperation,
    SelectedSDKPolicy,
    caller_digest,
    claim_digest,
    decode_runtime_sdk_access,
    epoch_us,
    grant_digest,
    operations_digest,
    sign_runtime_sdk_token,
    source_digest,
    utc_from_us,
)
from src.core.security import decode_token
from src.models.orm.runtime_sdk_grants import WorkflowRuntimeSDKGrant


def _uuid(number: int) -> UUID:
    return UUID(f"00000000-0000-0000-0000-{number:012d}")


def _caller() -> AuthorizedCallerSnapshot:
    return AuthorizedCallerSnapshot(
        caller_user_id=_uuid(6),
        caller_organization_id=_uuid(7),
        effective_organization_id=_uuid(7),
        caller_email="unit@example.invalid",
        caller_name="Readiness",
        caller_admin=False,
        caller_provider=False,
        caller_external=True,
        roles=("Reader",),
    )


def _source() -> AcceptedManifestIdentity:
    return AcceptedManifestIdentity(
        source_id=_uuid(10),
        solution_install_id=_uuid(9),
        source_manifest_digest="sha256:" + "a" * 64,
        source_resolution_digest="sha256:" + "b" * 64,
        source_global_permission=False,
    )


def _policy() -> SelectedSDKPolicy:
    return SelectedSDKPolicy(
        operations=tuple(
            SDKOperation(
                operation=operation,
                integration_name="Example",
                scope_kind="organization",
                scope_organization_id=_uuid(7),
                resolved_organization_id=_uuid(7),
                solution_install_id=_uuid(9)
                if operation == "integration-get"
                else None,
            )
            for operation in ("integration-get", "mapping-get")
        )
    )


def _snapshot() -> GrantSnapshot:
    return GrantSnapshot(
        id=_uuid(1),
        schema_version="cred-p1/v1",
        workflow_attempt_id=_uuid(2),
        execution_id=_uuid(3),
        attempt_number=1,
        claim_token_digest="c" * 64,
        worker_incarnation_id=_uuid(4),
        supervisor_incarnation_id=_uuid(5),
        runtime_session_id=_uuid(6),
        started_at=utc_from_us(1700000000000000),
        issued_at=utc_from_us(1700000001000000),
        timeout_seconds=30,
        credential_deadline=utc_from_us(1700000330000000),
        initial_access_expires_at=utc_from_us(1700000330000000),
        caller_user_id=_uuid(6),
        caller_organization_id=_uuid(7),
        effective_organization_id=_uuid(7),
        caller_email="unit@example.invalid",
        caller_name="Readiness",
        caller_admin=0,
        caller_provider=0,
        caller_external=1,
        caller_snapshot_digest="1" * 64,
        workflow_id=_uuid(8),
        solution_install_id=_uuid(9),
        source_kind="solution-deployment",
        source_id=_uuid(10),
        source_manifest_digest="sha256:" + "a" * 64,
        source_resolution_digest="sha256:" + "b" * 64,
        source_global_permission=0,
        source_digest="2" * 64,
        operations_digest="3" * 64,
    )


def test_frozen_synthetic_private_codec_vectors():
    # Derived independently from explicit length-framed synthetic fields.
    assert (
        claim_digest(_uuid(1))
        == "410e30dc8a6f8dafec1ece1c66c4f4a80600f84b9b78f90787b721ae90420406"
    )
    assert (
        caller_digest(_caller())
        == "a61d01f9c09ae947992fc13b9d6f82dc3238ada0935988a8cb2c17d6170238b1"
    )
    assert (
        source_digest(_source())
        == "eed0c882eeb877d861baf3350eecc1ffdd21f06ec42c2890f30e06793a399999"
    )
    assert (
        operations_digest(_policy())
        == "e9e584f492eb766878ad012219de34367620ee3ccf0f4f08f3c63b35d462e5f8"
    )
    assert (
        grant_digest(_snapshot())
        == "d6b89b1970dfd8b7c4fa4c2781f853c6d06e0266cd4110356171944a793eba64"
    )
    table_fields = set(WorkflowRuntimeSDKGrant.__table__.columns.keys()) - {
        "grant_digest",
        "revoked_at",
        "revocation_reason",
    }
    assert set(GRANT_FIELDS) == table_fields == set(GrantSnapshot.model_fields)


@pytest.mark.parametrize("field", GRANT_FIELDS)
def test_every_immutable_field_is_covered_by_the_grant_digest(field):
    snapshot = _snapshot()
    data = snapshot.model_dump()
    value = data[field]
    if field in {"schema_version", "source_kind"}:
        # Version/kind changes are rejected rather than accepted as another profile.
        data[field] = "unknown-profile"
        with pytest.raises(ValidationError):
            GrantSnapshot.model_validate(data)
        return
    if field in {"credential_deadline", "initial_access_expires_at"}:
        data["credential_deadline"] = data["initial_access_expires_at"] = (
            value + timedelta(microseconds=1)
        )
    elif isinstance(value, UUID):
        data[field] = uuid4()
    elif isinstance(value, datetime):
        data[field] = value + timedelta(microseconds=1)
    elif type(value) is int:
        data[field] = (
            1 - value if value in {0, 1} and field != "attempt_number" else value + 1
        )
    elif field.endswith("digest"):
        data[field] = ("sha256:" if value.startswith("sha256:") else "") + "f" * 64
    else:
        data[field] = value + "x"
    assert grant_digest(GrantSnapshot.model_validate(data)) != grant_digest(snapshot)


@pytest.mark.parametrize("bad", [1, "true", None])
def test_caller_flags_are_strict_booleans_even_after_model_copy(bad):
    caller = _caller().model_copy(update={"caller_admin": bad})
    with pytest.raises(ValidationError):
        caller_digest(caller)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("caller_email", "é" * 161),
        ("caller_name", "é" * 128),
        ("caller_name", "hidden\x00marker"),
        ("caller_name", "hidden\ud800marker"),
        ("roles", ("z", "a")),
        ("roles", ("x", "x")),
        ("roles", tuple(f"role-{index:03d}" for index in range(257))),
        ("roles", ("é" * 128,)),
    ],
)
def test_caller_byte_bounds_and_roles_are_closed(field, bad):
    with pytest.raises(ValidationError) as error:
        AuthorizedCallerSnapshot.model_validate({**_caller().model_dump(), field: bad})
    assert "input_value=" not in str(error.value)


@pytest.mark.parametrize(
    "bad",
    ["a" * 64, "sha256:" + "A" * 64, "sha256:" + "a" * 63, "sha256:" + "a" * 64 + "\n"],
)
def test_existing_source_hash_prefix_and_exact_length_are_preserved(bad):
    with pytest.raises(ValidationError):
        AcceptedManifestIdentity.model_validate(
            {**_source().model_dump(), "source_manifest_digest": bad}
        )


def test_timestamp_boundaries_and_optional_tags():
    assert epoch_us(utc_from_us(MAX_TIMESTAMP_US)) == MAX_TIMESTAMP_US
    assert epoch_us(utc_from_us(0)) == 0
    for bad in (True, 1.5, "1", -1, MAX_TIMESTAMP_US + 1):
        with pytest.raises(ValueError):
            utc_from_us(bad)
    with pytest.raises(ValueError):
        epoch_us(datetime(2026, 1, 1))  # noqa: DTZ001 - deliberate invalid input
    data = _snapshot().model_dump()
    data["caller_organization_id"] = None
    assert grant_digest(GrantSnapshot.model_validate(data)) != grant_digest(_snapshot())


def _valid_token():
    now = datetime.now(UTC)
    return sign_runtime_sdk_token(
        GrantReference(grant_id=uuid4(), grant_digest="a" * 64),
        issued_at=now,
        expires_at=now + timedelta(seconds=60),
    )


def test_exact_token_shape_dedicated_audience_and_secret_repr():
    bundle = _valid_token()
    claims = decode_runtime_sdk_access(bundle.access_token)
    assert set(claims.model_dump()) == {
        "iss",
        "aud",
        "sub",
        "type",
        "purpose",
        "grant_digest",
        "iat",
        "exp",
        "jti",
    }
    assert jwt.get_unverified_header(bundle.access_token) == {
        "alg": get_settings().algorithm,
        "typ": "JWT",
    }
    assert decode_token(bundle.access_token, expected_type="access") is None
    assert bundle.refresh_token == bundle.access_token
    assert bundle.access_token not in repr(bundle)
    assert "engine_attempt_token" not in claims.model_dump()


@pytest.mark.parametrize(
    "field,bad",
    [
        ("iss", "wrong-issuer"),
        ("aud", "bifrost-client"),
        ("aud", [RUNTIME_SDK_AUDIENCE]),
        ("type", "refresh"),
        ("purpose", "other-purpose"),
        ("extra", "claim-marker"),
        ("exp", True),
        ("exp", 253402300799.5),
        ("exp", "253402300799"),
        ("iat", False),
        ("iat", 0.5),
        ("iat", "0"),
        ("sub", "not-a-uuid"),
        ("grant_digest", "invalid"),
    ],
)
def test_signed_wrong_claims_are_denied_without_echoing_input(field, bad):
    settings = get_settings()
    payload = decode_runtime_sdk_access(_valid_token().access_token).model_dump()
    payload[field] = bad
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)
    with pytest.raises(RuntimeSDKDenied) as error:
        decode_runtime_sdk_access(token)
    assert token not in str(error.value) and "claim-marker" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize(
    "header",
    [
        {"kid": "key-marker"},
        {"jku": "https://invalid.example"},
        {"crit": ["b64"]},
        {"b64": False},
        {"typ": "other"},
    ],
)
def test_unknown_jwt_header_authority_is_denied(header):
    settings = get_settings()
    payload = decode_runtime_sdk_access(_valid_token().access_token).model_dump()
    token = jwt.encode(
        payload, settings.secret_key, algorithm=settings.algorithm, headers=header
    )
    with pytest.raises(RuntimeSDKDenied):
        decode_runtime_sdk_access(token)


def test_bad_signature_algorithm_missing_claim_expiry_and_audience_collision(
    monkeypatch,
):
    settings = get_settings()
    payload = decode_runtime_sdk_access(_valid_token().access_token).model_dump()
    for token in (
        jwt.encode(
            payload,
            "synthetic-untrusted-signing-key-at-least-32-bytes",
            algorithm=settings.algorithm,
        ),
        jwt.encode(payload, settings.secret_key, algorithm="HS384"),
        jwt.encode(
            {key: value for key, value in payload.items() if key != "jti"},
            settings.secret_key,
            algorithm=settings.algorithm,
        ),
    ):
        with pytest.raises(RuntimeSDKDenied):
            decode_runtime_sdk_access(token)
    payload["exp"] = payload["iat"] - 1
    expired = jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)
    with pytest.raises(RuntimeSDKDenied):
        decode_runtime_sdk_access(expired)
    assert (
        decode_runtime_sdk_access(expired, allow_expired_for_renewal=True).exp
        == payload["exp"]
    )
    with pytest.raises(RuntimeSDKDenied):
        decode_runtime_sdk_access("synthetic-token-marker" * 4096)
    monkeypatch.setattr(settings, "jwt_audience", RUNTIME_SDK_AUDIENCE)
    with pytest.raises(RuntimeSDKDenied):
        _valid_token()


def test_expiry_floor_and_private_input_errors_do_not_expose_claims():
    now = datetime.now(UTC)
    with pytest.raises(RuntimeSDKDenied):
        sign_runtime_sdk_token(
            GrantReference(grant_id=uuid4(), grant_digest="a" * 64),
            issued_at=now,
            expires_at=now.replace(microsecond=999999),
        )
    claim = _uuid(99)
    start = CommittedWorkflowStart(
        execution_id=uuid4(),
        workflow_attempt_id=uuid4(),
        attempt_number=1,
        private_claim_token=claim,
        worker_incarnation_id=uuid4(),
        supervisor_incarnation_id=uuid4(),
        runtime_session_id=uuid4(),
        committed_started_at_us=epoch_us(now),
        timeout_seconds=0,
        predeclared_credential_deadline_us=None,
        predeclared_initial_access_expires_at_us=epoch_us(now + timedelta(seconds=600)),
    )
    assert str(claim) not in repr(start)
    with pytest.raises(ValidationError) as error:
        CommittedWorkflowStart.model_validate(
            {**start.model_dump(), "timeout_seconds": "synthetic-input-marker"}
        )
    assert "synthetic-input-marker" not in str(error.value)
    assert "synthetic-input-marker" not in repr(error.value)


def test_signing_failure_does_not_echo_producer_input(monkeypatch):
    def reject_signing(*_args, **_kwargs):
        raise ValueError("synthetic-credential-marker")

    monkeypatch.setattr(jwt, "encode", reject_signing)
    with pytest.raises(RuntimeSDKDenied) as error:
        _valid_token()
    assert "synthetic-credential-marker" not in str(error.value)
    assert "synthetic-credential-marker" not in repr(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize(
    "changes",
    [
        {"integration_name": "é" * 128},
        {"integration_name": " hidden "},
        {"integration_name": "hidden\x00marker"},
        {"integration_name": "hidden\ud800marker"},
        {"operation": "lifecycle-finalize"},
        {"scope_kind": "unknown"},
        {"oauth_scope": "organization"},
        {"entity_scope": "all"},
    ],
)
def test_sdk_operation_fields_are_closed_and_byte_bounded(changes):
    with pytest.raises(ValidationError):
        SDKOperation.model_validate({**_policy().operations[0].model_dump(), **changes})


def test_policy_cardinality_order_and_mapping_selector_are_closed():
    operations = _policy().operations
    for invalid in (
        (operations[0], operations[0]),
        tuple(reversed(operations)),
        operations + (operations[0],),
    ):
        with pytest.raises(ValidationError):
            SelectedSDKPolicy(operations=invalid)
    with pytest.raises(ValidationError):
        SDKOperation.model_validate(
            {**operations[1].model_dump(), "solution_install_id": uuid4()}
        )
