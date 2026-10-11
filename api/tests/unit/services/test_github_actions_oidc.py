from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from src.services import github_actions_oidc
from src.services.github_actions_oidc import (
    GITHUB_ACTIONS_ISSUER,
    GITHUB_ACTIONS_JWKS_MAX_BYTES,
    GITHUB_ACTIONS_JWKS_MAX_KEYS,
    GITHUB_ACTIONS_JWKS_UNKNOWN_KID_REFRESH_SECONDS,
    GITHUB_ACTIONS_OIDC_MAX_TOKEN_LENGTH,
    WORKSPACE_SOURCE_RELEASE_AUDIENCE,
    GitHubActionsOIDCError,
    authenticate_workspace_source_release_producer,
    workspace_source_release_producer_configured,
    workspace_source_release_tracking_expected,
    workspace_source_release_tracking_organization_id,
)

REPOSITORY = "MTG-Thomas/bifrost-workspace"
WORKFLOW_REF = (
    "MTG-Thomas/bifrost-workspace/.github/workflows/"
    "declare-workspace-source-release.yml@refs/heads/main"
)
ORGANIZATION_ID = "9f65129e-0e8a-4a3b-a56a-27d831bd7ab1"
SOURCE_SHA = "a" * 40
PRODUCER_SHA = "b" * 40
TRIGGERING_RUN_ID = "987654"
TRIGGERING_RUN_ATTEMPT = 3
DECLARATION_DIGEST = "c" * 64
WORKFLOW_RUN_AUDIENCE = (
    f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_run:{TRIGGERING_RUN_ID}:{SOURCE_SHA}"
)
WORKFLOW_DISPATCH_AUDIENCE = (
    f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_dispatch:"
    f"{TRIGGERING_RUN_ID}:{TRIGGERING_RUN_ATTEMPT}:{SOURCE_SHA}:"
    f"{DECLARATION_DIGEST}"
)


def _settings():
    return SimpleNamespace(
        workspace_source_release_oidc_repository=REPOSITORY,
        workspace_source_release_oidc_repository_id=1197464564,
        workspace_source_release_oidc_repository_owner_id=87775189,
        workspace_source_release_oidc_workflow_ref=WORKFLOW_REF,
        workspace_source_release_oidc_organization_id=ORGANIZATION_ID,
    )


def test_configuration_state_distinguishes_absent_partial_and_ready() -> None:
    assert workspace_source_release_tracking_expected(SimpleNamespace()) is False
    assert workspace_source_release_producer_configured(SimpleNamespace()) is False

    partial = SimpleNamespace(
        workspace_source_release_oidc_repository=REPOSITORY,
    )
    assert workspace_source_release_tracking_expected(partial) is True
    assert workspace_source_release_producer_configured(partial) is False

    assert workspace_source_release_tracking_expected(_settings()) is True
    assert workspace_source_release_producer_configured(_settings()) is True


def test_tracking_organization_comes_from_the_pinned_producer_configuration() -> None:
    assert str(workspace_source_release_tracking_organization_id(_settings())) == (
        ORGANIZATION_ID
    )
    settings = SimpleNamespace(workspace_source_release_oidc_organization_id=None)
    assert workspace_source_release_tracking_organization_id(settings) is None


def test_tracking_organization_rejects_an_invalid_uuid() -> None:
    settings = SimpleNamespace(
        workspace_source_release_oidc_organization_id="not-a-uuid"
    )
    with pytest.raises(GitHubActionsOIDCError, match="must be a UUID"):
        workspace_source_release_tracking_organization_id(settings)


def _token(
    *,
    overrides: dict[str, object] | None = None,
    kid: str = "test-key",
):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    claims: dict[str, object] = {
        "aud": WORKSPACE_SOURCE_RELEASE_AUDIENCE,
        "exp": now + timedelta(minutes=5),
        "iat": now,
        "iss": GITHUB_ACTIONS_ISSUER,
        "jti": "test-jti",
        "nbf": now - timedelta(seconds=5),
        "repository": REPOSITORY,
        "repository_id": "1197464564",
        "repository_owner_id": "87775189",
        "ref": "refs/heads/main",
        "ref_type": "branch",
        "event_name": "push",
        "sha": SOURCE_SHA,
        "workflow_ref": WORKFLOW_REF,
        "run_id": "123456",
        "actor": "MTG-Thomas",
        "actor_id": "87775189",
    }
    claims.update(overrides or {})
    token = jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": kid},
    )
    public_key = private_key.public_key()
    jwks = {
        "keys": [
            jwt.algorithms.RSAAlgorithm.to_jwk(public_key, as_dict=True)
            | {"kid": kid, "alg": "RS256", "use": "sig"}
        ]
    }
    return token, jwks


@pytest.mark.asyncio
async def test_accepts_exact_protected_main_push_identity() -> None:
    token, jwks = _token()

    producer = await authenticate_workspace_source_release_producer(
        token,
        settings=_settings(),
        jwks=jwks,
    )

    assert str(producer.organization_id) == ORGANIZATION_ID
    assert producer.source_commit_sha == SOURCE_SHA
    assert producer.oidc_commit_sha == SOURCE_SHA
    assert producer.repository == REPOSITORY
    assert producer.workflow_ref == WORKFLOW_REF
    assert producer.run_id == "123456"
    assert producer.event_name == "push"
    assert producer.triggering_workflow_run_id is None
    assert producer.actor == "MTG-Thomas"
    assert producer.actor_id == "87775189"


@pytest.mark.asyncio
async def test_accepts_workflow_run_bound_to_triggering_ci_head_sha() -> None:
    token, jwks = _token(
        overrides={
            "aud": WORKFLOW_RUN_AUDIENCE,
            "event_name": "workflow_run",
            "sha": PRODUCER_SHA,
        }
    )

    producer = await authenticate_workspace_source_release_producer(
        token,
        settings=_settings(),
        jwks=jwks,
    )

    assert producer.source_commit_sha == SOURCE_SHA
    assert producer.oidc_commit_sha == PRODUCER_SHA
    assert producer.workflow_ref == WORKFLOW_REF
    assert producer.event_name == "workflow_run"
    assert producer.triggering_workflow_run_id == TRIGGERING_RUN_ID
    assert producer.actor == "MTG-Thomas"
    assert producer.actor_id == "87775189"


@pytest.mark.asyncio
async def test_accepts_workflow_dispatch_bound_to_successful_ci_head_sha() -> None:
    token, jwks = _token(
        overrides={
            "aud": WORKFLOW_DISPATCH_AUDIENCE,
            "event_name": "workflow_dispatch",
            "sha": PRODUCER_SHA,
            "actor": "github-actions[bot]",
            "actor_id": "41898282",
        }
    )

    producer = await authenticate_workspace_source_release_producer(
        token,
        settings=_settings(),
        jwks=jwks,
    )

    assert producer.source_commit_sha == SOURCE_SHA
    assert producer.oidc_commit_sha == PRODUCER_SHA
    assert producer.event_name == "workflow_dispatch"
    assert producer.triggering_workflow_run_id == TRIGGERING_RUN_ID
    assert producer.triggering_workflow_run_attempt == TRIGGERING_RUN_ATTEMPT
    assert producer.declaration_digest == DECLARATION_DIGEST
    assert producer.actor == "github-actions[bot]"
    assert producer.actor_id == "41898282"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"actor": None, "actor_id": None},
        {"actor": None},
        {"actor_id": None},
        {"actor": "MTG-Thomas", "actor_id": "87775189"},
        {"actor": "github-actions[bot]", "actor_id": "87775189"},
    ],
)
async def test_rejects_workflow_dispatch_without_exact_bot_actor(
    overrides: dict[str, object],
) -> None:
    token, jwks = _token(
        overrides={
            "aud": WORKFLOW_DISPATCH_AUDIENCE,
            "event_name": "workflow_dispatch",
            "sha": PRODUCER_SHA,
        }
        | overrides
    )

    with pytest.raises(GitHubActionsOIDCError, match="actor"):
        await authenticate_workspace_source_release_producer(
            token,
            settings=_settings(),
            jwks=jwks,
        )


@pytest.mark.asyncio
async def test_rejects_workflow_run_with_unbound_fixed_audience() -> None:
    token, jwks = _token(overrides={"event_name": "workflow_run"})

    with pytest.raises(GitHubActionsOIDCError, match="exact-bound"):
        await authenticate_workspace_source_release_producer(
            token,
            settings=_settings(),
            jwks=jwks,
        )


@pytest.mark.asyncio
async def test_rejects_push_with_workflow_run_audience() -> None:
    token, jwks = _token(overrides={"aud": WORKFLOW_RUN_AUDIENCE})

    with pytest.raises(GitHubActionsOIDCError, match="push producer"):
        await authenticate_workspace_source_release_producer(
            token,
            settings=_settings(),
            jwks=jwks,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_name", "audience"),
    [
        ("push", WORKFLOW_DISPATCH_AUDIENCE),
        ("workflow_run", WORKFLOW_DISPATCH_AUDIENCE),
        ("workflow_dispatch", WORKSPACE_SOURCE_RELEASE_AUDIENCE),
        ("workflow_dispatch", WORKFLOW_RUN_AUDIENCE),
    ],
)
async def test_rejects_event_and_audience_crossovers(
    event_name: str, audience: str
) -> None:
    token, jwks = _token(overrides={"aud": audience, "event_name": event_name})

    with pytest.raises(GitHubActionsOIDCError, match=f"{event_name} producer"):
        await authenticate_workspace_source_release_producer(
            token,
            settings=_settings(),
            jwks=jwks,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "audience",
    [
        f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_run:0:{SOURCE_SHA}",
        f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_run:123:{SOURCE_SHA.upper()}",
        f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_run:123",
    ],
)
async def test_rejects_malformed_workflow_run_audience(audience: str) -> None:
    token, jwks = _token(overrides={"aud": audience, "event_name": "workflow_run"})

    with pytest.raises(GitHubActionsOIDCError, match="invalid source-release audience"):
        await authenticate_workspace_source_release_producer(
            token,
            settings=_settings(),
            jwks=jwks,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "audience",
    [
        f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_dispatch:0:1:{SOURCE_SHA}",
        f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_dispatch:123:0:{SOURCE_SHA}",
        f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_dispatch:123:1:{SOURCE_SHA.upper()}:{DECLARATION_DIGEST}",
        f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_dispatch:123:1:{SOURCE_SHA}",
        f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_dispatch:123:1:{SOURCE_SHA}:BAD",
        f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_dispatch:123:{SOURCE_SHA}",
        f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_dispatch:123",
    ],
)
async def test_rejects_malformed_workflow_dispatch_audience(audience: str) -> None:
    token, jwks = _token(overrides={"aud": audience, "event_name": "workflow_dispatch"})

    with pytest.raises(GitHubActionsOIDCError, match="invalid source-release audience"):
        await authenticate_workspace_source_release_producer(
            token,
            settings=_settings(),
            jwks=jwks,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("repository_id", "999"),
        ("repository_owner_id", "999"),
        ("ref", "refs/heads/feature"),
        ("event_name", "pull_request"),
        ("workflow_ref", f"{REPOSITORY}/.github/workflows/other.yml@refs/heads/main"),
    ],
)
async def test_rejects_identity_outside_pinned_policy(claim: str, value: str) -> None:
    token, jwks = _token(overrides={claim: value})

    with pytest.raises(GitHubActionsOIDCError, match=claim):
        await authenticate_workspace_source_release_producer(
            token,
            settings=_settings(),
            jwks=jwks,
        )


@pytest.mark.asyncio
async def test_rejects_wrong_audience() -> None:
    token, jwks = _token(overrides={"aud": "not-bifrost"})

    with pytest.raises(GitHubActionsOIDCError, match="invalid source-release audience"):
        await authenticate_workspace_source_release_producer(
            token,
            settings=_settings(),
            jwks=jwks,
        )


@pytest.mark.asyncio
async def test_rejects_oversized_token_before_jwt_parsing(monkeypatch) -> None:
    parse_header = MagicMock(side_effect=AssertionError("must not parse oversized token"))
    monkeypatch.setattr(jwt, "get_unverified_header", parse_header)

    with pytest.raises(GitHubActionsOIDCError, match="too large"):
        await authenticate_workspace_source_release_producer(
            "x" * (GITHUB_ACTIONS_OIDC_MAX_TOKEN_LENGTH + 1),
            settings=_settings(),
            jwks={"keys": []},
        )

    parse_header.assert_not_called()


@pytest.mark.asyncio
async def test_rejects_oversized_key_identifier_before_jwks_access() -> None:
    token, _ = _token(kid="k" * 257)

    with pytest.raises(GitHubActionsOIDCError, match="key identifier"):
        await authenticate_workspace_source_release_producer(
            token,
            settings=_settings(),
            jwks={"keys": []},
        )


@pytest.mark.asyncio
async def test_concurrent_unknown_kids_share_one_bounded_refresh(monkeypatch) -> None:
    _, known_jwks = _token(kid="known-key")
    fetch_jwks = AsyncMock(return_value=known_jwks)
    monkeypatch.setattr(
        github_actions_oidc,
        "_fetch_github_actions_jwks",
        fetch_jwks,
    )
    cache = github_actions_oidc._GitHubActionsJWKSCache()
    monkeypatch.setattr(
        github_actions_oidc,
        "_github_actions_jwks_cache",
        cache,
    )

    await cache.get()
    cache._last_attempt_at -= GITHUB_ACTIONS_JWKS_UNKNOWN_KID_REFRESH_SECONDS + 1
    tokens = [_token(kid=f"unknown-{index}")[0] for index in range(12)]

    results = await asyncio.gather(
        *(
            authenticate_workspace_source_release_producer(
                token,
                settings=_settings(),
            )
            for token in tokens
        ),
        return_exceptions=True,
    )

    assert all(isinstance(result, GitHubActionsOIDCError) for result in results)
    assert all("not recognized" in str(result) for result in results)
    assert fetch_jwks.await_count == 2


@pytest.mark.asyncio
async def test_jwks_failure_backoff_coalesces_concurrent_fetches(monkeypatch) -> None:
    fetch_jwks = AsyncMock(
        side_effect=GitHubActionsOIDCError(
            "GitHub Actions OIDC signing keys are unavailable"
        )
    )
    monkeypatch.setattr(
        github_actions_oidc,
        "_fetch_github_actions_jwks",
        fetch_jwks,
    )
    cache = github_actions_oidc._GitHubActionsJWKSCache()

    results = await asyncio.gather(
        *(cache.get() for _ in range(12)),
        return_exceptions=True,
    )

    assert all(isinstance(result, GitHubActionsOIDCError) for result in results)
    assert fetch_jwks.await_count == 1


@pytest.mark.asyncio
async def test_fetch_jwks_accepts_a_bounded_key_document(monkeypatch) -> None:
    expected = {"keys": []}

    async def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=expected)

    original_client = httpx.AsyncClient

    def client_with_mock_transport(**kwargs):
        return original_client(
            **kwargs,
            transport=httpx.MockTransport(respond),
        )

    monkeypatch.setattr(
        github_actions_oidc.httpx,
        "AsyncClient",
        client_with_mock_transport,
    )

    assert await github_actions_oidc._fetch_github_actions_jwks() == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"x" * (GITHUB_ACTIONS_JWKS_MAX_BYTES + 1), "too large"),
        (b'{"keys": {}}', "invalid"),
        (
            (
                '{"keys":['
                + ",".join("{}" for _ in range(GITHUB_ACTIONS_JWKS_MAX_KEYS + 1))
                + "]}"
            ).encode(),
            "invalid",
        ),
    ],
    ids=["oversized", "wrong-key-shape", "too-many-keys"],
)
async def test_fetch_jwks_rejects_unbounded_or_invalid_documents(
    monkeypatch,
    body: bytes,
    message: str,
) -> None:
    async def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    original_client = httpx.AsyncClient

    def client_with_mock_transport(**kwargs):
        return original_client(
            **kwargs,
            transport=httpx.MockTransport(respond),
        )

    monkeypatch.setattr(
        github_actions_oidc.httpx,
        "AsyncClient",
        client_with_mock_transport,
    )

    with pytest.raises(GitHubActionsOIDCError, match=message):
        await github_actions_oidc._fetch_github_actions_jwks()


@pytest.mark.asyncio
async def test_jwks_total_deadline_releases_waiters_and_starts_backoff(
    monkeypatch,
) -> None:
    started = asyncio.Event()
    request_count = 0

    class ProgressingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            yield b'{"keys":['
            while True:
                await asyncio.sleep(0.01)
                yield b" "

    async def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, stream=ProgressingStream())

    original_client = httpx.AsyncClient

    def client_with_mock_transport(**kwargs):
        return original_client(
            **kwargs,
            transport=httpx.MockTransport(respond),
        )

    monkeypatch.setattr(
        github_actions_oidc,
        "GITHUB_ACTIONS_JWKS_FETCH_TIMEOUT_SECONDS",
        0.05,
    )
    monkeypatch.setattr(
        github_actions_oidc.httpx,
        "AsyncClient",
        client_with_mock_transport,
    )
    cache = github_actions_oidc._GitHubActionsJWKSCache()

    owner = asyncio.create_task(cache.get())
    await started.wait()
    waiter = asyncio.create_task(cache.get())
    results = await asyncio.gather(owner, waiter, return_exceptions=True)

    assert all(isinstance(result, GitHubActionsOIDCError) for result in results)
    assert all("unavailable" in str(result) for result in results)
    assert request_count == 1
    assert cache._lock.locked() is False

    with pytest.raises(GitHubActionsOIDCError, match="unavailable"):
        await cache.get()
    assert request_count == 1
