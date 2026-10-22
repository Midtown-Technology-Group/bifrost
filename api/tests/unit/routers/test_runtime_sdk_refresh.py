"""Dedicated renewal through real committed admission, Redis and auth router.

Failure injection isolates effect ordering after real admission. It does not
replace registration/current-caller reads with an always-live grant loader.
"""

from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.core import rate_limit
from src.core import runtime_sdk_ingress as ingress
from src.core.cache import get_shared_redis
from src.core.cache.keys import rate_limit_key
from src.core.runtime_sdk_credentials import RuntimeSDKDenied, decode_runtime_sdk_access
from src.core.security import create_refresh_token, decode_token, mint_engine_token
from src.routers import auth
from src.services import runtime_sdk_grants as grants
from tests.unit.core.test_runtime_sdk_ingress import _ordinary, _request, _require
from tests.unit.services import test_runtime_sdk_grants as foundation

credential_cohort = foundation.credential_cohort
make_work = foundation.make_work
_bundle = foundation._bundle


def _auth_app():
    app = FastAPI()
    app.include_router(auth.router)
    return app


async def test_renewal_admission_never_signs_or_returns_bundle(make_work, monkeypatch):
    work = await make_work(timeout=0)
    reference, bundle = await _bundle(work)

    def forbidden_sign(*args, **kwargs):
        raise AssertionError("Admission attempted signing")

    monkeypatch.setattr(grants, "sign_runtime_sdk_token", forbidden_sign)
    authority = await grants.admit_runtime_sdk_renewal(
        work.cohort.factory, token=bundle.refresh_token
    )
    assert authority.snapshot.id == reference.grant_id
    assert authority.snapshot.runtime_session_id == work.start.runtime_session_id
    assert not hasattr(authority, "access_token")


async def test_refresh_db_close_then_trusted_limiter_then_fresh_same_grant_signing(
    make_work, monkeypatch
):
    work = await make_work(timeout=0)
    reference, bundle = await _bundle(work)
    events = []

    class ObservedSession(AsyncSession):
        async def rollback(self):
            await super().rollback()
            events.append("rollback")

        async def close(self):
            await super().close()
            events.append("close")

    factory = async_sessionmaker(
        work.cohort.factory.kw["bind"], class_=ObservedSession, expire_on_commit=False
    )
    monkeypatch.setattr(ingress, "get_session_factory", lambda: factory)
    limiter_done = None

    async def limiter(endpoint, identifier):
        nonlocal limiter_done
        assert events == ["rollback", "close"]
        assert endpoint == "runtime_sdk_refresh"
        assert identifier == f"{reference.grant_id}:{work.start.workflow_attempt_id}"
        events.append("limiter")
        limiter_done = datetime.now(UTC)

    actual_sign = ingress.sign_runtime_sdk_token

    def signer(selected, *, issued_at, expires_at):
        assert events == ["rollback", "close", "limiter"]
        assert selected == reference
        assert issued_at >= limiter_done
        assert expires_at - issued_at == timedelta(seconds=600)
        events.append("sign")
        return actual_sign(selected, issued_at=issued_at, expires_at=expires_at)

    monkeypatch.setattr(ingress.auth_limiter, "check", limiter)
    monkeypatch.setattr(ingress, "sign_runtime_sdk_token", signer)
    renewed = await ingress.refresh_runtime_sdk_credential(
        _request(), token=bundle.refresh_token
    )
    before, after = (
        decode_runtime_sdk_access(bundle.access_token),
        decode_runtime_sdk_access(renewed.access_token),
    )
    assert (after.sub, after.grant_digest, after.aud, after.purpose, after.type) == (
        before.sub,
        before.grant_digest,
        before.aud,
        before.purpose,
        "access",
    )
    assert after.exp - after.iat == 600
    assert after.jti != before.jti
    _require(
        renewed.access_token == renewed.refresh_token,
        "Renewal response split dedicated credentials",
    )
    assert events == ["rollback", "close", "limiter", "sign"]


@pytest.mark.parametrize("condition", ["finite", "revoked", "invalid"])
async def test_ineligible_renewal_uses_ip_limiter_and_never_signs(
    make_work, monkeypatch, condition
):
    work = await make_work(timeout=30 if condition == "finite" else 0)
    reference, bundle = await _bundle(work)
    if condition == "revoked":
        await grants.revoke_workflow_runtime_sdk_grant(
            work.cohort.factory,
            grant_id=reference.grant_id,
            supervisor_incarnation_id=work.start.supervisor_incarnation_id,
            runtime_session_id=work.start.runtime_session_id,
            reason="session_closed",
        )
    monkeypatch.setattr(ingress, "get_session_factory", lambda: work.cohort.factory)
    calls = []

    async def limiter(endpoint, identifier):
        calls.append((endpoint, identifier))

    def forbidden_sign(*args, **kwargs):
        raise AssertionError("Ineligible renewal signed")

    monkeypatch.setattr(ingress.auth_limiter, "check", limiter)
    monkeypatch.setattr(ingress, "sign_runtime_sdk_token", forbidden_sign)
    with pytest.raises(HTTPException) as error:
        await ingress.refresh_runtime_sdk_credential(
            _request(),
            token="synthetic-invalid"
            if condition == "invalid"
            else bundle.refresh_token,
        )
    assert error.value.status_code == 401
    assert error.value.detail == "Invalid runtime SDK credential"
    assert error.value.headers == {"WWW-Authenticate": "Bearer"}
    assert calls == [("refresh", "127.0.0.1")]


async def test_real_redis_grant_limiter_ten_per_minute_and_429_never_signs(
    make_work, monkeypatch
):
    work = await make_work(timeout=0)
    reference, bundle = await _bundle(work)
    monkeypatch.setattr(ingress, "get_session_factory", lambda: work.cohort.factory)
    actual_check, actual_sign = (
        ingress.auth_limiter.check,
        ingress.sign_runtime_sdk_token,
    )
    identifier = f"{reference.grant_id}:{work.start.workflow_attempt_id}"
    redis = await get_shared_redis()
    key = rate_limit_key("runtime_sdk_refresh", identifier)
    hits = f"bifrost:rate_limit_hits:{identifier}"
    signed = []

    async def forced_existing_limiter(endpoint, identity):
        assert (endpoint, identity) == ("runtime_sdk_refresh", identifier)
        await actual_check(endpoint, identity, force=True)

    def signer(*args, **kwargs):
        signed.append(True)
        return actual_sign(*args, **kwargs)

    monkeypatch.setattr(ingress.auth_limiter, "check", forced_existing_limiter)
    monkeypatch.setattr(ingress, "sign_runtime_sdk_token", signer)
    await redis.delete(key, hits)
    try:
        for _ in range(10):
            await ingress.refresh_runtime_sdk_credential(
                _request(), token=bundle.refresh_token
            )
        with pytest.raises(HTTPException) as error:
            await ingress.refresh_runtime_sdk_credential(
                _request(), token=bundle.refresh_token
            )
        assert error.value.status_code == 429
        assert "Retry-After" in error.value.headers
        assert len(signed) == 10
        assert int(await redis.get(key)) == 11
        assert 0 < await redis.ttl(key) <= 60
    finally:
        await redis.delete(key, hits)


async def test_real_admission_redis_outage_is_not_relabelled_as_policy_401(
    make_work, monkeypatch
):
    work = await make_work(timeout=0)
    _, bundle = await _bundle(work)
    monkeypatch.setattr(ingress, "get_session_factory", lambda: work.cohort.factory)
    actual_check = ingress.auth_limiter.check

    async def forced_existing_limiter(endpoint, identifier):
        await actual_check(endpoint, identifier, force=True)

    async def unavailable_redis():
        raise ConnectionError("synthetic Redis unavailable")

    def forbidden_sign(*args, **kwargs):
        raise AssertionError("Redis outage signed")

    monkeypatch.setattr(ingress.auth_limiter, "check", forced_existing_limiter)
    monkeypatch.setattr(rate_limit, "get_shared_redis", unavailable_redis)
    monkeypatch.setattr(ingress, "sign_runtime_sdk_token", forbidden_sign)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_auth_app(), raise_app_exceptions=False),
        base_url="http://synthetic.invalid",
    ) as client:
        response = await client.post(
            "/auth/refresh", json={"refresh_token": bundle.refresh_token}
        )
    assert response.status_code == 500


async def test_router_dedicated_body_only_closed_keys_and_no_cookies(
    make_work, monkeypatch
):
    work = await make_work(timeout=0)
    _, bundle = await _bundle(work)
    monkeypatch.setattr(ingress, "get_session_factory", lambda: work.cohort.factory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_auth_app()),
        base_url="http://synthetic.invalid",
    ) as client:
        response = await client.post(
            "/auth/refresh", json={"refresh_token": bundle.refresh_token}
        )
        assert response.status_code == 200
        assert "set-cookie" not in response.headers
        _require(
            set(response.json()) == {"access_token", "refresh_token", "token_type"},
            "Dedicated response contract changed",
        )
        for body, headers in (
            ({"refresh_token": bundle.refresh_token, "extra": True}, {}),
            (
                {"refresh_token": bundle.refresh_token},
                {"Authorization": "Bearer synthetic-ignored"},
            ),
            (
                {"refresh_token": bundle.refresh_token},
                {"Cookie": "access_token=synthetic-ignored"},
            ),
        ):
            denied = await client.post("/auth/refresh", json=body, headers=headers)
            assert denied.status_code == 401
            _require(
                denied.json() == {"detail": "Invalid runtime SDK credential"},
                "Dedicated denial leaked details",
            )


async def test_cookie_only_dedicated_refresh_without_json_denies_opaquely(
    make_work, monkeypatch
):
    work = await make_work(timeout=0)
    _, bundle = await _bundle(work)
    monkeypatch.setattr(ingress, "get_session_factory", lambda: work.cohort.factory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_auth_app()),
        base_url="http://synthetic.invalid",
    ) as client:
        response = await client.post(
            "/auth/refresh", headers={"Cookie": f"refresh_token={bundle.refresh_token}"}
        )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    _require(
        response.json() == {"detail": "Invalid runtime SDK credential"},
        "Cookie-only dedicated renewal parsed absent JSON",
    )


@pytest.mark.parametrize("marker", ["purpose", "audience"])
async def test_router_signed_oversized_reserved_refresh_denies_before_human_rotation(
    marker,
):
    token = _ordinary(kind="refresh", reserved=marker, large=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_auth_app()),
        base_url="http://synthetic.invalid",
    ) as client:
        response = await client.post("/auth/refresh", json={"refresh_token": token})
    assert response.status_code == 401
    _require(
        response.json() == {"detail": "Invalid runtime SDK credential"},
        "Oversized reserved refresh reached legacy rotation",
    )
    assert "set-cookie" not in response.headers


async def test_large_ordinary_refresh_body_precedence_and_human_rotation_unchanged(
    make_work,
):
    work = await make_work()
    _, dedicated = await _bundle(work)
    token, jti = create_refresh_token(
        {"sub": str(work.caller.caller_user_id), "synthetic_padding": "x" * 5000}
    )
    _require(len(token) > 4096, "Ordinary refresh positive is not oversized")
    await auth.store_refresh_token_jti(str(work.caller.caller_user_id), jti)
    rotated = None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_auth_app()),
            base_url="http://synthetic.invalid",
        ) as client:
            response = await client.post(
                "/auth/refresh",
                json={"refresh_token": token, "legacy_extra": True},
                headers={
                    "Authorization": f"Bearer {dedicated.access_token}",
                    "Cookie": f"refresh_token={dedicated.refresh_token}",
                },
            )
        assert response.status_code == 200
        assert "set-cookie" in response.headers
        access = decode_token(response.json()["access_token"], expected_type="access")
        rotated = decode_token(
            response.json()["refresh_token"], expected_type="refresh"
        )
        _require(
            access is not None
            and access.get("sub") == str(work.caller.caller_user_id)
            and not access.get("engine"),
            "Ordinary renewal changed human principal",
        )
        _require(
            rotated is not None and rotated.get("jti") != jti,
            "Ordinary refresh did not rotate",
        )
    finally:
        await auth.validate_and_revoke_refresh_token_jti(
            str(work.caller.caller_user_id), jti
        )
        if rotated is not None:
            await auth.validate_and_revoke_refresh_token_jti(
                str(work.caller.caller_user_id), rotated["jti"]
            )


async def test_legacy_expired_renewable_engine_route_and_large_unreserved_backstop(
    make_work,
):
    work = await make_work(timeout=0)
    original, _ = mint_engine_token(
        execution_id=str(work.start.execution_id),
        attempt_token=str(work.start.private_claim_token),
        solution_id=str(work.cohort.source.solution_install_id),
        organization_id=str(work.cohort.organization_id),
        timeout_seconds=0,
    )
    settings = get_settings()
    claims = jwt.decode(
        original,
        settings.secret_key,
        algorithms=[settings.algorithm],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )
    claims.update(
        exp=int((datetime.now(UTC) - timedelta(minutes=1)).timestamp()),
        synthetic_padding="x" * 5000,
    )
    expired = jwt.encode(claims, settings.secret_key, algorithm=settings.algorithm)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_auth_app()),
        base_url="http://synthetic.invalid",
    ) as client:
        response = await client.post(
            "/auth/refresh",
            json={"refresh_token": expired},
            headers={
                "Authorization": "Bearer synthetic-ignored",
                "Cookie": "refresh_token=synthetic-ignored",
            },
        )
        assert response.status_code == 200
        _require(
            "set-cookie" not in response.headers,
            "Legacy Engine renewal installed human cookies",
        )
        claims["purpose"] = "workflow-runtime-sdk/v1"
        reserved = jwt.encode(claims, settings.secret_key, algorithm=settings.algorithm)
        denied = await client.post("/auth/refresh", json={"refresh_token": reserved})
        assert denied.status_code == 401
        _require(
            denied.json() == {"detail": "Invalid runtime SDK credential"},
            "Reserved expired Engine token entered legacy branch",
        )


async def test_finite_admission_denies_even_while_private_token_is_valid(make_work):
    work = await make_work(timeout=30)
    _, bundle = await _bundle(work)
    with pytest.raises(RuntimeSDKDenied):
        await grants.admit_runtime_sdk_renewal(
            work.cohort.factory, token=bundle.refresh_token
        )
