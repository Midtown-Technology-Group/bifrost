"""Staged H/R mechanism: unchanged SDK, real app/PG and synthetic HTTP only.

All source/integration/config/OAuth literals are owned synthetic fixtures. This
does not certify the selected real A+B workload, owner bootstrap, HTTPS custody,
vendor redirects, mechanical writer exclusion, or broader C2/C3 acceptance.
"""

import asyncio
import errno
import socket
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from urllib.parse import parse_qs
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select, update

from bifrost._context import clear_execution_context, set_execution_context
from bifrost._execution_context import ExecutionContext
from bifrost.client import (
    BifrostClient,
    _clear_client,
    _reset_refresh_coordinators_for_tests,
    _set_client,
    refresh_connection_access_token,
)
from bifrost.integrations import integrations
from bifrost.models import Organization as SDKOrganization
from src.config import get_settings
from src.core import runtime_sdk_ingress as ingress
from src.core.request_context import get_request_session_id, get_request_user
from src.core.runtime_sdk_credentials import (
    GrantReference,
    RuntimeSDKDenied,
    SelectedSDKPolicy,
    decode_runtime_sdk_access,
    sign_runtime_sdk_token,
)
from src.core.security import (
    ENGINE_USER_ID,
    create_access_token,
    decrypt_secret,
    encrypt_secret,
)
from src.main import create_app
from src.models.enums import ConfigType
from src.models.orm import Config as ConfigModel
from src.models.orm.executions import WorkflowExecutionAttempt
from src.models.orm.integrations import (
    Integration,
    IntegrationConfigSchema,
    IntegrationMapping,
)
from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, User, UserRole
from src.repositories.integrations import IntegrationsRepository
from src.routers import cli
from src.services import runtime_sdk_grants as grants
from src.services.audit_context import current_actor
from tests.unit.core.test_runtime_sdk_ingress import _require
from tests.unit.services import test_runtime_sdk_grants as foundation

credential_cohort = foundation.credential_cohort
make_work = foundation.make_work
_bundle = foundation._bundle

pytestmark = pytest.mark.e2e


@asynccontextmanager
async def _server(app):
    """Own the loopback listener and real ASGI lifespan through shutdown."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_config=None, access_log=False, lifespan="on")
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))

    async def started():
        while not server.started:
            if task.done():
                await task
                raise AssertionError("Synthetic server failed before startup")
            await asyncio.sleep(0.01)

    try:
        await asyncio.wait_for(started(), 15)
        lifespan = server.lifespan
        _require(
            lifespan.startup_event.is_set()
            and not lifespan.startup_failed
            and not lifespan.error_occurred
            and not lifespan.should_exit,
            "Actual ASGI startup failed",
        )
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, 5)
            _require(
                server.lifespan.shutdown_event.is_set()
                and not server.lifespan.shutdown_failed
                and not server.lifespan.error_occurred,
                "Actual ASGI shutdown failed",
            )
        finally:
            listener.close()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(1)
            _require(
                probe.connect_ex(("127.0.0.1", port)) == errno.ECONNREFUSED,
                "Owned loopback listener survived shutdown",
            )


@asynccontextmanager
async def _case(make_work, *, external=False, token_url=None):
    """Commit fixture after production startup, preserving real lease/fences."""
    work = await make_work(timeout=0)
    suffix = uuid4().hex
    name = f"Synthetic HR {suffix}"
    integration_id, mapping_id, provider_id, token_id, role_id = (
        uuid4() for _ in range(5)
    )
    role_name = f"Synthetic HR Reader {suffix}"
    global_secret, org_secret, stored_token = (
        f"synthetic-{tier}-{suffix}" for tier in ("global", "org", "oauth")
    )
    caller = work.caller.model_copy(
        update={"caller_external": external, "roles": (role_name,)}
    )
    policy = SelectedSDKPolicy(
        operations=tuple(
            op.model_copy(
                update={
                    "integration_name": name,
                    "scope_kind": "default",
                    "scope_organization_id": None,
                }
            )
            for op in work.policy.operations
        )
    )
    async with work.cohort.factory() as db, db.begin():
        user = await db.get(User, caller.caller_user_id)
        original_external = user.is_external
        user.is_external = external
        db.add(Role(id=role_id, name=role_name, created_by="synthetic-hr"))
        db.add(
            Integration(
                id=integration_id, name=name, default_entity_id="synthetic-entity"
            )
        )
        await db.flush()
        db.add(
            UserRole(
                user_id=caller.caller_user_id,
                role_id=role_id,
                assigned_by="synthetic-hr",
            )
        )
        for position, key in enumerate(
            ("global_secret", "org_secret", "global_value", "org_value")
        ):
            db.add(
                IntegrationConfigSchema(
                    integration_id=integration_id,
                    key=key,
                    type="secret" if key.endswith("secret") else "string",
                    position=position,
                )
            )
        for key, value, config_type, org in (
            ("global_secret", encrypt_secret(global_secret), ConfigType.SECRET, None),
            ("global_value", "global-value", ConfigType.STRING, None),
            (
                "org_secret",
                encrypt_secret(org_secret),
                ConfigType.SECRET,
                work.cohort.organization_id,
            ),
            ("org_value", "org-value", ConfigType.STRING, work.cohort.organization_id),
        ):
            db.add(
                ConfigModel(
                    key=key,
                    value={"value": value},
                    config_type=config_type,
                    organization_id=org,
                    integration_id=integration_id,
                    updated_by="synthetic-hr",
                )
            )
        db.add(
            OAuthProvider(
                id=provider_id,
                provider_name=f"synthetic-hr-{suffix}",
                display_name="Synthetic HR",
                oauth_flow_type="client_credentials",
                client_id="synthetic-client",
                encrypted_client_secret=encrypt_secret(
                    "synthetic-client-secret"
                ).encode(),
                token_url=token_url or "http://127.0.0.1:1/synthetic-unused-token",
                authorization_url="http://127.0.0.1:1/synthetic-unused-authorize",
                scopes=["synthetic.read"],
                redirect_uri="/synthetic-unused-callback",
                integration_id=integration_id,
                organization_id=None,
                status="failed",
                status_message="Synthetic stored-token profile",
            )
        )
        await db.flush()
        db.add(
            OAuthToken(
                id=token_id,
                organization_id=None,
                provider_id=provider_id,
                encrypted_access_token=encrypt_secret(stored_token).encode(),
                scopes=["synthetic.read"],
                expires_at=datetime.now(UTC) + timedelta(days=2),
                status="failed",
                status_message="Synthetic stored-token profile",
            )
        )
        db.add(
            IntegrationMapping(
                id=mapping_id,
                integration_id=integration_id,
                organization_id=work.cohort.organization_id,
                entity_id="synthetic-entity",
                entity_name="Synthetic entity",
            )
        )
    try:
        reference = await work.provision(caller=caller, policy=policy)
        bundle = await grants.issue_workflow_runtime_sdk_token(
            work.cohort.factory, reference=reference
        )
        human_token = create_access_token(
            {
                "sub": str(caller.caller_user_id),
                "email": caller.caller_email,
                "name": caller.caller_name,
                "org_id": str(caller.caller_organization_id),
                "is_superuser": False,
                "is_external": external,
                "is_provider_org": False,
                "roles": [role_name],
            }
        )
        yield {
            "work": work,
            "caller": caller,
            "name": name,
            "bundle": bundle,
            "reference": reference,
            "human_token": human_token,
            "integration_id": integration_id,
            "mapping_id": mapping_id,
            "provider_id": provider_id,
            "token_id": token_id,
            "global_secret": global_secret,
            "org_secret": org_secret,
            "stored_token": stored_token,
        }
    finally:
        async with work.cohort.factory() as db, db.begin():
            await db.execute(
                update(User)
                .where(User.id == caller.caller_user_id)
                .values(is_external=original_external)
            )
            await db.execute(delete(UserRole).where(UserRole.role_id == role_id))
            await db.execute(delete(Role).where(Role.id == role_id))
            await db.execute(
                delete(IntegrationMapping).where(IntegrationMapping.id == mapping_id)
            )
            await db.execute(
                delete(ConfigModel).where(ConfigModel.integration_id == integration_id)
            )
            await db.execute(delete(OAuthToken).where(OAuthToken.id == token_id))
            await db.execute(
                delete(OAuthProvider).where(OAuthProvider.id == provider_id)
            )
            await db.execute(
                delete(Integration).where(Integration.id == integration_id)
            )


async def _sdk(case, api_url, operation, *, human=False):
    caller, work = case["caller"], case["work"]
    token = case["human_token"] if human else case["bundle"].access_token
    client = BifrostClient(api_url=api_url, access_token=token)
    context = ExecutionContext(
        user_id=str(caller.caller_user_id),
        email=caller.caller_email,
        name=caller.caller_name,
        scope=str(caller.effective_organization_id),
        organization=SDKOrganization(
            id=str(caller.effective_organization_id), name="Synthetic HR org"
        ),
        is_platform_admin=False,
        is_function_key=False,
        is_external=caller.caller_external,
        is_provider_org=False,
        execution_id=str(work.start.execution_id),
        solution_id=str(work.cohort.source.solution_install_id),
    )
    _set_client(client)
    set_execution_context(context)
    try:
        result = (
            await integrations.get(case["name"])
            if operation == "get"
            else await integrations.get_mapping(case["name"])
        )
        return result.model_dump(mode="json") if result is not None else None
    except Exception:  # noqa: BLE001 - SDK errors may contain decrypted response values
        raise AssertionError("Unchanged SDK mechanism call failed") from None
    finally:
        clear_execution_context()
        _clear_client()
        await client.close()


def _profile(payload, case, operation, *, human=False):
    _require(isinstance(payload, dict), "Expected SDK profile absent")
    external = human and case["caller"].caller_external
    expected = {"org_value": "org-value", "org_secret": case["org_secret"]}
    if not external:
        expected["global_value"] = "global-value"
        if operation == "get" or human:
            expected["global_secret"] = case["global_secret"]
    _require(
        payload.get("config") == expected,
        "Engine/human/external config profile changed",
    )
    _require(
        payload.get("integration_id") == str(case["integration_id"])
        and payload.get("entity_id") == "synthetic-entity",
        "SDK resolved a different integration/entity",
    )
    if operation == "get":
        oauth = payload.get("oauth")
        if external:
            _require(oauth is None, "Human external caller obtained global OAuth")
        else:
            _require(
                isinstance(oauth, dict)
                and oauth.get("client_secret") == "synthetic-client-secret"
                and oauth.get("access_token") == case["stored_token"],
                "Stored synthetic OAuth profile changed",
            )
        _require(
            payload.get("config_secret_keys") == ["global_secret", "org_secret"],
            "Secret schema metadata changed",
        )
    else:
        _require(
            payload.get("id") == str(case["mapping_id"])
            and payload.get("organization_id")
            == str(case["caller"].effective_organization_id)
            and payload.get("oauth_token_id") is None
            and "oauth" not in payload,
            "Mapping response/profile changed",
        )


def _observe_reads(monkeypatch, principal_records=None):
    records = []
    actual = IntegrationsRepository.get_config_for_mapping

    async def observed(self, *args, **kwargs):
        records.append((current_actor(), get_request_user(), get_request_session_id()))
        return await actual(self, *args, **kwargs)

    monkeypatch.setattr(IntegrationsRepository, "get_config_for_mapping", observed)
    if principal_records is not None:
        actual_external = cli._is_external_user_db

        async def observe_principal(principal, db):
            principal_records.append(
                (
                    principal.is_superuser,
                    principal.is_engine_token,
                    principal.is_external,
                    principal.delegated_user_id,
                    principal.delegated_email,
                    principal.delegated_name,
                    principal.delegated_is_superuser,
                    principal.delegated_is_provider_org,
                    principal.delegated_is_external,
                    principal.engine_execution_id,
                    principal.engine_attempt_token,
                    principal.engine_solution_id,
                )
            )
            return await actual_external(principal, db)

        monkeypatch.setattr(cli, "_is_external_user_db", observe_principal)
    return records


async def _oauth_state(case, *, refreshed_token=None):
    async with case["work"].cohort.factory() as db:
        provider = await db.get(OAuthProvider, case["provider_id"])
        token = await db.get(OAuthToken, case["token_id"])
        _require(provider is not None and token is not None, "Owned OAuth rows missing")
        raw = token.encrypted_access_token
        value = decrypt_secret(raw.decode() if isinstance(raw, bytes) else raw)
        token_ids = tuple(
            (
                await db.scalars(
                    select(OAuthToken.id)
                    .where(OAuthToken.provider_id == provider.id)
                    .order_by(OAuthToken.id)
                )
            ).all()
        )
        return {
            "token_id": token.id,
            "token_ids": token_ids,
            "cipher_digest": sha256(raw).hexdigest(),
            "stored_matches": value == case["stored_token"],
            "refreshed_matches": value == refreshed_token if refreshed_token else False,
            "provider_status": provider.status,
            "token_status": token.status,
            "provider_message": provider.status_message,
            "token_message": token.status_message,
            "provider_refresh": provider.last_token_refresh,
            "token_refresh": token.last_refresh_at,
            "expires": token.expires_at,
            "scopes": tuple(token.scopes),
        }


@pytest.mark.parametrize("external", [False, True])
async def test_actual_sdk_engine_and_direct_human_profiles_actor_and_anonymous_isolation(
    make_work, monkeypatch, external
):
    principals = []
    records = _observe_reads(monkeypatch, principals)
    async with _server(create_app()) as api_url:
        async with _case(make_work, external=external) as case:
            monkeypatch.setattr(
                ingress, "get_session_factory", lambda: case["work"].cohort.factory
            )
            for human in (False, True):
                for operation in ("get", "get_mapping"):
                    before = len(records)
                    oauth_before = await _oauth_state(case)
                    payload = await _sdk(case, api_url, operation, human=human)
                    _profile(payload, case, operation, human=human)
                    assert len(records) == before + 1
                    actor, user, session = records[-1]
                    expected_id = (
                        case["caller"].caller_user_id if human else UUID(ENGINE_USER_ID)
                    )
                    assert actor.user_id == expected_id
                    assert (
                        actor.organization_id
                        == case["caller"].effective_organization_id
                    )
                    assert actor.source == "http"
                    assert user.user_id == str(expected_id)
                    assert session is None
                    if not human:
                        caller = case["caller"]
                        _require(
                            principals[-1]
                            == (
                                True,
                                True,
                                False,
                                caller.caller_user_id,
                                caller.caller_email,
                                caller.caller_name,
                                caller.caller_admin,
                                caller.caller_provider,
                                caller.caller_external,
                                case["work"].start.execution_id,
                                case["work"].start.private_claim_token,
                                str(case["work"].cohort.source.solution_install_id),
                            ),
                            "Actual Engine/delegation/fence profile changed",
                        )
                    _require(
                        await _oauth_state(case) == oauth_before,
                        "Unexpired stored non-template OAuth read changed committed state",
                    )
            before = len(records)
            async with httpx.AsyncClient(base_url=api_url, trust_env=False) as client:
                response = await client.post(
                    "/api/sdk/integrations/get", json={"name": case["name"]}
                )
                assert response.status_code == 401
                _require(
                    response.json() == {"detail": "Not authenticated"},
                    "Anonymous legacy error contract changed",
                )
                assert response.headers["www-authenticate"] == "Bearer"
            assert len(records) == before


async def test_actual_http_closed_keys_precedence_and_concurrent_actor_contexts(
    make_work, monkeypatch
):
    records = _observe_reads(monkeypatch)
    async with _server(create_app()) as api_url:
        async with _case(make_work) as case:
            work, bundle = case["work"], case["bundle"]
            monkeypatch.setattr(
                ingress, "get_session_factory", lambda: work.cohort.factory
            )
            body = {
                "name": case["name"],
                "scope": str(work.cohort.organization_id),
                "solution": str(work.cohort.source.solution_install_id),
            }
            auth_header = {"Authorization": f"Bearer {bundle.access_token}"}
            async with httpx.AsyncClient(base_url=api_url, trust_env=False) as client:
                for path, extra in (
                    ("get", {"extra": True}),
                    ("get", {"oauth_scope": "synthetic.read"}),
                    ("get", {"scope": "global"}),
                    (
                        "get_mapping",
                        {"solution": str(work.cohort.source.solution_install_id)},
                    ),
                    ("get_mapping", {"entity_id": "synthetic-override"}),
                ):
                    candidate = dict(body)
                    if path == "get_mapping":
                        candidate.pop("solution")
                    candidate.update(extra)
                    before = len(records)
                    denied = await client.post(
                        f"/api/sdk/integrations/{path}",
                        json=candidate,
                        headers=auth_header,
                    )
                    assert denied.status_code == 401
                    _require(
                        denied.json() == {"detail": "Invalid runtime SDK credential"},
                        "Closed selector denial leaked details",
                    )
                    assert len(records) == before
                malformed = await client.post(
                    "/api/sdk/integrations/get",
                    json={"scope": body["scope"]},
                    headers=auth_header,
                )
                assert malformed.status_code == 422
                cookie_only = await client.post(
                    "/auth/refresh",
                    headers={"Cookie": f"refresh_token={bundle.refresh_token}"},
                )
                assert cookie_only.status_code == 401
                _require(
                    cookie_only.json() == {"detail": "Invalid runtime SDK credential"},
                    "Cookie-only dedicated renewal parsed absent JSON",
                )
                ordinary = {
                    "Authorization": f"Bearer {case['human_token']}",
                    "Cookie": f"access_token={bundle.access_token}",
                    "X-Bifrost-Watch-Session": "synthetic-watch",
                }
                response = await client.post(
                    "/api/sdk/integrations/get",
                    json={**body, "extra": True},
                    headers=ordinary,
                )
                assert response.status_code == 200
                _profile(response.json(), case, "get", human=True)
                assert records[-1][0].user_id == case["caller"].caller_user_id
                assert records[-1][2] == "synthetic-watch"
                before = len(records)
                responses = await asyncio.gather(
                    client.post(
                        "/api/sdk/integrations/get",
                        json=body,
                        headers={
                            **auth_header,
                            "X-Bifrost-Watch-Session": "synthetic-untrusted",
                        },
                    ),
                    client.post(
                        "/api/sdk/integrations/get", json=body, headers=ordinary
                    ),
                )
                assert all(response.status_code == 200 for response in responses)
                observed = records[before:]
                assert len(observed) == 2
                assert {
                    (actor.user_id, session) for actor, _user, session in observed
                } == {
                    (UUID(ENGINE_USER_ID), None),
                    (case["caller"].caller_user_id, "synthetic-watch"),
                }


@pytest.mark.parametrize("external", [False, True])
async def test_actual_sdk_template_oauth_recovery_commits_existing_engine_profile(
    make_work, monkeypatch, external
):
    """Finite synthetic provider; no live vendor or vendor confinement claim."""
    provider_app = FastAPI()
    exchanges = []
    refreshed_token = f"synthetic-recovered-{uuid4().hex}"

    @provider_app.post("/synthetic-entity/token")
    async def token_endpoint(request: Request):
        form = parse_qs((await request.body()).decode())
        _require(
            form.get("grant_type") == ["client_credentials"]
            and form.get("client_id") == ["synthetic-client"]
            and form.get("client_secret") == ["synthetic-client-secret"]
            and form.get("scope") == ["synthetic.read"],
            "Synthetic OAuth request profile changed",
        )
        exchanges.append(True)
        return {
            "access_token": refreshed_token,
            "token_type": "Bearer",
            "expires_in": 3600,
        }

    async with _server(provider_app) as provider_url:
        async with _server(create_app()) as api_url:
            async with _case(
                make_work,
                external=external,
                token_url=provider_url + "/{entity_id}/token",
            ) as case:
                monkeypatch.setattr(
                    ingress, "get_session_factory", lambda: case["work"].cohort.factory
                )
                for human in (False, True):
                    before = await _oauth_state(case, refreshed_token=refreshed_token)
                    count = len(exchanges)
                    mapping = await _sdk(case, api_url, "get_mapping", human=human)
                    _profile(mapping, case, "get_mapping", human=human)
                    _require(
                        await _oauth_state(case, refreshed_token=refreshed_token)
                        == before
                        and len(exchanges) == count,
                        "Mapping read performed OAuth recovery",
                    )
                    started = datetime.now(UTC)
                    payload = await _sdk(case, api_url, "get", human=human)
                    _profile(
                        payload,
                        {**case, "stored_token": refreshed_token},
                        "get",
                        human=human,
                    )
                    after = await _oauth_state(case, refreshed_token=refreshed_token)
                    if human and external:
                        _require(
                            after == before and len(exchanges) == count,
                            "Human external read changed global OAuth state",
                        )
                    else:
                        _require(
                            len(exchanges) == count + 1,
                            "Expected one finite synthetic OAuth exchange",
                        )
                        _require(
                            after["token_id"] == before["token_id"]
                            and after["token_ids"] == before["token_ids"]
                            and after["cipher_digest"] != before["cipher_digest"]
                            and after["refreshed_matches"]
                            and not after["stored_matches"],
                            "OAuth recovery did not update only the existing cascaded token",
                        )
                        _require(
                            after["provider_status"]
                            == after["token_status"]
                            == "completed"
                            and after["provider_message"] is None
                            and after["token_message"] is None
                            and after["provider_refresh"] >= started
                            and after["token_refresh"] >= started
                            and started + timedelta(minutes=59)
                            <= after["expires"]
                            <= datetime.now(UTC) + timedelta(hours=1)
                            and after["scopes"] == ("synthetic.read",),
                            "OAuth recovery state/timestamps/scopes were not committed",
                        )


async def test_actual_http_signed_oversized_reserved_denial_and_large_ordinary_positive(
    make_work, monkeypatch
):
    records = _observe_reads(monkeypatch)
    async with _server(create_app()) as api_url:
        async with _case(make_work) as case:
            monkeypatch.setattr(
                ingress, "get_session_factory", lambda: case["work"].cohort.factory
            )
            body = {
                "name": case["name"],
                "scope": str(case["caller"].effective_organization_id),
                "solution": str(case["work"].cohort.source.solution_install_id),
            }
            settings = get_settings()
            human = jwt.decode(
                case["human_token"],
                settings.secret_key,
                algorithms=[settings.algorithm],
                audience=settings.jwt_audience,
                issuer=settings.jwt_issuer,
            )
            human["synthetic_padding"] = "x" * 5000
            async with httpx.AsyncClient(base_url=api_url, trust_env=False) as client:
                large = jwt.encode(
                    human, settings.secret_key, algorithm=settings.algorithm
                )
                response = await client.post(
                    "/api/sdk/integrations/get",
                    json=body,
                    headers={"Authorization": f"Bearer {large}"},
                )
                assert response.status_code == 200
                _profile(response.json(), case, "get", human=True)
                for marker in ("purpose", "audience"):
                    claims = dict(human)
                    claims.update(
                        {"purpose": "workflow-runtime-sdk/v1"}
                        if marker == "purpose"
                        else {
                            "aud": [
                                settings.jwt_audience,
                                "bifrost-workflow-runtime-sdk",
                            ]
                        }
                    )
                    token = jwt.encode(
                        claims, settings.secret_key, algorithm=settings.algorithm
                    )
                    before = len(records)
                    denied = await client.post(
                        "/api/sdk/integrations/get",
                        json=body,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    assert denied.status_code == 401
                    assert denied.headers["www-authenticate"] == "Bearer"
                    _require(
                        denied.json() == {"detail": "Invalid runtime SDK credential"},
                        "Oversized marker denial leaked details",
                    )
                    assert len(records) == before


async def test_actual_http_dedicated_signature_header_claims_and_locations_deny(
    make_work, monkeypatch
):
    records = _observe_reads(monkeypatch)
    async with _server(create_app()) as api_url:
        async with _case(make_work) as case:
            work, bundle = case["work"], case["bundle"]
            monkeypatch.setattr(
                ingress, "get_session_factory", lambda: work.cohort.factory
            )
            body = {
                "name": case["name"],
                "scope": str(work.cohort.organization_id),
                "solution": str(work.cohort.source.solution_install_id),
            }
            claims = decode_runtime_sdk_access(bundle.access_token).model_dump()
            settings = get_settings()
            async with httpx.AsyncClient(base_url=api_url, trust_env=False) as client:
                for case_name in (
                    "signature",
                    "issuer",
                    "audience_array",
                    "purpose",
                    "type",
                    "header",
                    "expired",
                    "unknown_claim",
                ):
                    changed = dict(claims)
                    key, headers = settings.secret_key, None
                    if case_name == "signature":
                        key = "synthetic-untrusted-signing-key-at-least-32-bytes"
                    elif case_name == "issuer":
                        changed["iss"] = "synthetic-wrong-issuer"
                    elif case_name == "audience_array":
                        changed["aud"] = [claims["aud"]]
                    elif case_name == "purpose":
                        changed["purpose"] = "synthetic-wrong-purpose"
                    elif case_name == "type":
                        changed["type"] = "refresh"
                    elif case_name == "header":
                        headers = {"kid": "synthetic-key-id"}
                    elif case_name == "expired":
                        changed["exp"] = changed["iat"] - 1
                    else:
                        changed["synthetic_extra"] = True
                    token = jwt.encode(
                        changed, key, algorithm=settings.algorithm, headers=headers
                    )
                    denied = await client.post(
                        "/api/sdk/integrations/get",
                        json=body,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    assert denied.status_code == 401
                    _require(
                        denied.json() == {"detail": "Invalid runtime SDK credential"},
                        "Dedicated cryptographic/shape failure entered legacy",
                    )
                for headers in (
                    {
                        "Cookie": f"access_token={bundle.access_token}; csrf_token=synthetic-csrf",
                        "X-CSRF-Token": "synthetic-csrf",
                    },
                    {
                        "Authorization": f"Bearer {bundle.access_token}",
                        "Cookie": "embed_token=synthetic-ignored",
                    },
                    [
                        ("Authorization", f"Bearer {bundle.access_token}"),
                        ("Authorization", "Bearer synthetic-ignored"),
                    ],
                    {
                        "Authorization": "Bearer synthetic-invalid",
                        "Cookie": f"access_token={bundle.access_token}",
                    },
                ):
                    denied = await client.post(
                        "/api/sdk/integrations/get", json=body, headers=headers
                    )
                    assert denied.status_code == 401
                    assert denied.headers["www-authenticate"] == "Bearer"
                assert records == []
                prior_csrf = await client.post(
                    "/api/sdk/integrations/get",
                    json=body,
                    headers={"Cookie": f"access_token={bundle.access_token}"},
                )
                assert prior_csrf.status_code == 403
                _require(
                    prior_csrf.json() == {"detail": "CSRF token missing"},
                    "Existing pre-authentication CSRF precedence changed",
                )
                assert records == []


@pytest.mark.parametrize(
    "mutation", ["user", "organization", "lease", "terminal", "claim", "revocation"]
)
async def test_actual_http_committed_authority_loss_denies_before_repository_effect(
    make_work, monkeypatch, mutation
):
    records = _observe_reads(monkeypatch)
    async with _server(create_app()) as api_url:
        async with _case(make_work) as case:
            work, bundle = case["work"], case["bundle"]
            monkeypatch.setattr(
                ingress, "get_session_factory", lambda: work.cohort.factory
            )
            body = {
                "name": case["name"],
                "scope": str(work.cohort.organization_id),
                "solution": str(work.cohort.source.solution_install_id),
            }
            async with httpx.AsyncClient(base_url=api_url, trust_env=False) as client:
                admitted = await client.post(
                    "/api/sdk/integrations/get",
                    json=body,
                    headers={"Authorization": f"Bearer {bundle.access_token}"},
                )
                assert admitted.status_code == 200
                _profile(admitted.json(), case, "get")
                if mutation == "revocation":
                    await grants.revoke_workflow_runtime_sdk_grant(
                        work.cohort.factory,
                        grant_id=case["reference"].grant_id,
                        supervisor_incarnation_id=work.start.supervisor_incarnation_id,
                        runtime_session_id=work.start.runtime_session_id,
                        reason="session_closed",
                    )
                    model, row_id, original = None, None, None
                else:
                    model, row_id, values = {
                        "user": (
                            User,
                            work.caller.caller_user_id,
                            {"is_active": False},
                        ),
                        "organization": (
                            Organization,
                            work.cohort.organization_id,
                            {"is_active": False},
                        ),
                        "lease": (
                            WorkflowExecutionAttempt,
                            work.start.workflow_attempt_id,
                            {
                                "heartbeat_at": datetime.now(UTC)
                                - timedelta(seconds=120)
                            },
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
                        "claim": (
                            WorkflowExecutionAttempt,
                            work.start.workflow_attempt_id,
                            {"claim_token": uuid4()},
                        ),
                    }[mutation]
                    async with work.cohort.factory() as db, db.begin():
                        row = await db.get(model, row_id)
                        original = {key: getattr(row, key) for key in values}
                        for key, value in values.items():
                            setattr(row, key, value)
                try:
                    before = len(records)
                    for path, candidate in (
                        ("/api/sdk/integrations/get", body),
                        (
                            "/api/sdk/integrations/get_mapping",
                            {
                                "name": case["name"],
                                "scope": body["scope"],
                                "entity_id": None,
                            },
                        ),
                    ):
                        denied = await client.post(
                            path,
                            json=candidate,
                            headers={"Authorization": f"Bearer {bundle.access_token}"},
                        )
                        assert denied.status_code == 401
                        _require(
                            denied.json()
                            == {"detail": "Invalid runtime SDK credential"},
                            "Committed admission denial leaked details",
                        )
                    refresh = await client.post(
                        "/auth/refresh", json={"refresh_token": bundle.refresh_token}
                    )
                    assert refresh.status_code == 401
                    assert len(records) == before
                finally:
                    if model is not None:
                        async with work.cohort.factory() as db, db.begin():
                            await db.execute(
                                update(model)
                                .where(model.id == row_id)
                                .values(**original)
                            )


async def test_actual_http_new_session_cannot_replace_registered_attempt(
    make_work, monkeypatch
):
    async with _server(create_app()) as api_url:
        work = await make_work(timeout=0)
        reference, bundle = await _bundle(work)
        monkeypatch.setattr(ingress, "get_session_factory", lambda: work.cohort.factory)
        with pytest.raises(RuntimeSDKDenied):
            await work.provision(
                start=work.start.model_copy(update={"runtime_session_id": uuid4()})
            )
        changed_reference = GrantReference(
            grant_id=uuid4(), grant_digest=reference.grant_digest
        )
        changed = sign_runtime_sdk_token(
            changed_reference,
            issued_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(seconds=590),
        )
        async with httpx.AsyncClient(base_url=api_url, trust_env=False) as client:
            denied = await client.post(
                "/api/sdk/integrations/get",
                json={
                    "name": "Synthetic integration",
                    "scope": str(work.cohort.organization_id),
                    "solution": str(work.cohort.source.solution_install_id),
                },
                headers={"Authorization": f"Bearer {changed.access_token}"},
            )
            assert denied.status_code == 401
            admitted = await grants.load_runtime_sdk_ingress_authority(
                work.cohort.factory, token=bundle.access_token
            )
            assert admitted.snapshot.runtime_session_id == work.start.runtime_session_id


@pytest.mark.parametrize("status_code", [301, 302, 303, 307, 308])
async def test_unchanged_sdk_get_mapping_and_existing_refresh_do_not_deliver_redirect(
    make_work, monkeypatch, status_code
):
    """Actual client transport only; redirect endpoint does not certify auth."""
    work = await make_work(timeout=0)
    _, bundle = await _bundle(work)
    deliveries = []
    target = FastAPI()

    @target.api_route("/sink", methods=["GET", "POST"])
    async def sink(request: Request):
        deliveries.append(request.method)
        return {"unexpected": True}

    async with _server(target) as target_url:
        origin = FastAPI()
        origin_calls = []

        @origin.post("/api/sdk/integrations/get")
        @origin.post("/api/sdk/integrations/get_mapping")
        @origin.post("/auth/refresh")
        async def redirect(request: Request):
            body = await request.json()
            selected = (
                body.get("refresh_token")
                if request.url.path == "/auth/refresh"
                else request.headers.get("authorization", "").removeprefix("Bearer ")
            )
            expected = (
                bundle.refresh_token
                if request.url.path == "/auth/refresh"
                else bundle.access_token
            )
            _require(
                selected == expected,
                "Synthetic redirect origin did not receive selected credential",
            )
            origin_calls.append(request.url.path)
            return RedirectResponse(f"{target_url}/sink", status_code=status_code)

        async with _server(origin) as origin_url:
            case = {
                "caller": work.caller,
                "work": work,
                "name": "Synthetic integration",
                "bundle": bundle,
            }
            for operation in ("get", "get_mapping"):
                # The public SDK parser may reject an empty 3xx body. The
                # transport requirement is that the target receives nothing.
                try:
                    await _sdk(case, origin_url, operation)
                except AssertionError:
                    pass
                assert origin_calls[-1:] == [f"/api/sdk/integrations/{operation}"]
                assert deliveries == []
            monkeypatch.setenv("BIFROST_API_URL", origin_url)
            monkeypatch.setenv("BIFROST_ACCESS_TOKEN", bundle.access_token)
            monkeypatch.setenv("BIFROST_REFRESH_TOKEN", bundle.refresh_token)
            _reset_refresh_coordinators_for_tests()
            try:
                renewed = await refresh_connection_access_token(
                    origin_url, bundle.access_token
                )
                _require(renewed is None, "SDK accepted a redirect as renewal")
                assert origin_calls == [
                    "/api/sdk/integrations/get",
                    "/api/sdk/integrations/get_mapping",
                    "/auth/refresh",
                ]
                assert deliveries == []
            finally:
                _reset_refresh_coordinators_for_tests()


async def test_actual_sdk_expired_dedicated_refresh_reuses_same_grant(
    make_work, monkeypatch
):
    records = _observe_reads(monkeypatch)
    async with _server(create_app()) as api_url:
        async with _case(make_work) as case:
            work = case["work"]
            monkeypatch.setattr(
                ingress, "get_session_factory", lambda: work.cohort.factory
            )
            issued = datetime.now(UTC) - timedelta(seconds=2)
            # A renewal may be expired, but it may not predate registration.
            # Keep original iat and make the access window expire after it.
            initial = decode_runtime_sdk_access(case["bundle"].access_token)
            settings = get_settings()
            signed = jwt.decode(
                case["bundle"].access_token,
                settings.secret_key,
                algorithms=[settings.algorithm],
                issuer=settings.jwt_issuer,
                audience="bifrost-workflow-runtime-sdk",
            )
            signed["exp"] = initial.iat + 1
            expired = jwt.encode(
                signed, settings.secret_key, algorithm=settings.algorithm
            )
            while datetime.now(UTC).timestamp() < signed["exp"]:
                await asyncio.sleep(0.01)
            _require(datetime.now(UTC) > issued, "Synthetic expiry clock failed")
            monkeypatch.setenv("BIFROST_API_URL", api_url)
            monkeypatch.setenv("BIFROST_ACCESS_TOKEN", expired)
            monkeypatch.setenv("BIFROST_REFRESH_TOKEN", expired)
            _reset_refresh_coordinators_for_tests()
            client = BifrostClient(api_url=api_url, access_token=expired)
            try:
                response = await client.post(
                    "/api/sdk/integrations/get",
                    json={
                        "name": case["name"],
                        "scope": str(work.cohort.organization_id),
                        "solution": str(work.cohort.source.solution_install_id),
                    },
                )
                assert response.status_code == 200
                _profile(response.json(), case, "get")
                after = decode_runtime_sdk_access(client._access_token)
                assert (after.sub, after.grant_digest) == (
                    initial.sub,
                    initial.grant_digest,
                )
                assert after.exp - after.iat == 600
                assert records[-1][0].user_id == UUID(ENGINE_USER_ID)
            finally:
                await client.close()
                _reset_refresh_coordinators_for_tests()
