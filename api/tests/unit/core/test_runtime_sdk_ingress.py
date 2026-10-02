"""Closed H admission: bounded hints, selectors and real committed PG facts.

The imported foundation fixtures own synthetic source/history until test-stack
teardown. Only context cleanup tests replace the loader; those are local lifetime
tests, never evidence of public authorization or a live registration.
"""

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from asyncpg.exceptions import CheckViolationError
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import delete, event, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool
from starlette.requests import Request

from src.config import get_settings
from src.core import runtime_sdk_ingress as ingress
from src.core.request_context import (
    RequestUser,
    get_request_session_id,
    get_request_user,
    set_request_session_id,
    set_request_user,
)
from src.core.runtime_sdk_credentials import (
    RUNTIME_SDK_AUDIENCE,
    RuntimeSDKDenied,
    SelectedSDKPolicy,
    caller_digest,
)
from src.core.security import ENGINE_USER_ID, decode_token
from src.models.contracts.cli import (
    SDKIntegrationsGetMappingRequest,
    SDKIntegrationsGetRequest,
)
from src.models.orm.executions import WorkflowExecutionAttempt
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, User, UserRole
from src.services import runtime_sdk_grants as grants
from src.services.audit_context import (
    ActorContext,
    clear_actor,
    current_actor,
    set_actor,
)
from tests.unit.services import test_runtime_sdk_grants as foundation

# Export the reviewed real fixtures into this module's pytest namespace.
credential_cohort = foundation.credential_cohort
make_work = foundation.make_work
_bundle = foundation._bundle


def _require(condition, message):
    if not condition:
        raise AssertionError(message)


def _request(*, token=None, cookies=None, headers=(), body=None):
    raw_headers = [(b"content-type", b"application/json"), *headers]
    if token is not None:
        raw_headers.append((b"authorization", f"Bearer {token}".encode()))
    if cookies:
        raw_headers.append(
            (b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode())
        )
    encoded = json.dumps(body or {}).encode()

    async def receive():
        return {"type": "http.request", "body": encoded, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/sdk/integrations/get",
            "headers": raw_headers,
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "query_string": b"",
        },
        receive,
    )


def _hint(payload):
    encoded = base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()
    return f"e30.{encoded}.AA"


def _ordinary(*, kind="access", reserved=None, large=False, expired_engine=False):
    settings = get_settings()
    claims = {
        "sub": ENGINE_USER_ID if expired_engine else str(uuid4()),
        "email": "synthetic@example.invalid",
        "name": "Synthetic",
        "is_superuser": True,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "type": kind,
        "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
    }
    if reserved == "purpose":
        claims["purpose"] = "workflow-runtime-sdk/v1"
    if reserved == "audience":
        claims["aud"] = [settings.jwt_audience, RUNTIME_SDK_AUDIENCE]
    if large:
        claims["synthetic_padding"] = "x" * 5000
    if expired_engine:
        claims.update(
            engine=True,
            engine_renewable=True,
            type="access",
            exp=int((datetime.now(UTC) - timedelta(minutes=5)).timestamp()),
        )
    return jwt.encode(claims, settings.secret_key, algorithm=settings.algorithm)


@pytest.mark.parametrize(
    "case",
    [
        "exact",
        "audience",
        "purpose",
        "array",
        "duplicate",
        "nested_duplicate",
        "nonfinite",
        "wrong_shape",
        "application_purpose",
    ],
)
def test_reserved_unsigned_hint_never_admits_legacy(case):
    payloads = {
        "exact": '{"aud":"bifrost-workflow-runtime-sdk","purpose":"workflow-runtime-sdk/v1"}',
        "audience": '{"aud":"bifrost-workflow-runtime-sdk"}',
        "purpose": '{"purpose":"workflow-runtime-sdk/v1"}',
        "array": '{"aud":["bifrost-workflow-runtime-sdk"]}',
        "duplicate": '{"aud":"bifrost-workflow-runtime-sdk","aud":"ordinary"}',
        "nested_duplicate": '{"aud":"bifrost-workflow-runtime-sdk","nested":{"a":1,"a":2}}',
        "nonfinite": '{"aud":"bifrost-workflow-runtime-sdk","value":NaN}',
        "wrong_shape": '{"aud":["bifrost-workflow-runtime-sdk",42]}',
        "application_purpose": json.dumps(
            {"aud": get_settings().jwt_audience, "purpose": "workflow-runtime-sdk/v1"}
        ),
    }
    token = _hint(payloads[case])
    classification = ingress.classify_sdk_ingress(
        _request(token=token), selected_token=token, selected_location="bearer"
    )
    expected = "dedicated" if case == "exact" else "deny"
    _require(classification == expected, "Reserved hint classification changed")


@pytest.mark.parametrize("renewal", [False, True])
@pytest.mark.parametrize("marker", ["purpose", "audience"])
def test_signed_oversized_reserved_ordinary_backstop_denies(renewal, marker):
    token = _ordinary(
        kind="refresh" if renewal else "access", reserved=marker, large=True
    )
    _require(len(token) > 4096, "Synthetic backstop input is not oversized")
    result = ingress.classify_sdk_ingress(
        _request(token=None if renewal else token),
        selected_token=token,
        selected_location="body" if renewal else "bearer",
        renewal=renewal,
    )
    _require(result == "deny", "Signed oversized reserved credential reached legacy")


@pytest.mark.parametrize("kind", ["access", "refresh", "renewable_engine"])
def test_large_unreserved_signed_ordinary_and_expired_engine_remain_legacy(kind):
    renewal = kind != "access"
    token = _ordinary(
        kind="refresh" if kind == "refresh" else "access",
        large=True,
        expired_engine=kind == "renewable_engine",
    )
    result = ingress.classify_sdk_ingress(
        _request(token=None if renewal else token),
        selected_token=token,
        selected_location="body" if renewal else "bearer",
        renewal=renewal,
    )
    _require(result == "legacy", "Large unreserved ordinary credential was capped")


def test_expired_renewable_engine_reserved_purpose_denies():
    token = _ordinary(reserved="purpose", large=True, expired_engine=True)
    _require(
        ingress.classify_sdk_ingress(
            _request(), selected_token=token, selected_location="body", renewal=True
        )
        == "deny",
        "Expired Engine reserved marker bypassed backstop",
    )


def _deep_unreserved_token():
    """Signed synthetic nesting without issuer/audience/type/profile authority."""
    payload = ('{"synthetic":' + "[" * 900 + "0" + "]" * 900 + "}").encode()
    settings = get_settings()
    token = jwt.api_jws.PyJWS().encode(
        payload, settings.secret_key, algorithm=settings.algorithm
    )
    _require(len(token) < 4096, "Synthetic deep hint exceeds inspector bound")
    return token


def test_deep_unreserved_token_preserves_ordinary_invalid_result():
    token = _deep_unreserved_token()
    # Locked PyJWT 2.15.1 translates payload RecursionError into DecodeError,
    # which the unchanged ordinary decoder rejects. No parser depth or global
    # recursion-limit assumption establishes authority for this fixture.
    _require(
        decode_token(token, expected_type="access") is None,
        "Deep invalid token gained ordinary authority",
    )
    _require(
        ingress.classify_sdk_ingress(
            _request(token=token), selected_token=token, selected_location="bearer"
        )
        == "legacy",
        "Deep unreserved token changed ordinary routing",
    )


@pytest.mark.parametrize(
    "location",
    ["cookie", "bearer_cookie", "duplicate_header", "refresh_header", "refresh_cookie"],
)
def test_selected_dedicated_mixed_locations_deny(location):
    token = _hint(
        '{"aud":"bifrost-workflow-runtime-sdk","purpose":"workflow-runtime-sdk/v1"}'
    )
    renewal = location.startswith("refresh")
    cookies = (
        {"access_token": "synthetic-ignored"}
        if location in {"bearer_cookie", "refresh_cookie"}
        else None
    )
    headers = (
        [(b"authorization", b"Bearer synthetic-ignored")]
        if location in {"duplicate_header", "refresh_header"}
        else ()
    )
    request = _request(
        token=token if not renewal and location != "cookie" else None,
        cookies=cookies,
        headers=headers,
    )
    result = ingress.classify_sdk_ingress(
        request,
        selected_token=token,
        selected_location="body"
        if renewal
        else "cookie"
        if location == "cookie"
        else "bearer",
        renewal=renewal,
    )
    _require(result == "deny", "Dedicated mixed/cookie credential was admitted")


def test_unselected_reserved_cookie_and_refresh_header_do_not_override_ordinary():
    token = _ordinary()
    dedicated = _hint('{"aud":"bifrost-workflow-runtime-sdk"}')
    request = _request(token=token, cookies={"access_token": dedicated})
    _require(
        ingress.classify_sdk_ingress(
            request, selected_token=token, selected_location="bearer"
        )
        == "legacy",
        "Ignored cookie changed ordinary precedence",
    )
    token = _ordinary(kind="refresh")
    request = _request(token=dedicated, cookies={"refresh_token": dedicated})
    _require(
        ingress.classify_sdk_ingress(
            request, selected_token=token, selected_location="body", renewal=True
        )
        == "legacy",
        "Ignored renewal locations changed precedence",
    )


@pytest.mark.parametrize("operation", ["integration-get", "mapping-get"])
@pytest.mark.parametrize("scope", ["omitted", "null", "same"])
async def test_default_selector_accepts_exact_effective_target(
    make_work, operation, scope
):
    work = await make_work()
    policy = SelectedSDKPolicy(
        operations=tuple(
            op.model_copy(
                update={"scope_kind": "default", "scope_organization_id": None}
            )
            for op in work.policy.operations
        )
    )
    reference = await work.provision(policy=policy)
    bundle = await grants.issue_workflow_runtime_sdk_token(
        work.cohort.factory, reference=reference
    )
    authority = await grants.load_runtime_sdk_ingress_authority(
        work.cohort.factory, token=bundle.access_token
    )
    values = {"name": "Synthetic integration"}
    if scope != "omitted":
        values["scope"] = str(work.cohort.organization_id) if scope == "same" else None
    if operation == "integration-get":
        values["solution"] = str(work.cohort.source.solution_install_id)
    model = (
        SDKIntegrationsGetRequest
        if operation == "integration-get"
        else SDKIntegrationsGetMappingRequest
    )
    ingress.match_sdk_operation(authority, model(**values), operation=operation)


@pytest.mark.parametrize(
    "change",
    ["name", "global", "empty", "foreign", "oauth_scope", "solution", "mapping_entity"],
)
async def test_closed_operation_mismatch_denies(make_work, change):
    work = await make_work()
    _, bundle = await _bundle(work)
    authority = await grants.load_runtime_sdk_ingress_authority(
        work.cohort.factory, token=bundle.access_token
    )
    operation = "mapping-get" if change == "mapping_entity" else "integration-get"
    body = {"name": "Synthetic integration", "scope": str(work.cohort.organization_id)}
    if operation == "integration-get":
        body["solution"] = str(work.cohort.source.solution_install_id)
    body.update(
        {
            "name": {"name": "Synthetic integration changed"},
            "global": {"scope": "global"},
            "empty": {"scope": ""},
            "foreign": {"scope": str(uuid4())},
            "oauth_scope": {"oauth_scope": "synthetic.read"},
            "solution": {"solution": str(uuid4())},
            "mapping_entity": {"entity_id": "override"},
        }[change]
    )
    model = (
        SDKIntegrationsGetRequest
        if operation == "integration-get"
        else SDKIntegrationsGetMappingRequest
    )
    with pytest.raises((RuntimeSDKDenied, ValueError)):
        ingress.match_sdk_operation(authority, model(**body), operation=operation)


@pytest.mark.parametrize("spelling", ["uppercase", "braces", "no_hyphens"])
@pytest.mark.parametrize("selector", ["scope", "solution"])
async def test_dedicated_uuid_selectors_require_canonical_wire_form(
    make_work, spelling, selector
):
    work = await make_work()
    _, bundle = await _bundle(work)
    authority = await grants.load_runtime_sdk_ingress_authority(
        work.cohort.factory, token=bundle.access_token
    )
    body = {
        "name": "Synthetic integration",
        "scope": str(work.cohort.organization_id),
        "solution": str(work.cohort.source.solution_install_id),
    }
    # Deterministic local matcher target guarantees uppercase changes the wire
    # spelling. This modified policy is a pure matcher seam, not DB admission.
    synthetic_target = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    changed_operations = []
    for operation in authority.policy.operations:
        changes = (
            {
                "scope_organization_id": synthetic_target,
                "resolved_organization_id": synthetic_target,
            }
            if selector == "scope"
            else {"solution_install_id": synthetic_target}
            if operation.operation == "integration-get"
            else {}
        )
        changed_operations.append(operation.model_copy(update=changes))
    authority = authority.model_copy(
        update={"policy": SelectedSDKPolicy(operations=tuple(changed_operations))}
    )
    body[selector] = str(synthetic_target)
    value = body[selector]
    body[selector] = (
        value.upper()
        if spelling == "uppercase"
        else "{" + value + "}"
        if spelling == "braces"
        else value.replace("-", "")
    )
    with pytest.raises((RuntimeSDKDenied, ValueError)):
        ingress.match_sdk_operation(
            authority, SDKIntegrationsGetRequest(**body), operation="integration-get"
        )


@pytest.mark.parametrize("actor", [1, 2])
async def test_original_provider_admin_external_normalization_and_effective_org_fence(
    make_work, actor
):
    work = await make_work(timeout=0, actor=actor)
    async with work.cohort.factory() as db, db.begin():
        user = await db.get(User, work.caller.caller_user_id)
        original_external = user.is_external
        user.is_external = True
    try:
        caller = work.caller.model_copy(update={"caller_external": False})
        reference = await work.provision(caller=caller)
        bundle = await grants.issue_workflow_runtime_sdk_token(
            work.cohort.factory, reference=reference
        )
        authority = await grants.load_runtime_sdk_ingress_authority(
            work.cohort.factory, token=bundle.access_token
        )
        assert authority.snapshot.caller_provider == (1 if actor == 1 else 0)
        assert authority.snapshot.caller_external == 0
        if actor == 1:
            assert caller.caller_organization_id != caller.effective_organization_id
        async with work.cohort.factory() as db, db.begin():
            await db.execute(
                update(Organization)
                .where(Organization.id == caller.effective_organization_id)
                .values(is_active=False)
            )
        try:
            with pytest.raises(RuntimeSDKDenied):
                await grants.load_runtime_sdk_ingress_authority(
                    work.cohort.factory, token=bundle.access_token
                )
        finally:
            async with work.cohort.factory() as db, db.begin():
                await db.execute(
                    update(Organization)
                    .where(Organization.id == caller.effective_organization_id)
                    .values(is_active=True)
                )
    finally:
        async with work.cohort.factory() as db, db.begin():
            await db.execute(
                update(User)
                .where(User.id == work.caller.caller_user_id)
                .values(is_external=original_external)
            )


async def test_explicit_orgless_admin_normalizes_external_without_invented_membership(
    make_work,
):
    work = await make_work(timeout=0, actor=2)
    async with work.cohort.factory() as db, db.begin():
        user = await db.get(User, work.caller.caller_user_id)
        original_org, original_external = user.organization_id, user.is_external
        user.organization_id, user.is_external = None, True
    try:
        caller = work.caller.model_copy(
            update={
                "caller_organization_id": None,
                "caller_external": False,
                "caller_provider": False,
            }
        )
        reference = await work.provision(caller=caller)
        bundle = await grants.issue_workflow_runtime_sdk_token(
            work.cohort.factory, reference=reference
        )
        authority = await grants.load_runtime_sdk_ingress_authority(
            work.cohort.factory, token=bundle.access_token
        )
        assert authority.snapshot.caller_organization_id is None
        assert (
            authority.snapshot.caller_admin,
            authority.snapshot.caller_provider,
            authority.snapshot.caller_external,
        ) == (1, 0, 0)
    finally:
        async with work.cohort.factory() as db, db.begin():
            await db.execute(
                update(User)
                .where(User.id == work.caller.caller_user_id)
                .values(organization_id=original_org, is_external=original_external)
            )


async def test_current_unverified_user_and_changed_human_provenance_do_not_add_gates(
    make_work,
):
    work = await make_work(timeout=0)
    _, bundle = await _bundle(work)
    async with work.cohort.factory() as db, db.begin():
        user = await db.get(User, work.caller.caller_user_id)
        assert user.is_verified is False
        original_email, original_name = user.email, user.name
        user.email, user.name = (
            f"synthetic-current-{uuid4().hex}@example.invalid",
            "Changed human name",
        )
    try:
        for helper in (
            grants.load_runtime_sdk_ingress_authority,
            grants.admit_runtime_sdk_renewal,
        ):
            authority = await helper(work.cohort.factory, token=bundle.access_token)
            assert authority.snapshot.caller_email == original_email
            assert authority.snapshot.caller_name == original_name
    finally:
        async with work.cohort.factory() as db, db.begin():
            await db.execute(
                update(User)
                .where(User.id == work.caller.caller_user_id)
                .values(email=original_email, name=original_name)
            )


@pytest.mark.parametrize("actor", [1, 2])
async def test_real_original_provider_or_admin_privilege_loss_denies(make_work, actor):
    work = await make_work(timeout=0, actor=actor)
    _, bundle = await _bundle(work)
    model, row_id, field = (
        (Organization, work.caller.caller_organization_id, "is_provider")
        if actor == 1
        else (User, work.caller.caller_user_id, "is_superuser")
    )
    async with work.cohort.factory() as db, db.begin():
        await db.execute(
            update(model).where(model.id == row_id).values(**{field: False})
        )
    try:
        for helper in (
            grants.load_runtime_sdk_ingress_authority,
            grants.admit_runtime_sdk_renewal,
        ):
            with pytest.raises(RuntimeSDKDenied):
                await helper(work.cohort.factory, token=bundle.access_token)
    finally:
        async with work.cohort.factory() as db, db.begin():
            await db.execute(
                update(model).where(model.id == row_id).values(**{field: True})
            )


@pytest.mark.parametrize(
    "change",
    [
        "inactive_user",
        "inactive_original_org",
        "admin_gain",
        "provider_gain",
        "external_gain",
        "caller_org",
        "claim",
        "worker",
        "terminal",
        "revoked",
    ],
)
async def test_admission_denies_real_committed_current_caller_and_fence_drift(
    make_work, change
):
    work = await make_work(timeout=0)
    reference, bundle = await _bundle(work)
    await grants.load_runtime_sdk_ingress_authority(
        work.cohort.factory, token=bundle.access_token
    )
    await grants.admit_runtime_sdk_renewal(
        work.cohort.factory, token=bundle.access_token
    )
    model, row_id, values = {
        "inactive_user": (User, work.caller.caller_user_id, {"is_active": False}),
        "inactive_original_org": (
            Organization,
            work.caller.caller_organization_id,
            {"is_active": False},
        ),
        "admin_gain": (User, work.caller.caller_user_id, {"is_superuser": True}),
        "provider_gain": (
            Organization,
            work.caller.caller_organization_id,
            {"is_provider": True},
        ),
        "external_gain": (User, work.caller.caller_user_id, {"is_external": True}),
        "caller_org": (
            User,
            work.caller.caller_user_id,
            {"organization_id": work.cohort.provider_organization_id},
        ),
        "claim": (
            WorkflowExecutionAttempt,
            work.start.workflow_attempt_id,
            {"claim_token": uuid4()},
        ),
        "worker": (
            WorkflowExecutionAttempt,
            work.start.workflow_attempt_id,
            {"worker_incarnation_id": uuid4()},
        ),
        "terminal": (
            WorkflowExecutionAttempt,
            work.start.workflow_attempt_id,
            {
                "status": "succeeded",
                "phase": "terminal",
                "completed_at": datetime.now(UTC),
            },
        ),
        "revoked": (
            grants.WorkflowRuntimeSDKGrant,
            reference.grant_id,
            {"revoked_at": datetime.now(UTC), "revocation_reason": "session_closed"},
        ),
    }[change]
    async with work.cohort.factory() as db, db.begin():
        row = await db.get(model, row_id)
        original = {key: getattr(row, key) for key in values}
        for key, value in values.items():
            setattr(row, key, value)
    try:
        for helper in (
            grants.load_runtime_sdk_ingress_authority,
            grants.admit_runtime_sdk_renewal,
        ):
            with pytest.raises(RuntimeSDKDenied):
                await helper(work.cohort.factory, token=bundle.access_token)
    finally:
        async with work.cohort.factory() as db, db.begin():
            await db.execute(update(model).where(model.id == row_id).values(**original))


@pytest.mark.parametrize(
    "age,grace,eligible",
    [
        (0, "120", True),
        (119, "120", True),
        (120, "120", False),
        (30, "30", False),
        (29, "30", True),
        (120, "999", False),
        (119, "invalid", True),
        (1, "0", False),
        (-1, "120", False),
    ],
)
async def test_real_committed_lease_bounds(
    make_work, monkeypatch, age, grace, eligible
):
    work = await make_work(timeout=0)
    _, bundle = await _bundle(work)
    now = datetime.now(UTC)
    monkeypatch.setenv("BIFROST_WORKFLOW_RESTART_ORPHAN_GRACE_SECONDS", grace)
    original_now = grants._now
    monkeypatch.setattr(
        grants, "_now", lambda value: now if value is None else original_now(value)
    )
    anchor = now - timedelta(seconds=age)
    async with work.cohort.factory() as db, db.begin():
        await db.execute(
            update(WorkflowExecutionAttempt)
            .where(WorkflowExecutionAttempt.id == work.start.workflow_attempt_id)
            .values(heartbeat_at=anchor, claimed_at=anchor)
        )
    for helper in (
        grants.load_runtime_sdk_ingress_authority,
        grants.admit_runtime_sdk_renewal,
    ):
        if eligible:
            authority = await helper(work.cohort.factory, token=bundle.access_token)
            assert (
                authority.snapshot.workflow_attempt_id == work.start.workflow_attempt_id
            )
        else:
            with pytest.raises(RuntimeSDKDenied):
                await helper(work.cohort.factory, token=bundle.access_token)


async def test_active_attempt_missing_both_anchors_is_rejected_by_real_storage(
    make_work,
):
    """Missing both anchors cannot be committed in a valid active attempt.

    This proves the migrated storage guard and preservation of admitted state;
    it does not claim runtime coverage of the defensive missing-anchor branch.
    """
    work = await make_work(timeout=0)
    reference, bundle = await _bundle(work)
    fields = (
        "status",
        "phase",
        "completed_at",
        "published_at",
        "claimed_at",
        "started_at",
        "heartbeat_at",
    )
    async with work.cohort.factory() as db:
        attempt = await db.get(WorkflowExecutionAttempt, work.start.workflow_attempt_id)
        _require(attempt is not None, "Owned committed attempt missing")
        original = tuple(getattr(attempt, name) for name in fields)

    async with work.cohort.factory() as db:
        try:
            with pytest.raises(IntegrityError) as error:
                await db.execute(
                    update(WorkflowExecutionAttempt)
                    .where(
                        WorkflowExecutionAttempt.id == work.start.workflow_attempt_id
                    )
                    .values(heartbeat_at=None, claimed_at=None)
                )
            diagnostic = error.value.orig.__cause__
            _require(
                isinstance(diagnostic, CheckViolationError),
                "Storage denial lacked original asyncpg check diagnostic",
            )
            _require(
                diagnostic.constraint_name
                == "ck_workflow_execution_attempt_state_shape",
                "Storage denial came from a different constraint",
            )
        finally:
            await db.rollback()

    # Both the failed write transaction and its session have ended before the
    # independent readback and the two actual admission helpers.
    async with work.cohort.factory() as db:
        attempt = await db.get(WorkflowExecutionAttempt, work.start.workflow_attempt_id)
        _require(attempt is not None, "Owned attempt disappeared after rollback")
        _require(
            tuple(getattr(attempt, name) for name in fields) == original,
            "Rejected missing-anchor write changed committed attempt state",
        )
    for helper in (
        grants.load_runtime_sdk_ingress_authority,
        grants.admit_runtime_sdk_renewal,
    ):
        authority = await helper(work.cohort.factory, token=bundle.access_token)
        assert authority.snapshot.id == reference.grant_id


@pytest.mark.parametrize(
    "age,grace,eligible",
    [
        (0, "120", True),
        (119, "120", True),
        (120, "120", False),
        (29, "30", True),
        (30, "30", False),
        (120, "999", False),
        (119, "invalid", True),
        (1, "0", False),
        (-1, "120", False),
    ],
)
async def test_real_missing_heartbeat_uses_valid_claimed_anchor_bounds(
    make_work, monkeypatch, age, grace, eligible
):
    work = await make_work(timeout=0)
    reference, bundle = await _bundle(work)
    now = datetime.now(UTC)
    monkeypatch.setenv("BIFROST_WORKFLOW_RESTART_ORPHAN_GRACE_SECONDS", grace)
    original_now = grants._now
    monkeypatch.setattr(
        grants, "_now", lambda value: now if value is None else original_now(value)
    )
    claimed_at = now - timedelta(seconds=age)
    async with work.cohort.factory() as db, db.begin():
        await db.execute(
            update(WorkflowExecutionAttempt)
            .where(WorkflowExecutionAttempt.id == work.start.workflow_attempt_id)
            .values(heartbeat_at=None, claimed_at=claimed_at)
        )
    for helper in (
        grants.load_runtime_sdk_ingress_authority,
        grants.admit_runtime_sdk_renewal,
    ):
        if eligible:
            authority = await helper(work.cohort.factory, token=bundle.access_token)
            assert authority.snapshot.id == reference.grant_id
        else:
            with pytest.raises(RuntimeSDKDenied):
                await helper(work.cohort.factory, token=bundle.access_token)


async def test_role_names_are_db_only_bounded_and_privilege_changes_deny(make_work):
    work = await make_work()
    role_ids = [uuid4() for _ in range(257)]
    names = [f"synthetic-role-{i:03}" for i in range(257)]
    # Row 257 duplicates an admitted name: deduplication must not hide an
    # over-limit DB result before the independent cardinality check.
    names[-1] = names[0]
    try:
        async with work.cohort.factory() as db, db.begin():
            db.add_all(
                [
                    Role(id=role_id, name=name, created_by="synthetic-hr")
                    for role_id, name in zip(role_ids, names, strict=True)
                ]
            )
            await db.flush()
            db.add_all(
                [
                    UserRole(
                        user_id=work.caller.caller_user_id,
                        role_id=role_id,
                        assigned_by="synthetic-hr",
                    )
                    for role_id in role_ids[:256]
                ]
            )
        caller = work.caller.model_copy(update={"roles": tuple(names[:256])})
        reference = await work.provision(caller=caller)
        bundle = await grants.issue_workflow_runtime_sdk_token(
            work.cohort.factory, reference=reference
        )
        authority = await grants.load_runtime_sdk_ingress_authority(
            work.cohort.factory, token=bundle.access_token
        )
        assert authority.snapshot.caller_snapshot_digest == caller_digest(caller)
        async with work.cohort.factory() as db, db.begin():
            db.add(
                UserRole(
                    user_id=work.caller.caller_user_id,
                    role_id=role_ids[-1],
                    assigned_by="synthetic-hr",
                )
            )
        with pytest.raises(RuntimeSDKDenied):
            await grants.load_runtime_sdk_ingress_authority(
                work.cohort.factory, token=bundle.access_token
            )
        async with work.cohort.factory() as db, db.begin():
            await db.execute(
                delete(UserRole).where(
                    UserRole.user_id == work.caller.caller_user_id,
                    UserRole.role_id.in_(role_ids[-2:]),
                )
            )
        with pytest.raises(RuntimeSDKDenied):
            await grants.load_runtime_sdk_ingress_authority(
                work.cohort.factory, token=bundle.access_token
            )
    finally:
        async with work.cohort.factory() as db, db.begin():
            await db.execute(delete(UserRole).where(UserRole.role_id.in_(role_ids)))
            await db.execute(delete(Role).where(Role.id.in_(role_ids)))


async def test_serializable_readonly_before_first_fact_and_pool_reset(make_work):
    work = await make_work()
    _, bundle = await _bundle(work)
    engine = create_async_engine(
        get_settings().database_url,
        poolclass=AsyncAdaptedQueuePool,
        pool_size=1,
        max_overflow=0,
    )
    observed = []
    armed = True

    def observe(connection, _cursor, statement, _parameters, _context, _executemany):
        nonlocal armed
        if armed and statement.lstrip().lower().startswith("select"):
            armed = False
            observed.append(
                tuple(
                    connection.exec_driver_sql(f"SHOW {setting}").scalar_one()
                    for setting in (
                        "transaction_isolation",
                        "transaction_read_only",
                        "transaction_deferrable",
                    )
                )
            )
            _require(
                not observed[-1][2] == "on", "Admission transaction was deferrable"
            )

    event.listen(engine.sync_engine, "before_cursor_execute", observe)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        authority = await grants.load_runtime_sdk_ingress_authority(
            factory, token=bundle.access_token
        )
        assert authority.snapshot.workflow_attempt_id == work.start.workflow_attempt_id
        assert observed == [("serializable", "on", "off")]
        async with factory() as db:
            reset = tuple(
                [
                    await db.scalar(text(f"SHOW {setting}"))
                    for setting in (
                        "transaction_isolation",
                        "transaction_read_only",
                        "transaction_deferrable",
                    )
                ]
            )
            assert reset == ("read committed", "off", "off")
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", observe)
        await engine.dispose()


async def test_snapshot_clock_is_after_committed_heartbeat_and_current_facts(
    make_work, monkeypatch, async_engine
):
    work = await make_work()
    _, bundle = await _bundle(work)
    committed = datetime.now(UTC)
    async with work.cohort.factory() as db, db.begin():
        await db.execute(
            update(WorkflowExecutionAttempt)
            .where(WorkflowExecutionAttempt.id == work.start.workflow_attempt_id)
            .values(heartbeat_at=committed)
        )
    reads = []

    def observe(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().lower().startswith("select"):
            reads.append(statement.lower())

    original_now = grants._now

    def clock(value):
        if value is None:
            _require(
                any("users" in read for read in reads),
                "Clock sampled before current user fact",
            )
            _require(
                any("roles" in read for read in reads),
                "Clock sampled before current role facts",
            )
            return datetime.now(UTC)
        return original_now(value)

    monkeypatch.setattr(grants, "_now", clock)
    event.listen(async_engine.sync_engine, "before_cursor_execute", observe)
    try:
        await grants.load_runtime_sdk_ingress_authority(
            work.cohort.factory, token=bundle.access_token
        )
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", observe)


async def test_snapshot_retains_pre_mutation_facts_and_next_admission_denies(make_work):
    work = await make_work()
    _, bundle = await _bundle(work)
    snapshot_ready, mutation_committed = asyncio.Event(), asyncio.Event()

    class PausingSession(AsyncSession):
        async def scalar(self, *args, **kwargs):
            value = await super().scalar(*args, **kwargs)
            if not snapshot_ready.is_set():
                snapshot_ready.set()
                await mutation_committed.wait()
            return value

    factory = async_sessionmaker(
        work.cohort.factory.kw["bind"], class_=PausingSession, expire_on_commit=False
    )
    admission = asyncio.create_task(
        grants.load_runtime_sdk_ingress_authority(factory, token=bundle.access_token)
    )
    try:
        await asyncio.wait_for(snapshot_ready.wait(), 5)
        async with work.cohort.factory() as db, db.begin():
            await db.execute(
                update(User)
                .where(User.id == work.caller.caller_user_id)
                .values(is_active=False)
            )
            await db.execute(
                update(WorkflowExecutionAttempt)
                .where(WorkflowExecutionAttempt.id == work.start.workflow_attempt_id)
                .values(heartbeat_at=datetime.now(UTC))
            )
        mutation_committed.set()
        authority = await asyncio.wait_for(admission, 5)
        assert authority.snapshot.caller_user_id == work.caller.caller_user_id
        with pytest.raises(RuntimeSDKDenied):
            await grants.load_runtime_sdk_ingress_authority(
                work.cohort.factory, token=bundle.access_token
            )
    finally:
        mutation_committed.set()
        if not admission.done():
            admission.cancel()
        await asyncio.gather(admission, return_exceptions=True)
        async with work.cohort.factory() as db, db.begin():
            await db.execute(
                update(User)
                .where(User.id == work.caller.caller_user_id)
                .values(is_active=True)
            )


@pytest.mark.parametrize("failure", ["configure", "read", "rollback", "close"])
async def test_uncertain_transaction_cleanup_denies(make_work, failure):
    work = await make_work()
    _, bundle = await _bundle(work)
    calls = []

    class FailingSession(AsyncSession):
        async def connection(self, *args, **kwargs):
            calls.append("configure")
            if failure == "configure":
                raise SQLAlchemyError("synthetic configuration failure")
            return await super().connection(*args, **kwargs)

        async def scalar(self, *args, **kwargs):
            if failure == "read":
                raise SQLAlchemyError("synthetic read failure")
            return await super().scalar(*args, **kwargs)

        async def rollback(self):
            calls.append("rollback")
            await super().rollback()
            if failure == "rollback":
                raise SQLAlchemyError("synthetic rollback failure")

        async def close(self):
            calls.append("close")
            await super().close()
            if failure == "close":
                raise SQLAlchemyError("synthetic close failure")

    factory = async_sessionmaker(
        work.cohort.factory.kw["bind"], class_=FailingSession, expire_on_commit=False
    )
    with pytest.raises(RuntimeSDKDenied):
        await grants.load_runtime_sdk_ingress_authority(
            factory, token=bundle.access_token
        )
    assert calls.count("configure") == 1
    assert "close" in calls
    await grants.load_runtime_sdk_ingress_authority(
        work.cohort.factory, token=bundle.access_token
    )


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_local_yield_context_restores_all_public_contexts(
    make_work, monkeypatch, outcome
):
    work = await make_work()
    _, bundle = await _bundle(work)
    # A real loader produces the authority once; replacement below isolates
    # yield lifetime only. Public authorization proof is in the HTTP module.
    authority = await grants.load_runtime_sdk_ingress_authority(
        work.cohort.factory, token=bundle.access_token
    )

    async def local_loader(_factory, *, token):
        return authority

    monkeypatch.setattr(ingress, "load_runtime_sdk_ingress_authority", local_loader)
    original_user = RequestUser("synthetic-prior", "Prior")
    original_actor = ActorContext(
        user_id=uuid4(), organization_id=None, source="synthetic"
    )
    set_request_user(original_user)
    set_request_session_id("synthetic-prior-session")
    actor_token = set_actor(original_actor)
    request = _request(
        token=bundle.access_token,
        body={
            "name": "Synthetic integration",
            "scope": str(work.cohort.organization_id),
            "solution": str(work.cohort.source.solution_install_id),
        },
    )
    dependency = ingress.sdk_get_principal(
        request,
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=bundle.access_token),
        None,
    )
    try:
        principal = await anext(dependency)
        assert (
            principal.is_superuser,
            principal.is_engine_token,
            principal.is_external,
        ) == (True, True, False)
        assert principal.delegated_user_id == work.caller.caller_user_id
        _require(
            principal.engine_attempt_token == work.start.private_claim_token,
            "Engine private fence changed",
        )
        assert principal.organization_id == work.caller.effective_organization_id
        assert get_request_user().user_id == ENGINE_USER_ID
        assert get_request_session_id() is None
        assert current_actor().user_id == UUID(ENGINE_USER_ID)
        if outcome == "success":
            await dependency.aclose()
        else:
            error = (
                asyncio.CancelledError()
                if outcome == "cancel"
                else RuntimeError("synthetic handler failure")
            )
            with pytest.raises(type(error)):
                await dependency.athrow(error)
        assert get_request_user() == original_user
        assert get_request_session_id() == "synthetic-prior-session"
        assert current_actor() == original_actor
    finally:
        await dependency.aclose()
        clear_actor(actor_token)
        set_request_user(None)
        set_request_session_id(None)
