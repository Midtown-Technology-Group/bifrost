"""Closed runtime-SDK HTTP mechanisms; production owner/channel proof is separate."""

import base64
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import bearer_scheme, get_current_user
from src.core.database import get_session_factory
from src.core.db_deps import DbSession
from src.core.principal import UserPrincipal
from src.core.rate_limit import auth_limiter, get_client_ip
from src.core.request_context import (
    RequestUser,
    get_request_session_id,
    get_request_user,
    set_request_session_id,
    set_request_user,
)
from src.core.runtime_sdk_credentials import (
    RENEWAL_SECONDS,
    RUNTIME_SDK_AUDIENCE,
    RUNTIME_SDK_PURPOSE,
    CredentialBundle,
    GrantReference,
    RuntimeSDKDenied,
    grant_digest,
    sign_runtime_sdk_token,
)
from src.core.security import (
    ENGINE_USER_ID,
    decode_renewable_engine_token,
    decode_token,
)
from src.models.contracts.cli import (
    SDKIntegrationsGetMappingRequest,
    SDKIntegrationsGetRequest,
)
from src.services.audit_context import ActorContext, clear_actor, set_actor
from src.services.runtime_sdk_grants import (
    ValidatedRuntimeSDKAuthority,
    admit_runtime_sdk_renewal,
    load_runtime_sdk_ingress_authority,
)


@dataclass(frozen=True)
class _HintObject:
    pairs: tuple[tuple[str, object], ...]


def _reserved_pair(name: str, value: object) -> bool:
    return (name == "purpose" and value == RUNTIME_SDK_PURPOSE) or (
        name == "aud"
        and (
            value == RUNTIME_SDK_AUDIENCE
            or (isinstance(value, list) and RUNTIME_SDK_AUDIENCE in value)
        )
    )


def _bounded_hint(token: str) -> tuple[bool, bool]:
    """Unsigned bytes choose strict verification/denial only, never authority."""
    if len(token) > 4096 or len(token.split(".")) != 3:
        return False, False
    segments = token.split(".")
    invalid = False

    def pairs(values):
        nonlocal invalid
        if len({key for key, _ in values}) != len(values):
            invalid = True
        return _HintObject(tuple(values))

    def constant(_value):
        nonlocal invalid
        invalid = True
        return None

    def parsed(segment):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", segment):
            raise ValueError("invalid hint segment")
        data = base64.b64decode(
            segment + "=" * (-len(segment) % 4), altchars=b"-_", validate=True
        )
        if len(data) > 4096:
            raise ValueError("hint exceeds bound")
        return json.loads(
            data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant
        )

    try:
        payload = parsed(segments[1])
    except (ValueError, UnicodeError, RecursionError):
        return False, False
    if not isinstance(payload, _HintObject):
        return False, False
    reserved = any(_reserved_pair(key, value) for key, value in payload.pairs)
    if not reserved:
        return False, False
    try:
        header = parsed(segments[0])
        if not isinstance(header, _HintObject):
            invalid = True
    except (ValueError, UnicodeError, RecursionError):
        invalid = True
    values = dict(payload.pairs)
    invalid = invalid or values.get("aud") != RUNTIME_SDK_AUDIENCE
    invalid = invalid or values.get("purpose") != RUNTIME_SDK_PURPOSE
    return True, invalid


def _verified_reserved(token: str, *, renewal: bool) -> bool:
    ordinary = decode_token(token, expected_type="refresh" if renewal else "access")
    if ordinary is None and renewal:
        ordinary = decode_renewable_engine_token(token)
    return ordinary is not None and any(
        _reserved_pair(key, value) for key, value in ordinary.items()
    )


def classify_sdk_ingress(
    request: Request,
    *,
    selected_token: str | None,
    selected_location: Literal["bearer", "cookie", "body", "none"],
    renewal: bool = False,
) -> Literal["legacy", "dedicated", "deny"]:
    if not selected_token:
        return "legacy"
    reserved, invalid = _bounded_hint(selected_token)
    if not reserved:
        return (
            "deny" if _verified_reserved(selected_token, renewal=renewal) else "legacy"
        )
    if invalid:
        return "deny"
    auth_cookies = {"access_token", "embed_token", "refresh_token"}
    if auth_cookies.intersection(request.cookies):
        return "deny"
    headers = request.headers.getlist("authorization")
    if renewal:
        return "dedicated" if selected_location == "body" and not headers else "deny"
    return (
        "dedicated" if selected_location == "bearer" and len(headers) == 1 else "deny"
    )


def _denied() -> HTTPException:
    return HTTPException(
        401, "Invalid runtime SDK credential", headers={"WWW-Authenticate": "Bearer"}
    )


def _optional_uuid(value: str | None) -> UUID | None:
    if value is None:
        return None
    result = None
    try:
        result = UUID(value)
    except ValueError:
        # Keep result unset so malformed selectors reach the denial below.
        pass
    if result is None or str(result) != value:
        raise RuntimeSDKDenied("runtime SDK UUID selector is invalid")
    return result


def match_sdk_operation(
    authority: ValidatedRuntimeSDKAuthority,
    body: SDKIntegrationsGetRequest | SDKIntegrationsGetMappingRequest,
    *,
    operation: Literal["integration-get", "mapping-get"],
) -> None:
    selected = next(
        (item for item in authority.policy.operations if item.operation == operation),
        None,
    )
    if selected is None or selected.integration_name != body.name:
        raise RuntimeSDKDenied("runtime SDK operation is outside grant")
    if operation == "integration-get":
        if (
            not isinstance(body, SDKIntegrationsGetRequest)
            or body.oauth_scope is not None
        ):
            raise RuntimeSDKDenied("runtime SDK GET selector is outside grant")
        install = _optional_uuid(body.solution)
        if install != selected.solution_install_id:
            raise RuntimeSDKDenied("runtime SDK solution is outside grant")
    elif (
        not isinstance(body, SDKIntegrationsGetMappingRequest)
        or body.entity_id is not None
        or selected.solution_install_id is not None
    ):
        raise RuntimeSDKDenied("runtime SDK mapping selector is outside grant")
    if selected.scope_kind == "default":
        if (
            selected.resolved_organization_id
            != authority.snapshot.effective_organization_id
        ):
            raise RuntimeSDKDenied("runtime SDK default organization differs")
        if (
            body.scope is not None
            and _optional_uuid(body.scope) != selected.resolved_organization_id
        ):
            raise RuntimeSDKDenied("runtime SDK default selector is outside grant")
    elif selected.scope_kind == "global":
        if body.scope != "global" or selected.resolved_organization_id is not None:
            raise RuntimeSDKDenied("runtime SDK global selector is outside grant")
    elif (
        body.scope is None
        or _optional_uuid(body.scope) != selected.scope_organization_id
    ):
        raise RuntimeSDKDenied("runtime SDK organization selector is outside grant")


def _engine_principal(authority: ValidatedRuntimeSDKAuthority) -> UserPrincipal:
    snapshot = authority.snapshot
    return UserPrincipal(
        user_id=UUID(ENGINE_USER_ID),
        email="engine@bifrost.internal",
        name="Bifrost Engine",
        organization_id=snapshot.effective_organization_id,
        is_active=True,
        is_verified=True,
        is_superuser=True,
        is_engine_token=True,
        is_external=False,
        engine_execution_id=snapshot.execution_id,
        engine_attempt_token=authority.private_claim_token,
        engine_solution_id=str(snapshot.solution_install_id),
        delegated_user_id=snapshot.caller_user_id,
        delegated_email=snapshot.caller_email,
        delegated_name=snapshot.caller_name,
        delegated_is_superuser=bool(snapshot.caller_admin),
        delegated_is_provider_org=bool(snapshot.caller_provider),
        delegated_is_external=bool(snapshot.caller_external),
    )


async def _dedicated_body(
    request: Request, *, operation: Literal["integration-get", "mapping-get"]
) -> SDKIntegrationsGetRequest | SDKIntegrationsGetMappingRequest:
    raw = await request.json()
    model = (
        SDKIntegrationsGetRequest
        if operation == "integration-get"
        else SDKIntegrationsGetMappingRequest
    )
    allowed = (
        {"name", "scope", "oauth_scope", "solution"}
        if operation == "integration-get"
        else {"name", "scope", "entity_id"}
    )
    if not isinstance(raw, dict) or not set(raw).issubset(allowed):
        raise _denied()
    errors = None
    body = None
    try:
        body = model.model_validate(raw)
    except ValidationError as error:
        errors = [{**item, "loc": ("body", *item["loc"])} for item in error.errors()]
    if errors is not None:
        raise RequestValidationError(errors, body=raw)
    if body is None:
        raise _denied()
    return body


@asynccontextmanager
async def _principal_scope(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    db: AsyncSession,
    *,
    operation: Literal["integration-get", "mapping-get"],
) -> AsyncIterator[UserPrincipal]:
    location: Literal["bearer", "cookie", "none"]
    token: str | None
    if credentials:
        token, location = credentials.credentials, "bearer"
    elif "access_token" in request.cookies:
        token, location = request.cookies["access_token"], "cookie"
    elif "embed_token" in request.cookies:
        token, location = request.cookies["embed_token"], "cookie"
    else:
        token, location = None, "none"
    classification = classify_sdk_ingress(
        request, selected_token=token, selected_location=location
    )
    if classification == "legacy":
        yield await get_current_user(request, credentials, db)
        return
    if classification == "deny" or token is None:
        raise _denied()
    try:
        authority = await load_runtime_sdk_ingress_authority(
            get_session_factory(), token=token
        )
    except RuntimeSDKDenied:
        authority = None
    if authority is None:
        raise _denied()
    body = await _dedicated_body(request, operation=operation)
    denied = False
    try:
        match_sdk_operation(authority, body, operation=operation)
    except (RuntimeSDKDenied, ValueError):
        denied = True
    if denied:
        raise _denied()
    principal = _engine_principal(authority)
    previous_user, previous_session = get_request_user(), get_request_session_id()
    actor_token = set_actor(
        ActorContext(
            user_id=principal.user_id,
            organization_id=principal.organization_id,
            email=principal.email,
            name=principal.name,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            source="http",
        )
    )
    try:
        set_request_user(RequestUser(str(principal.user_id), principal.name))
        set_request_session_id(None)
        yield principal
    finally:
        try:
            set_request_user(previous_user)
        finally:
            try:
                set_request_session_id(previous_session)
            finally:
                clear_actor(actor_token)


async def sdk_get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbSession,
) -> AsyncIterator[UserPrincipal]:
    async with _principal_scope(
        request, credentials, db, operation="integration-get"
    ) as principal:
        yield principal


async def sdk_mapping_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbSession,
) -> AsyncIterator[UserPrincipal]:
    async with _principal_scope(
        request, credentials, db, operation="mapping-get"
    ) as principal:
        yield principal


async def refresh_runtime_sdk_credential(
    request: Request, *, token: str
) -> CredentialBundle:
    authority = None
    try:
        authority = await admit_runtime_sdk_renewal(get_session_factory(), token=token)
    except RuntimeSDKDenied:
        # Policy denial uses the IP limiter and opaque 401 branch below.
        pass
    if authority is None:
        await auth_limiter.check("refresh", get_client_ip(request))
        raise _denied()
    await auth_limiter.check(
        "runtime_sdk_refresh",
        f"{authority.snapshot.id}:{authority.snapshot.workflow_attempt_id}",
    )
    issued_at = datetime.now(UTC)
    return sign_runtime_sdk_token(
        GrantReference(
            grant_id=authority.snapshot.id,
            grant_digest=grant_digest(authority.snapshot),
        ),
        issued_at=issued_at,
        expires_at=issued_at + timedelta(seconds=RENEWAL_SECONDS),
    )


SDKGetPrincipal = Annotated[UserPrincipal, Depends(sdk_get_principal)]
SDKMappingPrincipal = Annotated[UserPrincipal, Depends(sdk_mapping_principal)]
