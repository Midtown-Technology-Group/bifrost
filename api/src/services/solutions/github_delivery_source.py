"""Authenticate a narrow producer and verify full source from protected Git.

No supplied source bytes or Git SHA assertion can authorize an activation.
GitHub is read through fixed endpoints with bounded responses and no redirects.
The ephemeral GitHub job token is used in memory only and grants no API role.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import jwt

from bifrost.workspace_release import canonical_digest
from src.core.solution_delivery_policy import SolutionGitDeliveryPolicy, delivery_path
from src.services.github_actions_oidc import GITHUB_ACTIONS_ISSUER, GITHUB_ACTIONS_JWKS_URL

AUDIENCE = "bifrost-solution-git-delivery/v1"
RECIPE_SCHEMA = "bifrost.solution-source-delivery/v1"
MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_METADATA_BYTES = 5 * 1024 * 1024


class GitDeliverySourceError(ValueError):
    """Producer, CI or protected source cannot authorize this delivery."""


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GitDeliverySourceError("Reviewed recipe JSON contains duplicate keys")
        result[key] = value
    return result


@dataclass(frozen=True)
class GitDeliveryIdentity:
    run_id: str
    run_attempt: int


@dataclass(frozen=True)
class VerifiedGitSource:
    solution_id: UUID
    commit_sha: str
    tree_sha: str
    recipe_path: str
    source_hashes: dict[str, str]
    files: dict[str, bytes]
    artifact_digest: str


def delivery_audience(solution_id: UUID, commit_sha: str, ci_run_id: int,
                      ci_run_attempt: int, artifact_digest: str) -> str:
    return f"{AUDIENCE}:{solution_id}:{commit_sha}:{ci_run_id}:{ci_run_attempt}:{artifact_digest}"


async def authenticate_git_delivery(
    token: str, *, policy: SolutionGitDeliveryPolicy, solution_id: UUID,
    commit_sha: str, ci_run_id: int, ci_run_attempt: int, artifact_digest: str,
    jwks: dict[str, Any] | None = None,
) -> GitDeliveryIdentity:
    if solution_id not in policy.solutions:
        raise GitDeliverySourceError("Solution is outside the delivery allowlist")
    audience = delivery_audience(solution_id, commit_sha, ci_run_id, ci_run_attempt, artifact_digest)
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise GitDeliverySourceError("Delivery token must use RS256 and a key identifier")
        if jwks is None:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.get(GITHUB_ACTIONS_JWKS_URL)
                response.raise_for_status()
                jwks = response.json()
        keys = jwks.get("keys")
        if not isinstance(keys, list):
            raise GitDeliverySourceError("Invalid GitHub signing key set")
        key = next((item for item in keys if isinstance(item, dict)
                    and item.get("kid") == header["kid"] and item.get("kty") == "RSA"), None)
        if key is None:
            raise GitDeliverySourceError("Unknown GitHub delivery signing key")
        claims = jwt.decode(token, jwt.PyJWK.from_dict(key).key, algorithms=["RS256"],
            audience=audience, issuer=GITHUB_ACTIONS_ISSUER,
            options={"require": ["aud", "iss", "exp", "iat", "nbf", "jti", "repository",
                "repository_id", "repository_owner_id", "ref", "ref_type", "sha", "workflow_ref",
                "workflow_sha", "event_name", "run_id", "run_attempt"]})
    except (jwt.PyJWTError, httpx.HTTPError, ValueError, AttributeError) as exc:
        if isinstance(exc, GitDeliverySourceError):
            raise
        raise GitDeliverySourceError("GitHub delivery signature or claims could not be verified") from exc
    expected = {"repository": policy.repository, "repository_id": str(policy.repository_id),
        "repository_owner_id": str(policy.repository_owner_id), "ref": "refs/heads/main",
        "ref_type": "branch", "sha": commit_sha, "workflow_sha": commit_sha,
        "workflow_ref": f"{policy.repository}/{policy.workflow_path}@refs/heads/main"}
    if any(str(claims.get(name)) != value for name, value in expected.items()):
        raise GitDeliverySourceError("Delivery token is outside the pinned producer policy")
    if claims["event_name"] not in {"workflow_run", "workflow_dispatch"}:
        raise GitDeliverySourceError("Delivery must follow successful protected-main CI")
    run_id, run_attempt = str(claims["run_id"]), str(claims["run_attempt"])
    if (re.fullmatch(r"[0-9]+", run_id) is None or int(run_id) < 1
            or re.fullmatch(r"[0-9]+", run_attempt) is None or int(run_attempt) < 1):
        raise GitDeliverySourceError("Invalid producer run identity")
    return GitDeliveryIdentity(run_id=run_id, run_attempt=int(run_attempt))


class ProtectedGitReader:
    """Read only the configured repo; never execute code or follow Git redirects."""

    def __init__(self, policy: SolutionGitDeliveryPolicy, token: str, client: httpx.AsyncClient):
        self.policy, self.token, self.client = policy, token, client

    async def document(self, suffix: str, *, limit: int = MAX_METADATA_BYTES) -> dict:
        if re.fullmatch(
            r"(?:branches/main|actions/runs/[1-9][0-9]*|git/(?:commits|blobs)/[0-9a-f]{40}"
            r"|git/trees/[0-9a-f]{40}\?recursive=1)?", suffix
        ) is None:
            raise GitDeliverySourceError("Protected Git endpoint is outside the delivery allowlist")
        path, _, query = suffix.partition("?")
        # Build authority from a constant URL. Only the validated repository
        # path can vary, never the scheme, host, port or a redirect destination.
        url = httpx.URL("https://api.github.com").copy_with(
            path=f"/repos/{self.policy.repository}" + (f"/{path}" if path else ""),
            query=query.encode("ascii") if query else None,
        )
        try:
            async with self.client.stream("GET", url, headers={"Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
                follow_redirects=False, timeout=20) as response:
                if response.status_code != 200:
                    raise GitDeliverySourceError(f"Protected Git read returned HTTP {response.status_code}")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise GitDeliverySourceError("Protected Git response exceeds its size bound")
                    chunks.append(chunk)
            value = json.loads(b"".join(chunks))
            if not isinstance(value, dict):
                raise GitDeliverySourceError("Unexpected protected Git response")
            return value
        except (httpx.HTTPError, ValueError) as exc:
            if isinstance(exc, GitDeliverySourceError):
                raise
            raise GitDeliverySourceError("Protected Git metadata is unavailable") from exc

    async def require_current_main(self, commit_sha: str) -> None:
        branch = await self.document("branches/main")
        if branch.get("protected") is not True or branch.get("commit", {}).get("sha") != commit_sha:
            raise GitDeliverySourceError("Source was superseded or main is not protected; deliver full current state")

    async def verify_ci(self, commit_sha: str, run_id: int, attempt: int) -> None:
        repository = await self.document("")
        if (repository.get("id") != self.policy.repository_id
                or repository.get("owner", {}).get("id") != self.policy.repository_owner_id
                or repository.get("full_name") != self.policy.repository
                or repository.get("default_branch") != "main"):
            raise GitDeliverySourceError("GitHub repository identity changed")
        run = await self.document(f"actions/runs/{run_id}")
        expected = {"id": run_id, "run_attempt": attempt, "head_sha": commit_sha,
            "head_branch": "main", "workflow_id": self.policy.ci_workflow_id,
            "path": self.policy.ci_workflow_path, "status": "completed", "conclusion": "success"}
        if (any(run.get(name) != value for name, value in expected.items())
                or run.get("event") not in {"push", "workflow_dispatch"}
                or run.get("repository", {}).get("id") != self.policy.repository_id
                or run.get("head_repository", {}).get("id") != self.policy.repository_id):
            raise GitDeliverySourceError("Exact protected-main CI has not passed its latest attempt")
        await self.require_current_main(commit_sha)

    async def blob(self, entry: dict, *, limit: int) -> bytes:
        sha = entry.get("sha")
        if (entry.get("type") != "blob" or entry.get("mode") not in {"100644", "100755"}
                or not isinstance(sha, str) or len(sha) != 40
                or any(char not in "0123456789abcdef" for char in sha)
                or not isinstance(entry.get("size"), int) or not 0 <= entry["size"] <= limit):
            raise GitDeliverySourceError("Source must be a bounded regular Git blob")
        document = await self.document(f"git/blobs/{sha}", limit=entry["size"] * 3 // 2 + 8192)
        try:
            if document.get("encoding") != "base64" or document.get("sha") != sha:
                raise GitDeliverySourceError("Git blob encoding or identity changed")
            content = base64.b64decode(document["content"].replace("\n", ""), validate=True)
        except (KeyError, AttributeError, binascii.Error) as exc:
            raise GitDeliverySourceError("Git blob encoding is invalid") from exc
        actual_sha = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content,
                                  usedforsecurity=False).hexdigest()
        if len(content) > limit or len(content) != entry["size"] or actual_sha != sha:
            raise GitDeliverySourceError("Git blob bytes differ from the protected tree")
        return content

    async def source(self, solution_id: UUID, commit_sha: str, artifact_digest: str) -> VerifiedGitSource:
        if solution_id not in self.policy.solutions:
            raise GitDeliverySourceError("Solution is outside the delivery allowlist")
        if re.fullmatch(r"[0-9a-f]{40}", commit_sha) is None:
            raise GitDeliverySourceError("Expected an exact protected commit SHA")
        commit = await self.document(f"git/commits/{commit_sha}")
        tree_sha = commit.get("tree", {}).get("sha")
        if (commit.get("sha") != commit_sha or not isinstance(tree_sha, str) or len(tree_sha) != 40
                or any(char not in "0123456789abcdef" for char in tree_sha)):
            raise GitDeliverySourceError("Protected commit/tree identity differs")
        tree = await self.document(f"git/trees/{tree_sha}?recursive=1")
        entries = tree.get("tree")
        if tree.get("sha") != tree_sha or tree.get("truncated") is not False or not isinstance(entries, list):
            raise GitDeliverySourceError("Protected tree is incomplete")
        index = {}
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or entry["path"] in index:
                raise GitDeliverySourceError("Protected tree paths are ambiguous")
            index[entry["path"]] = entry
        recipe_path = self.policy.solutions[solution_id]
        if recipe_path not in index:
            raise GitDeliverySourceError("Reviewed installed Solution recipe is absent")
        try:
            recipe = json.loads(await self.blob(index[recipe_path], limit=128 * 1024),
                                object_pairs_hook=_unique_json_object)
            if (not isinstance(recipe, dict) or recipe.get("schema_version") != RECIPE_SCHEMA
                    or set(recipe) != {"schema_version", "solution_id", "files"}
                    or recipe.get("solution_id") != str(solution_id)
                    or not isinstance(recipe.get("files"), dict) or not 1 <= len(recipe["files"]) <= 256):
                raise GitDeliverySourceError("Reviewed installed Solution recipe is invalid")
        except (ValueError, UnicodeError) as exc:
            if isinstance(exc, GitDeliverySourceError):
                raise
            raise GitDeliverySourceError("Reviewed recipe JSON is invalid") from exc
        files: dict[str, bytes] = {}
        slots = asyncio.Semaphore(16)

        async def read(runtime_path, git_path):
            if not isinstance(runtime_path, str) or not isinstance(git_path, str):
                raise GitDeliverySourceError("Recipe source paths must be strings")
            try:
                delivery_path(runtime_path)
                delivery_path(git_path)
            except ValueError as exc:
                raise GitDeliverySourceError("Recipe source path is unsafe") from exc
            if not runtime_path.endswith(".py") or not git_path.endswith(".py") or git_path not in index:
                raise GitDeliverySourceError("Recipe must contain existing Python source")
            async with slots:
                files[runtime_path] = await self.blob(index[git_path], limit=MAX_SOURCE_BYTES)

        # Bound the complete desired tree before any concurrent blob reads.
        sizes = [index.get(path, {}).get("size") for path in recipe["files"].values() if isinstance(path, str)]
        if (len(sizes) != len(recipe["files"]) or any(type(size) is not int or size < 0 for size in sizes)
                or sum(sizes) > MAX_SOURCE_BYTES):
            raise GitDeliverySourceError("Complete source exceeds its artifact bound")
        await asyncio.gather(*(read(path, git_path) for path, git_path in recipe["files"].items()))
        if sum(len(content) for content in files.values()) > MAX_SOURCE_BYTES:
            raise GitDeliverySourceError("Complete source exceeds its artifact bound")
        hashes = {path: "sha256:" + hashlib.sha256(content).hexdigest() for path, content in sorted(files.items())}
        digest = canonical_digest({"schema_version": RECIPE_SCHEMA, "solution_id": str(solution_id),
            "source_commit_sha": commit_sha, "source_tree_sha": tree_sha, "recipe_path": recipe_path,
            "source_hashes": hashes})
        if digest != artifact_digest:
            raise GitDeliverySourceError("Protected Git artifact differs from the bound producer digest")
        return VerifiedGitSource(solution_id, commit_sha, tree_sha, recipe_path, hashes, files, digest)
