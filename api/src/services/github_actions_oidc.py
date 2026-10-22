"""Narrow GitHub Actions OIDC authentication for Workspace merge declarations."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import jwt

from src.config import Settings

GITHUB_ACTIONS_ISSUER = "https://token.actions.githubusercontent.com"
GITHUB_ACTIONS_JWKS_URL = f"{GITHUB_ACTIONS_ISSUER}/.well-known/jwks"
WORKSPACE_SOURCE_RELEASE_AUDIENCE = "bifrost-workspace-source-release"
GITHUB_ACTIONS_BOT_ACTOR_ID = 41898282
GITHUB_ACTIONS_OIDC_MAX_TOKEN_LENGTH = 16_384
GITHUB_ACTIONS_OIDC_MAX_KID_LENGTH = 256
GITHUB_ACTIONS_JWKS_MAX_BYTES = 262_144
GITHUB_ACTIONS_JWKS_MAX_KEYS = 32
GITHUB_ACTIONS_JWKS_FETCH_TIMEOUT_SECONDS = 3.0
GITHUB_ACTIONS_JWKS_TTL_SECONDS = 21_600
GITHUB_ACTIONS_JWKS_UNKNOWN_KID_REFRESH_SECONDS = 60
GITHUB_ACTIONS_JWKS_FAILURE_BACKOFF_SECONDS = 5
WORKSPACE_SOURCE_RELEASE_EVENTS = frozenset(
    {"push", "workflow_run", "workflow_dispatch"}
)
_WORKFLOW_RUN_AUDIENCE = re.compile(
    rf"^{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_run:"
    r"(?P<run_id>[1-9][0-9]*):(?P<source_sha>[0-9a-f]{40})$"
)
_WORKFLOW_DISPATCH_AUDIENCE = re.compile(
    rf"^{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_dispatch:"
    r"(?P<run_id>[1-9][0-9]*):(?P<run_attempt>[1-9][0-9]*):"
    r"(?P<source_sha>[0-9a-f]{40}):(?P<declaration_digest>[0-9a-f]{64})$"
)
_WORKSPACE_SOURCE_RELEASE_SETTING_NAMES = (
    "workspace_source_release_oidc_repository",
    "workspace_source_release_oidc_repository_id",
    "workspace_source_release_oidc_repository_owner_id",
    "workspace_source_release_oidc_workflow_ref",
    "workspace_source_release_oidc_organization_id",
)


class GitHubActionsOIDCError(ValueError):
    """The producer token is absent, invalid, or outside the pinned trust policy."""


async def _fetch_github_actions_jwks() -> dict[str, Any]:
    """Fetch and size-check GitHub's signing-key document."""
    try:
        async with asyncio.timeout(GITHUB_ACTIONS_JWKS_FETCH_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(GITHUB_ACTIONS_JWKS_FETCH_TIMEOUT_SECONDS),
                follow_redirects=False,
            ) as client:
                async with client.stream("GET", GITHUB_ACTIONS_JWKS_URL) as response:
                    response.raise_for_status()
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(payload) + len(chunk) > GITHUB_ACTIONS_JWKS_MAX_BYTES:
                            raise GitHubActionsOIDCError(
                                "GitHub Actions OIDC key set is too large"
                            )
                        payload.extend(chunk)
                    jwks = json.loads(payload)
    except GitHubActionsOIDCError:
        raise
    except (TimeoutError, httpx.HTTPError, ValueError) as exc:
        raise GitHubActionsOIDCError(
            "GitHub Actions OIDC signing keys are unavailable"
        ) from exc

    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    if not isinstance(keys, list) or len(keys) > GITHUB_ACTIONS_JWKS_MAX_KEYS:
        raise GitHubActionsOIDCError("GitHub Actions OIDC key set is invalid")
    return jwks


class _GitHubActionsJWKSCache:
    """Coalesce key refreshes and bound attacker-triggered unknown-kid work."""

    def __init__(self) -> None:
        self._jwks: dict[str, Any] | None = None
        self._refreshed_at = 0.0
        self._last_attempt_at = 0.0
        self._failure_until = 0.0
        self._generation = 0
        self._lock = asyncio.Lock()

    async def get(
        self,
        *,
        force_refresh: bool = False,
        observed_generation: int | None = None,
    ) -> tuple[dict[str, Any], int]:
        now = time.monotonic()
        if (
            self._jwks is not None
            and not force_refresh
            and now - self._refreshed_at < GITHUB_ACTIONS_JWKS_TTL_SECONDS
        ):
            return self._jwks, self._generation

        async with self._lock:
            now = time.monotonic()
            if self._jwks is not None:
                if force_refresh:
                    refreshed_by_waiter = (
                        observed_generation is not None
                        and self._generation != observed_generation
                    )
                    refresh_throttled = (
                        now - self._last_attempt_at
                        < GITHUB_ACTIONS_JWKS_UNKNOWN_KID_REFRESH_SECONDS
                    )
                    if refreshed_by_waiter or refresh_throttled:
                        return self._jwks, self._generation
                elif now - self._refreshed_at < GITHUB_ACTIONS_JWKS_TTL_SECONDS:
                    return self._jwks, self._generation

            if now < self._failure_until:
                raise GitHubActionsOIDCError(
                    "GitHub Actions OIDC signing keys are unavailable"
                )

            self._last_attempt_at = now
            try:
                jwks = await _fetch_github_actions_jwks()
            except GitHubActionsOIDCError:
                self._failure_until = (
                    time.monotonic() + GITHUB_ACTIONS_JWKS_FAILURE_BACKOFF_SECONDS
                )
                raise

            self._jwks = jwks
            self._refreshed_at = time.monotonic()
            self._failure_until = 0.0
            self._generation += 1
            return jwks, self._generation


_github_actions_jwks_cache = _GitHubActionsJWKSCache()


@dataclass(frozen=True)
class WorkspaceSourceReleaseProducer:
    organization_id: UUID
    source_commit_sha: str
    oidc_commit_sha: str
    repository: str
    workflow_ref: str
    run_id: str
    event_name: str
    triggering_workflow_run_id: str | None
    triggering_workflow_run_attempt: int | None = None
    declaration_digest: str | None = None
    actor: str | None = None
    actor_id: str | None = None


def workspace_source_release_producer_configured(settings: Settings) -> bool:
    """Return whether every setting required by the trusted producer is present."""
    return all(
        value is not None and str(value).strip() != ""
        for value in (
            getattr(settings, name, None)
            for name in _WORKSPACE_SOURCE_RELEASE_SETTING_NAMES
        )
    )


def workspace_source_release_tracking_expected(settings: Settings) -> bool:
    """Fail closed once an operator starts configuring the trusted producer."""
    return any(
        value is not None and str(value).strip() != ""
        for value in (
            getattr(settings, name, None)
            for name in _WORKSPACE_SOURCE_RELEASE_SETTING_NAMES
        )
    )


def workspace_source_release_tracking_organization_id(
    settings: Settings,
) -> UUID | None:
    """Return the organization that owns protected-source accountability."""
    raw = settings.workspace_source_release_oidc_organization_id
    if raw is None or not raw.strip():
        return None
    try:
        return UUID(raw)
    except ValueError as exc:
        raise GitHubActionsOIDCError(
            "BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_ORGANIZATION_ID must be a UUID"
        ) from exc


def _required_setting(value: str | int | None, name: str) -> str:
    if value is None or str(value).strip() == "":
        raise GitHubActionsOIDCError(
            f"Workspace source-release OIDC is not configured: {name} is required"
        )
    return str(value)


async def authenticate_workspace_source_release_producer(
    token: str,
    *,
    settings: Settings,
    jwks: dict[str, Any] | None = None,
) -> WorkspaceSourceReleaseProducer:
    """Verify one protected-main producer token against immutable GitHub claims."""
    if len(token) > GITHUB_ACTIONS_OIDC_MAX_TOKEN_LENGTH:
        raise GitHubActionsOIDCError("GitHub Actions OIDC token is too large")
    repository = _required_setting(
        settings.workspace_source_release_oidc_repository,
        "BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_REPOSITORY",
    )
    repository_id = _required_setting(
        settings.workspace_source_release_oidc_repository_id,
        "BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_REPOSITORY_ID",
    )
    owner_id = _required_setting(
        settings.workspace_source_release_oidc_repository_owner_id,
        "BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_REPOSITORY_OWNER_ID",
    )
    workflow_ref = _required_setting(
        settings.workspace_source_release_oidc_workflow_ref,
        "BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_WORKFLOW_REF",
    )
    _required_setting(
        settings.workspace_source_release_oidc_organization_id,
        "BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_ORGANIZATION_ID",
    )
    organization_id = workspace_source_release_tracking_organization_id(settings)
    assert organization_id is not None

    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise GitHubActionsOIDCError("GitHub Actions OIDC token is malformed") from exc
    kid = header.get("kid")
    if (
        header.get("alg") != "RS256"
        or not isinstance(kid, str)
        or not kid
        or len(kid) > GITHUB_ACTIONS_OIDC_MAX_KID_LENGTH
    ):
        raise GitHubActionsOIDCError(
            "GitHub Actions OIDC token must use RS256 with a key identifier"
        )

    try:
        unverified_claims = jwt.decode(
            token,
            options={"verify_signature": False},
        )
    except jwt.InvalidTokenError as exc:
        raise GitHubActionsOIDCError("GitHub Actions OIDC token is malformed") from exc
    token_audience = unverified_claims.get("aud")
    if not isinstance(token_audience, str):
        raise GitHubActionsOIDCError(
            "GitHub Actions OIDC token has an invalid source-release audience"
        )
    workflow_run_audience = _WORKFLOW_RUN_AUDIENCE.fullmatch(token_audience)
    workflow_dispatch_audience = _WORKFLOW_DISPATCH_AUDIENCE.fullmatch(token_audience)
    if (
        token_audience != WORKSPACE_SOURCE_RELEASE_AUDIENCE
        and workflow_run_audience is None
        and workflow_dispatch_audience is None
    ):
        raise GitHubActionsOIDCError(
            "GitHub Actions OIDC token has an invalid source-release audience"
        )

    cached_generation: int | None = None
    if jwks is None:
        jwks, cached_generation = await _github_actions_jwks_cache.get()

    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    if not isinstance(keys, list):
        raise GitHubActionsOIDCError("GitHub Actions OIDC key set is invalid")
    matching_key = next(
        (
            key
            for key in keys
            if isinstance(key, dict) and key.get("kid") == kid
        ),
        None,
    )
    if matching_key is None and cached_generation is not None:
        jwks, _ = await _github_actions_jwks_cache.get(
            force_refresh=True,
            observed_generation=cached_generation,
        )
        keys = jwks.get("keys") if isinstance(jwks, dict) else None
        if not isinstance(keys, list):
            raise GitHubActionsOIDCError("GitHub Actions OIDC key set is invalid")
        matching_key = next(
            (
                key
                for key in keys
                if isinstance(key, dict) and key.get("kid") == kid
            ),
            None,
        )
    if matching_key is None:
        raise GitHubActionsOIDCError(
            "GitHub Actions OIDC signing key is not recognized"
        )

    try:
        signing_key = jwt.PyJWK.from_dict(matching_key).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=token_audience,
            issuer=GITHUB_ACTIONS_ISSUER,
            options={
                "require": [
                    "aud",
                    "exp",
                    "iat",
                    "iss",
                    "jti",
                    "nbf",
                    "repository",
                    "repository_id",
                    "repository_owner_id",
                    "ref",
                    "ref_type",
                    "event_name",
                    "sha",
                    "workflow_ref",
                    "run_id",
                ]
            },
        )
    except jwt.InvalidTokenError as exc:
        raise GitHubActionsOIDCError(
            "GitHub Actions OIDC token failed signature or claim validation"
        ) from exc

    expected = {
        "repository": repository,
        "repository_id": repository_id,
        "repository_owner_id": owner_id,
        "ref": "refs/heads/main",
        "ref_type": "branch",
        "workflow_ref": workflow_ref,
    }
    mismatches = [
        claim
        for claim, expected_value in expected.items()
        if str(claims.get(claim)) != expected_value
    ]
    if mismatches:
        raise GitHubActionsOIDCError(
            "GitHub Actions OIDC token is outside the source-release trust policy: "
            + ", ".join(sorted(mismatches))
        )
    event_name = claims.get("event_name")
    if event_name not in WORKSPACE_SOURCE_RELEASE_EVENTS:
        raise GitHubActionsOIDCError(
            "GitHub Actions OIDC token is outside the source-release trust policy: "
            "event_name"
        )
    oidc_commit_sha = str(claims["sha"])
    if len(oidc_commit_sha) != 40 or any(
        char not in "0123456789abcdef" for char in oidc_commit_sha
    ):
        raise GitHubActionsOIDCError(
            "GitHub Actions OIDC token has an invalid source commit SHA"
        )
    if event_name == "push":
        if token_audience != WORKSPACE_SOURCE_RELEASE_AUDIENCE:
            raise GitHubActionsOIDCError(
                "push producer must use the fixed source-release audience"
            )
        source_commit_sha = oidc_commit_sha
        triggering_workflow_run_id = None
        triggering_workflow_run_attempt = None
        declaration_digest = None
    else:
        bound_audience = (
            workflow_run_audience
            if event_name == "workflow_run"
            else workflow_dispatch_audience
        )
        if bound_audience is None:
            raise GitHubActionsOIDCError(
                f"{event_name} producer must use its matching exact-bound "
                "source-release audience"
            )
        source_commit_sha = bound_audience.group("source_sha")
        triggering_workflow_run_id = bound_audience.group("run_id")
        triggering_workflow_run_attempt = (
            int(bound_audience.group("run_attempt"))
            if event_name == "workflow_dispatch"
            else None
        )
        declaration_digest = (
            bound_audience.group("declaration_digest")
            if event_name == "workflow_dispatch"
            else None
        )
    actor = str(claims["actor"]) if claims.get("actor") is not None else None
    actor_id = str(claims["actor_id"]) if claims.get("actor_id") is not None else None
    if event_name == "workflow_dispatch":
        actor_mismatches = []
        if actor != "github-actions[bot]":
            actor_mismatches.append("actor")
        if actor_id != str(GITHUB_ACTIONS_BOT_ACTOR_ID):
            actor_mismatches.append("actor_id")
        if actor_mismatches:
            raise GitHubActionsOIDCError(
                "GitHub Actions OIDC token is outside the source-release trust "
                "policy: " + ", ".join(actor_mismatches)
            )
    return WorkspaceSourceReleaseProducer(
        organization_id=organization_id,
        source_commit_sha=source_commit_sha,
        oidc_commit_sha=oidc_commit_sha,
        repository=repository,
        workflow_ref=workflow_ref,
        run_id=str(claims["run_id"]),
        event_name=str(event_name),
        triggering_workflow_run_id=triggering_workflow_run_id,
        triggering_workflow_run_attempt=triggering_workflow_run_attempt,
        declaration_digest=declaration_digest,
        actor=actor,
        actor_id=actor_id,
    )
