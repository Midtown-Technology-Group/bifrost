"""Legacy engine reference only: real HTTP/SDK, committed PG and synthetic OAuth.

Expected profiles are source-derived hypotheses. A discrepancy must fail for
review; this module neither changes policy nor proves consumer/source admission.
The actor arm runs the unchanged application lifespan in an isolated process.
No SDK response, token, decrypted canary, or request body is failure evidence.
"""

from __future__ import annotations

import asyncio
import errno
import multiprocessing
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from multiprocessing.connection import Connection
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from bifrost._context import clear_execution_context, set_execution_context
from bifrost._execution_context import ExecutionContext
from bifrost.client import BifrostClient, _clear_client, _set_client
from bifrost.integrations import integrations
from bifrost.models import Organization as SDKOrganization
from src.core.security import (
    ENGINE_USER_ID,
    decode_token,
    decrypt_secret,
    encrypt_secret,
    mint_engine_token,
)
from src.models.enums import ConfigType, ExecutionStatus
from src.models.orm import Config as ConfigModel
from src.models.orm.audit import AuditLog
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from src.models.orm.integrations import Integration, IntegrationMapping
from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from tests.e2e.conftest import E2E_API_URL
from tests.e2e.fixtures.setup import _register_and_authenticate_user
from tests.e2e.fixtures.users import E2EUser

pytestmark = pytest.mark.e2e
CALLER_KINDS = ("ordinary", "external", "engine", "engine_external")
FIXTURE_URL = "http://scheduler-fixtures:8080"
FIXTURE_ACCESS_TOKEN = "scheduler-fixture-access-service"


@dataclass(frozen=True)
class Caller:
    user_id: UUID
    organization_id: UUID
    email: str
    name: str
    external: bool
    token: str = field(repr=False)
    execution_id: UUID | None = None
    engine: bool = False


@dataclass(frozen=True)
class IntegrationFixture:
    name: str
    integration_id: UUID
    provider_id: UUID
    token_id: UUID
    receipt_key: str
    mapping_id: UUID | None
    global_secret: str = field(repr=False)
    org_secret: str = field(repr=False)
    stored_token: str = field(repr=False)


def _require(condition: bool, message: str) -> None:
    # Disable pytest's rich comparison of OAuth/SDK objects on failure.
    if not condition:
        raise AssertionError(message)


@pytest.fixture(scope="module")
def reference_users(e2e_client, platform_admin, org1, org1_user):
    suffix = uuid4().hex
    role = e2e_client.post(
        "/api/roles",
        headers=platform_admin.headers,
        json={"name": f"cred-ref-{suffix}", "description": "Owned SDK reference role"},
    )
    _require(role.status_code == 201, "Reference role creation failed")
    role_id = role.json()["id"]
    external_id = None
    try:
        user = E2EUser(
            email=f"cred-ref-{suffix}@gobifrost.dev",
            password="ReferenceFixture123!",
            name=f"Reference external {suffix}",
            organization_id=UUID(org1["id"]),
        )
        created = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": user.email,
                "name": user.name,
                "organization_id": org1["id"],
                "is_superuser": False,
                "is_external": True,
            },
        )
        _require(created.status_code == 201, "Reference external user creation failed")
        external_id = created.json()["id"]
        _require(
            created.json()["is_external"] is True,
            "External fixture classification missing",
        )
        user.user_id = UUID(external_id)
        assigned = e2e_client.post(
            f"/api/roles/{role_id}/users",
            headers=platform_admin.headers,
            json={"user_ids": [external_id]},
        )
        _require(assigned.status_code == 204, "Reference role assignment failed")
        try:
            user = _register_and_authenticate_user(
                e2e_client, user, skip_registration=False
            )
        except Exception:  # noqa: BLE001 - Shared login assertions may include token responses.
            raise AssertionError("Actual external fixture login failed") from None
        _require(
            bool(user.access_token and org1_user.access_token),
            "Actual caller login missing",
        )
        yield {
            "ordinary": Caller(
                UUID(str(org1_user.user_id)),
                UUID(org1["id"]),
                org1_user.email,
                org1_user.name,
                False,
                org1_user.access_token,
            ),
            "external": Caller(
                UUID(external_id),
                UUID(org1["id"]),
                user.email,
                user.name,
                True,
                user.access_token,
            ),
        }
    finally:
        if external_id is not None:
            deleted = e2e_client.delete(
                f"/api/users/{external_id}", headers=platform_admin.headers
            )
            _require(deleted.status_code == 204, "Owned external user cleanup failed")
        deleted = e2e_client.delete(
            f"/api/roles/{role_id}", headers=platform_admin.headers
        )
        _require(deleted.status_code == 204, "Owned reference role cleanup failed")


@pytest_asyncio.fixture
async def callers(reference_users, async_session_factory):
    owned: list[UUID] = []
    result = dict(reference_users)
    try:
        async with async_session_factory() as db:
            system = await db.get(User, UUID(ENGINE_USER_ID))
            _require(
                system is not None and system.is_external is False,
                "Migrated system principal missing or external",
            )
            org = await db.get(
                Organization, reference_users["ordinary"].organization_id
            )
            _require(
                org is not None and not org.is_provider,
                "Reference org must be non-provider",
            )
            for kind, caller in reference_users.items():
                authoritative = await db.get(User, caller.user_id)
                _require(
                    authoritative is not None
                    and not authoritative.is_superuser
                    and authoritative.is_external is caller.external
                    and authoritative.organization_id == caller.organization_id,
                    "Reference original-caller authority mismatch",
                )
                now, execution_id, claim = datetime.now(UTC), uuid4(), uuid4()
                execution = Execution(
                    id=execution_id,
                    workflow_name=f"cred-reference-{execution_id.hex}",
                    status=ExecutionStatus.RUNNING,
                    started_at=now,
                    executed_by=caller.user_id,
                    executed_by_name=caller.name,
                    organization_id=caller.organization_id,
                )
                db.add(execution)
                await db.flush()
                db.add(
                    WorkflowExecutionAttempt(
                        execution_id=execution_id,
                        attempt_number=1,
                        claim_token=claim,
                        status="running",
                        phase="execution",
                        published_at=now,
                        claimed_at=now,
                        started_at=now,
                        heartbeat_at=now,
                    )
                )
                owned.append(execution_id)
                token, _ = mint_engine_token(
                    execution_id=str(execution_id),
                    attempt_token=str(claim),
                    solution_id=None,
                    organization_id=str(caller.organization_id),
                    delegated_user_id=str(caller.user_id),
                    delegated_email=caller.email,
                    delegated_name=caller.name,
                    delegated_is_superuser=False,
                    delegated_is_provider_org=False,
                    delegated_is_external=caller.external,
                )
                payload = decode_token(token, expected_type="access")
                _require(
                    payload is not None
                    and payload["sub"] == ENGINE_USER_ID
                    and "is_external" not in payload
                    and payload["delegated_is_external"] is caller.external,
                    "Actual engine mint claim shape changed",
                )
                result["engine_external" if kind == "external" else "engine"] = Caller(
                    caller.user_id,
                    caller.organization_id,
                    caller.email,
                    caller.name,
                    caller.external,
                    token,
                    execution_id,
                    True,
                )
            await db.commit()
        # Independent committed read; this is not a rollback-only fixture.
        async with async_session_factory() as db:
            for execution_id in owned:
                _require(
                    await db.get(Execution, execution_id) is not None,
                    "Owned execution not committed",
                )
        yield result
    finally:
        async with async_session_factory() as db:
            for execution_id in owned:
                row = await db.get(Execution, execution_id)
                if row is not None:
                    await db.delete(
                        row
                    )  # Own non-Solution execution; attempts cascade.
            await db.commit()


@asynccontextmanager
async def _integration(
    e2e_client,
    admin,
    factory,
    org_id,
    *,
    template=False,
    mapped=True,
    empty_mapping_entity=False,
):
    suffix = uuid4().hex
    created = e2e_client.post(
        "/api/integrations",
        headers=admin.headers,
        json={
            "name": f"cred-ref-{suffix}",
            "default_entity_id": "oauth",
            "config_schema": [
                {"key": key, "type": config_type}
                for key, config_type in (
                    ("global_secret", "secret"),
                    ("org_secret", "secret"),
                    ("global_value", "string"),
                    ("org_value", "string"),
                )
            ],
        },
    )
    _require(created.status_code == 201, "Owned integration creation failed")
    integration_id = UUID(created.json()["id"])
    provider_id, token_id, key = uuid4(), uuid4(), uuid4().hex
    global_secret, org_secret, stored = (
        f"reference-global-{suffix}",
        f"reference-org-{suffix}",
        f"reference-old-token-{suffix}",
    )
    mapping_id = None
    try:
        async with factory() as db:
            row = await db.get(Integration, integration_id)
            _require(row is not None, "Created integration not committed")
            _require(
                row.default_entity_id == "oauth",
                "Public default entity was not committed",
            )
            for config_key, value, config_type, scope in (
                (
                    "global_secret",
                    encrypt_secret(global_secret),
                    ConfigType.SECRET,
                    None,
                ),
                ("global_value", "global-value", ConfigType.STRING, None),
                ("org_secret", encrypt_secret(org_secret), ConfigType.SECRET, org_id),
                ("org_value", "org-value", ConfigType.STRING, org_id),
            ):
                db.add(
                    ConfigModel(
                        key=config_key,
                        value={"value": value},
                        config_type=config_type,
                        organization_id=scope,
                        integration_id=integration_id,
                        updated_by="owned-cred-reference",
                    )
                )
            db.add(
                OAuthProvider(
                    id=provider_id,
                    provider_name=f"cred-ref-{suffix}",
                    display_name="Owned reference",
                    oauth_flow_type="client_credentials",
                    client_id="scheduler-fixture-client",
                    encrypted_client_secret=encrypt_secret(
                        "scheduler-fixture-secret"
                    ).encode(),
                    token_url=f"{FIXTURE_URL}/{{entity_id}}/token"
                    if template
                    else f"{FIXTURE_URL}/oauth/token",
                    authorization_url=f"{FIXTURE_URL}/oauth/authorize",
                    scopes=["fixture.read"],
                    audience=f"cred-p1-reference:{key}",
                    redirect_uri="/api/oauth/callback/owned-reference",
                    integration_id=integration_id,
                    organization_id=None,
                    status="failed",
                    status_message="Owned stale reference state",
                )
            )
            await db.flush()
            db.add(
                OAuthToken(
                    id=token_id,
                    organization_id=None,
                    provider_id=provider_id,
                    user_id=None,
                    encrypted_access_token=encrypt_secret(stored).encode(),
                    scopes=["fixture.read"],
                    expires_at=datetime.now(UTC) + timedelta(days=2),
                    status="failed",
                    status_message="Owned stale reference state",
                )
            )
            await db.commit()
        if mapped:
            mapping = e2e_client.post(
                f"/api/integrations/{integration_id}/mappings",
                headers=admin.headers,
                json={
                    "organization_id": str(org_id),
                    "entity_id": "oauth",
                    "entity_name": "Reference",
                },
            )
            _require(mapping.status_code == 201, "Owned mapping creation failed")
            mapping_id = UUID(mapping.json()["id"])
            if empty_mapping_entity:
                async with factory() as db:
                    row = await db.get(IntegrationMapping, mapping_id)
                    _require(row is not None, "Owned mapping not committed")
                    row.entity_id = (
                        ""  # Actual get falls back to integration.default_entity_id.
                    )
                    await db.commit()
        yield IntegrationFixture(
            f"cred-ref-{suffix}",
            integration_id,
            provider_id,
            token_id,
            key,
            mapping_id,
            global_secret,
            org_secret,
            stored,
        )
    finally:
        async with factory() as db:
            for model, predicate in (
                (
                    IntegrationMapping,
                    IntegrationMapping.integration_id == integration_id,
                ),
                (ConfigModel, ConfigModel.integration_id == integration_id),
                (OAuthToken, OAuthToken.provider_id == provider_id),
                (OAuthProvider, OAuthProvider.id == provider_id),
            ):
                for row in (await db.scalars(select(model).where(predicate))).all():
                    await db.delete(row)
                await db.flush()
            await db.commit()
        deleted = e2e_client.delete(
            f"/api/integrations/{integration_id}", headers=admin.headers
        )
        _require(
            deleted.status_code == 204, "Owned integration soft-delete cleanup failed"
        )
        # Public soft-deleted integration/audit history remains until owned-stack teardown.


async def _receipt(key):
    async with httpx.AsyncClient(
        base_url=FIXTURE_URL, trust_env=False, timeout=5
    ) as client:
        response = await client.get(f"/__reference/oauth-receipts/{key}")
    _require(response.status_code == 200, "Internal OAuth receipt service unavailable")
    return response.json()


async def _state(factory, fixture):
    async with factory() as db:
        token, provider = (
            await db.get(OAuthToken, fixture.token_id),
            await db.get(OAuthProvider, fixture.provider_id),
        )
        _require(
            token is not None and provider is not None,
            "Owned committed OAuth rows missing",
        )
        raw = token.encrypted_access_token
        text = decrypt_secret(raw.decode() if isinstance(raw, bytes) else raw)
        tokens = (
            await db.scalars(
                select(OAuthToken.id).where(
                    OAuthToken.provider_id == fixture.provider_id
                )
            )
        ).all()
        return {
            "token_id": token.id,
            "tokens": tuple(tokens),
            "cipher_digest": sha256(raw).hexdigest(),
            "stored": text == fixture.stored_token,
            "fixture": text == FIXTURE_ACCESS_TOKEN,
            "token_status": token.status,
            "token_message": token.status_message,
            "provider_status": provider.status,
            "provider_message": provider.status_message,
            "last_refresh": token.last_refresh_at,
            "provider_refresh": provider.last_token_refresh,
            "expires": token.expires_at,
            "scopes": tuple(token.scopes),
        }


async def _call(
    caller, operation, name, *, transport, api_url=E2E_API_URL, entity_id=None
):
    if transport == "http":
        body = {"name": name, "scope": str(caller.organization_id)}
        if entity_id is not None:
            body["entity_id"] = entity_id
        async with httpx.AsyncClient(
            base_url=api_url, trust_env=False, timeout=15
        ) as client:
            response = await client.post(
                f"/api/sdk/integrations/{operation}",
                json=body,
                headers={"Authorization": f"Bearer {caller.token}"},
            )
        _require(response.status_code == 200, "Reference HTTP read failed")
        return response.json()
    _require(transport == "sdk", "Unknown reference transport")
    client = BifrostClient(api_url=api_url, access_token=caller.token)
    ctx = ExecutionContext(
        user_id=str(caller.user_id),
        email=caller.email,
        name=caller.name,
        scope=str(caller.organization_id),
        organization=SDKOrganization(
            id=str(caller.organization_id), name="Reference org"
        ),
        is_platform_admin=False,
        is_function_key=False,
        is_external=caller.external,
        is_provider_org=False,
        execution_id=str(caller.execution_id or "reference-http"),
        solution_id=None,
    )
    _set_client(client)
    set_execution_context(ctx)
    try:
        # No scope/oauth_scope override; these are the unchanged public SDK methods.
        value = (
            await integrations.get(name)
            if operation == "get"
            else await integrations.get_mapping(name, entity_id=entity_id)
        )
        return value.model_dump(mode="json") if value is not None else None
    except Exception:  # noqa: BLE001 - SDK errors may contain decrypted response input.
        raise AssertionError(
            "Unchanged SDK reference call failed; inspect sanitized service evidence"
        ) from None
    finally:
        clear_execution_context()
        _clear_client()
        await client.close()


def _assert_profile(
    payload,
    caller,
    fixture,
    operation,
    *,
    mapped=True,
    empty_mapping_entity=False,
    refreshed=False,
):
    _require(isinstance(payload, dict), "Expected integration/mapping result missing")
    _require(
        payload.get("integration_id") == str(fixture.integration_id),
        "Wrong integration selected",
    )
    external = caller.external and not caller.engine
    config = payload.get("config")
    _require(isinstance(config, dict), "Config result shape changed")
    expected = {}
    if not external:
        expected["global_value"] = "global-value"
        if operation == "get" or not caller.engine:
            expected["global_secret"] = fixture.global_secret
    if mapped:
        expected.update(org_value="org-value", org_secret=fixture.org_secret)
    _require(
        config == expected,
        "Legacy result tier/secret profile differs from source hypothesis",
    )
    expected_entity = (
        "" if operation == "get_mapping" and empty_mapping_entity else "oauth"
    )
    _require(
        payload.get("entity_id") == expected_entity,
        "Entity selection/default fallback changed",
    )
    if operation == "get_mapping":
        _require(
            payload.get("id") == str(fixture.mapping_id)
            and payload.get("organization_id") == str(caller.organization_id)
            and payload.get("oauth_token_id") is None
            and "oauth" not in payload,
            "Mapping public selector/result profile changed",
        )
    else:
        _require(
            payload.get("config_secret_keys") == ["global_secret", "org_secret"],
            "Secret schema metadata changed",
        )
        oauth = payload.get("oauth")
        if external:
            _require(
                oauth is None, "Direct external caller unexpectedly received OAuth"
            )
        else:
            _require(
                isinstance(oauth, dict)
                and oauth.get("client_secret") == "scheduler-fixture-secret"
                and oauth.get("access_token")
                == (FIXTURE_ACCESS_TOKEN if refreshed else fixture.stored_token),
                "Legacy OAuth result profile differs from source hypothesis",
            )


@pytest.mark.parametrize("kind", CALLER_KINDS)
@pytest.mark.parametrize("transport", ("http", "sdk"))
async def test_mapped_legacy_result_profiles_and_selector_precedence(
    e2e_client,
    platform_admin,
    async_session_factory,
    callers,
    kind,
    transport,
):
    caller = callers[kind]
    async with _integration(
        e2e_client, platform_admin, async_session_factory, caller.organization_id
    ) as fixture:
        before = await _state(async_session_factory, fixture)
        for operation in ("get", "get_mapping"):
            payload = await _call(
                caller,
                operation,
                fixture.name,
                transport=transport,
                entity_id="conflicting-selector"
                if operation == "get_mapping"
                else None,
            )
            _assert_profile(payload, caller, fixture, operation)
        _require(
            await _receipt(fixture.receipt_key)
            == {"attempted": 0, "statuses": {}, "exhausted": False},
            "No-template get/mapping unexpectedly contacted OAuth provider",
        )
        _require(
            await _state(async_session_factory, fixture) == before,
            "Read control changed OAuth rows",
        )


@pytest.mark.parametrize("kind", CALLER_KINDS)
@pytest.mark.parametrize("transport", ("http", "sdk"))
async def test_missing_mapping_defaults_missing_name_and_exact_name_binding(
    e2e_client,
    platform_admin,
    async_session_factory,
    callers,
    kind,
    transport,
):
    caller = callers[kind]
    async with _integration(
        e2e_client,
        platform_admin,
        async_session_factory,
        caller.organization_id,
        mapped=False,
    ) as fixture:
        before = await _state(async_session_factory, fixture)
        _assert_profile(
            await _call(caller, "get", fixture.name, transport=transport),
            caller,
            fixture,
            "get",
            mapped=False,
        )
        _require(
            await _call(caller, "get_mapping", fixture.name, transport=transport)
            is None,
            "Missing mapping must return null",
        )
        for operation in ("get", "get_mapping"):
            _require(
                await _call(caller, operation, fixture.name + " ", transport=transport)
                is None,
                "Name lookup unexpectedly trims input",
            )
            _require(
                await _call(
                    caller, operation, "missing-" + fixture.name, transport=transport
                )
                is None,
                "Missing integration must return null",
            )
        _require(
            await _receipt(fixture.receipt_key)
            == {"attempted": 0, "statuses": {}, "exhausted": False},
            "Absence/default controls unexpectedly contacted provider",
        )
        _require(
            await _state(async_session_factory, fixture) == before,
            "Absence/default controls changed committed OAuth state",
        )


@pytest.mark.parametrize("kind", CALLER_KINDS)
@pytest.mark.parametrize("transport", ("http", "sdk"))
async def test_entity_template_recovery_without_oauth_scope_override(
    e2e_client,
    platform_admin,
    async_session_factory,
    callers,
    kind,
    transport,
):
    caller = callers[kind]
    async with _integration(
        e2e_client,
        platform_admin,
        async_session_factory,
        caller.organization_id,
        template=True,
        empty_mapping_entity=True,
    ) as fixture:
        before = await _state(async_session_factory, fixture)
        _require(
            await _receipt(fixture.receipt_key)
            == {"attempted": 0, "statuses": {}, "exhausted": False},
            "Fresh synthetic OAuth scenario has unexpected exchanges",
        )
        _assert_profile(
            await _call(caller, "get_mapping", fixture.name, transport=transport),
            caller,
            fixture,
            "get_mapping",
            empty_mapping_entity=True,
        )
        _require(
            await _receipt(fixture.receipt_key)
            == {"attempted": 0, "statuses": {}, "exhausted": False},
            "get_mapping unexpectedly contacted OAuth provider",
        )
        _require(
            await _state(async_session_factory, fixture) == before,
            "get_mapping changed committed OAuth state",
        )
        should_refresh = not (caller.external and not caller.engine)
        start = datetime.now(UTC)
        payload = await _call(caller, "get", fixture.name, transport=transport)
        _assert_profile(payload, caller, fixture, "get", refreshed=should_refresh)
        after = await _state(async_session_factory, fixture)
        if not should_refresh:
            _require(
                await _receipt(fixture.receipt_key)
                == {"attempted": 0, "statuses": {}, "exhausted": False},
                "External get unexpectedly contacted OAuth provider",
            )
            _require(after == before, "External get unexpectedly persisted OAuth state")
        else:
            _require(
                await _receipt(fixture.receipt_key)
                == {"attempted": 1, "statuses": {"200": 1}, "exhausted": False},
                "Expected exactly one actual synthetic OAuth exchange",
            )
            _require(
                after["token_id"] == before["token_id"]
                and after["tokens"] == before["tokens"]
                and after["cipher_digest"] != before["cipher_digest"]
                and after["fixture"]
                and not after["stored"],
                "Recovery did not update only the actual existing cascaded token",
            )
            _require(
                after["token_status"] == after["provider_status"] == "completed"
                and after["token_message"] is None
                and after["provider_message"] is None
                and after["last_refresh"] >= start
                and after["provider_refresh"] >= start
                and start + timedelta(minutes=59)
                <= after["expires"]
                <= datetime.now(UTC) + timedelta(hours=1)
                and after["scopes"] == ("fixture.read",),
                "Recovery state was not committed/cleared with expected expiry and scopes",
            )


class _ActorObserver:
    """Read ContextVars inside production request middleware; forward untouched."""

    def __init__(self, app, connection: Connection, lifespan_completions: set[str]):
        self.app, self.connection = app, connection
        self.lifespan_completions = lifespan_completions

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":

            async def observe_send(message):
                # Read only actual ASGI completion messages; forward unchanged.
                if message["type"] in (
                    "lifespan.startup.complete",
                    "lifespan.shutdown.complete",
                ):
                    self.lifespan_completions.add(message["type"])
                await send(message)

            await self.app(scope, receive, observe_send)
            return
        paths = ("/api/sdk/integrations/get", "/api/sdk/integrations/get_mapping")
        if scope["type"] == "http" and scope.get("path") in paths:
            from src.core.request_context import get_request_user
            from src.services.audit_context import current_actor

            actor, user = current_actor(), get_request_user()
            record = {
                "kind": "actor",
                "path": scope["path"],
                "method": scope["method"],
                "actor_user": str(actor.user_id) if actor and actor.user_id else None,
                "actor_org": str(actor.organization_id)
                if actor and actor.organization_id
                else None,
                "actor_email": actor.email if actor else None,
                "actor_name": actor.name if actor else None,
                "source": actor.source if actor else None,
                "request_user": user.user_id if user else None,
                "request_name": user.user_name if user else None,
            }
            _require(
                all(
                    value is None
                    or (
                        isinstance(value, str)
                        and len(value.encode()) <= 320
                        and "\x00" not in value
                    )
                    for value in record.values()
                ),
                "Reference actor observation exceeds bounded identity profile",
            )
            self.connection.send(record)
        await self.app(scope, receive, send)


def _reference_api_process(listener: socket.socket, connection: Connection) -> None:
    """A spawned process owns the actual app's global resources and lifespan."""

    async def serve():
        import uvicorn
        from starlette.middleware import Middleware
        from uvicorn.lifespan.on import LifespanOn

        from src.main import create_app

        # requirements.lock pins 0.46.0. Its LifespanOn suppresses exceptions
        # and sets both events in finally; event.is_set() alone is not proof.
        _require(uvicorn.__version__ == "0.46.0", "Pinned Uvicorn reference changed")
        completions: set[str] = set()
        app = create_app()
        # Starlette wraps user_middleware in reverse order. Append means the
        # observer runs inside ALL original user middleware, before the router.
        app.user_middleware.append(
            Middleware(
                _ActorObserver,
                connection=connection,
                lifespan_completions=completions,
            )
        )
        server = uvicorn.Server(
            uvicorn.Config(app, log_config=None, access_log=False, lifespan="on")
        )
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Unchanged reference application startup failed")
                await asyncio.sleep(0.01)
            # These are verified 0.46.0 source attributes, without defaults.
            lifespan = server.lifespan
            _require(
                type(lifespan) is LifespanOn
                and lifespan.startup_event.is_set()
                and not lifespan.shutdown_event.is_set()
                and not lifespan.startup_failed
                and not lifespan.shutdown_failed
                and not lifespan.error_occurred
                and not lifespan.should_exit
                and completions == {"lifespan.startup.complete"},
                "Unchanged reference lifespan startup did not complete safely",
            )
            connection.send({"kind": "ready"})
            requested_stop = False
            while not task.done():
                if connection.poll():
                    _require(
                        connection.recv() == "stop",
                        "Unexpected private reference command",
                    )
                    requested_stop = True
                    server.should_exit = True
                    break
                await asyncio.sleep(0.01)
            await task
            _require(
                requested_stop
                and not server.force_exit
                and lifespan.startup_event.is_set()
                and lifespan.shutdown_event.is_set()
                and not lifespan.startup_failed
                and not lifespan.shutdown_failed
                and not lifespan.error_occurred
                and not lifespan.should_exit
                and completions
                == {"lifespan.startup.complete", "lifespan.shutdown.complete"},
                "Unchanged reference lifespan shutdown did not complete safely",
            )
            connection.send({"kind": "shutdown_complete"})
        finally:
            server.should_exit = True
            if not task.done():
                await task

    try:
        asyncio.run(serve())
    except BaseException:  # noqa: BLE001 - Static IPC denial and nonzero child exit.
        # Never send exception strings, request inputs, or traceback locals.
        try:
            connection.send({"kind": "blocked"})
        except (BrokenPipeError, EOFError, OSError):
            pass
        raise SystemExit(1) from None
    finally:
        listener.close()
        connection.close()


async def _next_record(connection: Connection, expected_kind: str):
    _require(
        await asyncio.to_thread(
            connection.poll, 5 if expected_kind == "shutdown_complete" else 15
        ),
        f"Reference process {expected_kind} acknowledgement unavailable",
    )
    try:
        record = connection.recv()
    except EOFError:
        raise AssertionError("Reference process ended before observation") from None
    _require(
        isinstance(record, dict) and record.get("kind") == expected_kind,
        f"Unchanged reference process blocked before {expected_kind} acknowledgement",
    )
    return record


async def _startup_baseline(factory):
    """Refuse startup that would seed/reset shared durable state in this lane."""
    from src.config import get_settings
    from src.models.orm.config import SystemConfig
    from src.models.orm.policy_rule import PolicyRule
    from src.services.kubernetes_execution import (
        KUBERNETES_CONFIG_CATEGORY,
        KUBERNETES_NOTICE_KEY,
        _detection_snapshot,
    )

    settings = get_settings()
    async with factory() as db:
        default_user = None
        if settings.default_user_email and settings.default_user_password:
            _require(
                not settings.debug,
                "Reference startup would synchronize shared debug credentials",
            )
            default_user = (
                await db.scalars(
                    select(User).where(User.email == settings.default_user_email)
                )
            ).one_or_none()
            _require(
                default_user is not None
                and isinstance(default_user.hashed_password, str),
                "Reference startup would create an unowned default user",
            )
        rules = (
            await db.scalars(
                select(PolicyRule).where(
                    PolicyRule.name == "admin_bypass",
                    PolicyRule.organization_id.is_(None),
                    PolicyRule.domain.in_(("file", "table")),
                )
            )
        ).all()
        _require(
            {rule.domain for rule in rules} == {"file", "table"}
            and all(rule.is_builtin for rule in rules),
            "Reference startup requires already-seeded built-in policies",
        )
        notice = (
            await db.scalars(
                select(SystemConfig).where(
                    SystemConfig.category == KUBERNETES_CONFIG_CATEGORY,
                    SystemConfig.key == KUBERNETES_NOTICE_KEY,
                    SystemConfig.organization_id.is_(None),
                )
            )
        ).one_or_none()
        _require(
            notice is not None and notice.value_json == _detection_snapshot(settings),
            "Reference startup would change shared Kubernetes detection state",
        )
        # Compare only fingerprints/identities, never expose a password hash.
        return (
            sha256(default_user.hashed_password.encode()).hexdigest()
            if default_user
            else None,
            default_user.mfa_enabled if default_user else None,
            tuple(
                sorted((str(rule.id), rule.domain, rule.is_builtin) for rule in rules)
            ),
            str(notice.id),
            notice.updated_at,
        )


@asynccontextmanager
async def _observed_api(factory):
    baseline = await _startup_baseline(factory)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    process = context.Process(target=_reference_api_process, args=(listener, child))
    started = False
    primary_failure: BaseException | None = None
    try:
        process.start()
        started = True
        child.close()
        _require(
            await _next_record(parent, "ready") == {"kind": "ready"},
            "Reference startup acknowledgement shape changed",
        )
        _require(
            await _startup_baseline(factory) == baseline,
            "Reference startup changed shared baseline",
        )
        yield f"http://127.0.0.1:{port}", parent
    except BaseException as error:
        primary_failure = error
        raise
    finally:
        child.close()
        cleanup_failures: list[BaseException] = []
        acknowledged = False
        if started:
            try:
                if process.is_alive():
                    parent.send("stop")
                record = await _next_record(parent, "shutdown_complete")
                _require(
                    record == {"kind": "shutdown_complete"},
                    "Reference shutdown acknowledgement shape changed",
                )
                acknowledged = True
            except BaseException as error:  # noqa: BLE001 - Retain error while cleaning owned process.
                cleanup_failures.append(error)
            finally:
                # Release our descriptor only after attempting to consume the
                # child's actual completion acknowledgement. Failure is retained.
                listener.close()
                await asyncio.to_thread(process.join, 5)
                exited_gracefully = not process.is_alive() and process.exitcode == 0
                if process.is_alive():
                    process.terminate()
                    await asyncio.to_thread(process.join, 5)
                if process.is_alive():
                    process.kill()
                    await asyncio.to_thread(process.join, 5)
                parent.close()
                if process.is_alive():
                    cleanup_failures.append(
                        AssertionError("Owned reference process teardown failed")
                    )
                else:
                    process.close()
                if not acknowledged or not exited_gracefully:
                    cleanup_failures.append(
                        AssertionError(
                            "Unchanged reference lifespan did not acknowledge a safe shutdown"
                        )
                    )
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                        probe.settimeout(1)
                        _require(
                            probe.connect_ex(("127.0.0.1", port)) == errno.ECONNREFUSED,
                            "Owned reference socket remains reachable after teardown",
                        )
                    _require(
                        await _startup_baseline(factory) == baseline,
                        "Reference shutdown changed shared baseline",
                    )
                except BaseException as error:  # noqa: BLE001 - Preserve all cleanup evidence.
                    cleanup_failures.append(error)
        else:
            listener.close()
            parent.close()
        if cleanup_failures:
            # Keep the original startup/assertion error alongside cleanup errors;
            # an absent ack, forced stop or nonzero child cannot mask it or pass.
            if primary_failure is not None:
                raise BaseExceptionGroup(
                    "Reference failure and cleanup failures",
                    [primary_failure, *cleanup_failures],
                ) from None
            raise BaseExceptionGroup(
                "Reference cleanup failures", cleanup_failures
            ) from None


def _assert_actor(record, caller, operation):
    payload = decode_token(caller.token, expected_type="access")
    _require(payload is not None, "Actual reference caller token no longer valid")
    primary_id = ENGINE_USER_ID if caller.engine else str(caller.user_id)
    _require(
        record
        == {
            "kind": "actor",
            "path": f"/api/sdk/integrations/{operation}",
            "method": "POST",
            "actor_user": primary_id,
            "actor_org": payload.get("org_id"),
            "actor_email": payload.get("email"),
            "actor_name": payload.get("name"),
            "source": "http",
            "request_user": primary_id,
            "request_name": payload.get("name") or payload.get("email") or primary_id,
        },
        "Actual audit/request attribution differs from primary-token source hypothesis",
    )


async def _audit_ids(factory, fixture):
    resource_ids = [fixture.integration_id, fixture.provider_id, fixture.token_id]
    if fixture.mapping_id is not None:
        resource_ids.append(fixture.mapping_id)
    async with factory() as db:
        return frozenset(
            await db.scalars(
                select(AuditLog.id).where(AuditLog.resource_id.in_(resource_ids))
            )
        )


async def test_actual_request_actor_profiles_and_anonymous_isolation(
    e2e_client,
    platform_admin,
    async_session_factory,
    callers,
):
    # Exactly one observed application lifespan; no repeated cached MCP startup.
    async with _observed_api(async_session_factory) as (api_url, connection):
        # Ordinary comes first and is the mandatory middleware-placement control.
        for kind in CALLER_KINDS:
            caller = callers[kind]
            for transport in ("http", "sdk"):
                async with _integration(
                    e2e_client,
                    platform_admin,
                    async_session_factory,
                    caller.organization_id,
                ) as fixture:
                    audit_before = await _audit_ids(async_session_factory, fixture)
                    for operation in ("get", "get_mapping"):
                        # Same committed fixture; controls have no OAuth/DML effects.
                        production = await _call(
                            caller, operation, fixture.name, transport=transport
                        )
                        observed = await _call(
                            caller,
                            operation,
                            fixture.name,
                            transport=transport,
                            api_url=api_url,
                        )
                        _assert_profile(production, caller, fixture, operation)
                        _assert_profile(observed, caller, fixture, operation)
                        _require(
                            production == observed,
                            "Observed app differs from actual supported API result",
                        )
                        _assert_actor(
                            await _next_record(connection, "actor"), caller, operation
                        )
                        # A distinct actual unauthenticated request must have no prior caller.
                        async with httpx.AsyncClient(
                            base_url=api_url, trust_env=False, timeout=15
                        ) as client:
                            response = await client.post(
                                f"/api/sdk/integrations/{operation}",
                                json={
                                    "name": fixture.name,
                                    "scope": str(caller.organization_id),
                                },
                            )
                        _require(
                            response.status_code == 401,
                            "Anonymous selected route unexpectedly admitted",
                        )
                        anonymous = await _next_record(connection, "actor")
                        _require(
                            anonymous
                            == {
                                "kind": "actor",
                                "path": f"/api/sdk/integrations/{operation}",
                                "method": "POST",
                                "actor_user": None,
                                "actor_org": None,
                                "actor_email": None,
                                "actor_name": None,
                                "source": "http",
                                "request_user": None,
                                "request_name": None,
                            },
                            "Anonymous request inherited prior actor/request identity",
                        )
                    _require(
                        await _audit_ids(async_session_factory, fixture)
                        == audit_before,
                        "Selected SDK reads unexpectedly emitted an owned-resource audit event",
                    )
                    _require(
                        await _receipt(fixture.receipt_key)
                        == {"attempted": 0, "statuses": {}, "exhausted": False},
                        "Actor observation controls unexpectedly contacted provider",
                    )
