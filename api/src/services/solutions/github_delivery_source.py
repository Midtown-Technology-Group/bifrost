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
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any
from uuid import UUID

import httpx
import jwt
from bifrost.workspace_release import canonical_digest

from src.core.solution_delivery_policy import SolutionGitDeliveryPolicy, delivery_path
from src.services.github_actions_oidc import (
    GITHUB_ACTIONS_ISSUER,
    GITHUB_ACTIONS_JWKS_URL,
)
from src.services.solutions.deployment_manifest import (
    MAX_DEPLOYMENT_RESOURCE_BYTES,
    MAX_DEPLOYMENT_RESOURCES_BYTES,
)
from src.services.solutions.workflow_revision_recipe import (
    WORKFLOW_RECIPE_SCHEMA,
    ReviewedWorkflowRecipe,
)

AUDIENCE = "bifrost-solution-git-delivery/v1"
RECIPE_SCHEMA = "bifrost.solution-source-delivery/v1"
MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_METADATA_BYTES = 5 * 1024 * 1024
MAX_AUTHORED_FILES = 1000


class GitDeliverySourceError(ValueError):
    """Producer, CI or protected source cannot authorize this delivery."""


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GitDeliverySourceError("Reviewed recipe JSON contains duplicate keys")
        result[key] = value
    return result


def _reviewed_recipe(raw: bytes, solution_id: UUID) -> tuple[dict, ReviewedWorkflowRecipe | None]:
    try:
        recipe = json.loads(raw, object_pairs_hook=_unique_json_object)
        workflow_recipe = None
        if isinstance(recipe, dict) and recipe.get("schema_version") == WORKFLOW_RECIPE_SCHEMA:
            workflow_recipe = ReviewedWorkflowRecipe.model_validate(recipe)
        if (not isinstance(recipe, dict) or recipe.get("schema_version") not in (RECIPE_SCHEMA, WORKFLOW_RECIPE_SCHEMA)
                or workflow_recipe is None and set(recipe) != {"schema_version", "solution_id", "files"}
                or recipe.get("solution_id") != str(solution_id)
                or not isinstance(recipe.get("files"), dict) or not 1 <= len(recipe["files"]) <= 256):
            raise GitDeliverySourceError("Reviewed installed Solution recipe is invalid")
        return recipe, workflow_recipe
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, GitDeliverySourceError):
            raise
        raise GitDeliverySourceError("Reviewed recipe JSON is invalid") from exc


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
    workflow_recipe: ReviewedWorkflowRecipe | None = None
    resources: dict[str, bytes] = field(default_factory=dict)
    # Populated only by the protected tree reader, never by request payloads.
    repository_paths: dict[str, str] = field(default_factory=dict)
    control_hashes: dict[str, str] = field(default_factory=dict)
    installation_registry: dict[str, Any] | None = None
    ancestor_commit_shas: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerifiedAuthoredSolutionFile:
    path: str
    mode: str
    sha256: str
    size: int


@dataclass(frozen=True)
class VerifiedAuthoredSolution:
    """Exact authored Git inventory; neither runtime closure nor delivery proof."""

    commit_sha: str
    tree_sha: str
    subtree_sha: str
    solution_slug: str
    repo_subpath: str
    source_content_id: str
    source_files: tuple[VerifiedAuthoredSolutionFile, ...]
    files: Mapping[str, bytes]

    def file_manifest(self) -> list[dict[str, object]]:
        return [{"path": item.path, "mode": item.mode, "sha256": item.sha256,
                 "size": item.size} for item in self.source_files]


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
        except ValueError as exc:
            if isinstance(exc, GitDeliverySourceError):
                raise
            raise GitDeliverySourceError("Protected Git metadata is unavailable") from exc

    async def require_current_main(self, commit_sha: str) -> None:
        branch = await self.document("branches/main")
        if branch.get("protected") is not True or branch.get("commit", {}).get("sha") != commit_sha:
            raise GitDeliverySourceError("Source was superseded or main is not protected; deliver full current state")

    async def verified_ancestors(self, commit_sha: str, candidates: set[str], *, limit: int = 100) -> tuple[str, ...]:
        """Bounded first-parent Git proof for delayed accounting, not time order.

        A truncated walk leaves older debt unresolved. Delivery is still allowed;
        no missing ancestor is guessed from timestamps or equal source bytes.
        """
        matched: list[str] = []
        seen = {commit_sha}
        current = commit_sha
        remaining = candidates - seen
        for _ in range(min(max(limit, 0), 100)):
            if not remaining:
                break
            document = await self.document(f"git/commits/{current}")
            parents = document.get("parents", [])
            if document.get("sha") != current or not isinstance(parents, list) or len(parents) != 1:
                break
            parent = parents[0].get("sha") if isinstance(parents[0], dict) else None
            if not isinstance(parent, str) or re.fullmatch(r"[0-9a-f]{40}", parent) is None or parent in seen:
                break
            seen.add(parent)
            if parent in remaining:
                matched.append(parent)
                remaining.remove(parent)
            current = parent
        return tuple(matched)

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

    async def authored_source(
        self, commit_sha: str, repo_subpath: str, expected_tree_sha: str,
    ) -> VerifiedAuthoredSolution:
        """Read a complete authored subtree independently of a runtime recipe.

        Callers still own protected-main/CI authorization and installed readback.
        This method cannot write artifacts or complete accounting.
        """
        from src.services.solution_deploy_obligations import solution_source_content_id

        if any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None
               for value in (commit_sha, expected_tree_sha)):
            raise GitDeliverySourceError("Expected exact authored commit/tree SHAs")
        if (not isinstance(repo_subpath, str)
                or re.fullmatch(r"solutions/[a-z0-9]+(?:-[a-z0-9]+)*", repo_subpath) is None):
            raise GitDeliverySourceError("Expected a canonical Solution subtree path")

        def tree_index(document: dict, identity: str) -> dict[str, dict]:
            entries = document.get("tree")
            if (document.get("sha") != identity or document.get("truncated") is not False
                    or not isinstance(entries, list)):
                raise GitDeliverySourceError("Authored Git tree is incomplete or differs")
            index: dict[str, dict] = {}
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                    raise GitDeliverySourceError("Authored Git tree entry is invalid")
                path = entry["path"]
                try:
                    delivery_path(path)
                except ValueError as exc:
                    raise GitDeliverySourceError("Authored Git tree path is unsafe") from exc
                if path in index:
                    raise GitDeliverySourceError("Authored Git tree paths are ambiguous")
                index[path] = entry
            return index

        commit = await self.document(f"git/commits/{commit_sha}")
        if (commit.get("sha") != commit_sha or not isinstance(commit.get("tree"), dict)
                or commit["tree"].get("sha") != expected_tree_sha):
            raise GitDeliverySourceError("Authored commit/tree identity differs")
        root = tree_index(await self.document(f"git/trees/{expected_tree_sha}?recursive=1"), expected_tree_sha)
        subtree = root.get(repo_subpath, {})
        subtree_sha = subtree.get("sha")
        if (subtree.get("type") != "tree" or subtree.get("mode") != "040000"
                or not isinstance(subtree_sha, str) or re.fullmatch(r"[0-9a-f]{40}", subtree_sha) is None):
            raise GitDeliverySourceError("Authored Solution subtree identity is invalid")
        selected = tree_index(await self.document(f"git/trees/{subtree_sha}?recursive=1"), subtree_sha)
        prefix = repo_subpath + "/"
        root_selected = {path[len(prefix):]: entry for path, entry in root.items() if path.startswith(prefix)}

        def signature(entry: dict) -> tuple:
            return entry.get("type"), entry.get("mode"), entry.get("sha"), entry.get("size")

        if (set(selected) != set(root_selected)
                or any(signature(entry) != signature(root_selected[path]) for path, entry in selected.items())):
            raise GitDeliverySourceError("Authored subtree differs from the protected root tree")
        blobs: dict[str, dict] = {}
        total = 0
        for path, entry in selected.items():
            sha = entry.get("sha")
            if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
                raise GitDeliverySourceError("Authored Git entry identity is invalid")
            parts = path.split("/")
            if any(selected.get("/".join(parts[:depth]), {}).get("type") != "tree"
                   for depth in range(1, len(parts))):
                raise GitDeliverySourceError("Authored Git tree parent is missing or invalid")
            if entry.get("type") == "tree" and entry.get("mode") == "040000":
                continue
            if (entry.get("type") != "blob" or entry.get("mode") not in {"100644", "100755"}
                    or type(entry.get("size")) is not int or not 0 <= entry["size"] <= MAX_SOURCE_BYTES):
                raise GitDeliverySourceError("Authored source requires bounded regular Git files")
            total += entry["size"]
            blobs[path] = entry
            if len(blobs) > MAX_AUTHORED_FILES or total > MAX_SOURCE_BYTES:
                raise GitDeliverySourceError("Authored source exceeds its inventory bound")
        if not blobs:
            raise GitDeliverySourceError("Authored Solution subtree is empty")
        slots = asyncio.Semaphore(16)

        async def read(path: str, entry: dict) -> tuple[str, bytes]:
            async with slots:
                return path, await self.blob(entry, limit=MAX_SOURCE_BYTES)

        files = dict(await asyncio.gather(*(read(path, blobs[path]) for path in sorted(blobs))))
        manifest = tuple(VerifiedAuthoredSolutionFile(
            path=prefix + path, mode=blobs[path]["mode"],
            sha256=hashlib.sha256(content).hexdigest(), size=len(content),
        ) for path, content in files.items())
        manifest_values: list[dict[str, object]] = [
            {"path": item.path, "mode": item.mode, "sha256": item.sha256, "size": item.size}
            for item in manifest
        ]
        return VerifiedAuthoredSolution(
            commit_sha=commit_sha, tree_sha=expected_tree_sha, subtree_sha=subtree_sha,
            solution_slug=repo_subpath.split("/")[1], repo_subpath=repo_subpath,
            source_content_id=solution_source_content_id(solution_slug=repo_subpath.split("/")[1],
                repo_subpath=repo_subpath, source_files=manifest_values),
            source_files=manifest, files=MappingProxyType(files),
        )

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
        recipe_bytes = await self.blob(index[recipe_path], limit=128 * 1024)
        recipe, workflow_recipe = _reviewed_recipe(recipe_bytes, solution_id)
        files: dict[str, bytes] = {}
        resources: dict[str, bytes] = {}
        slots = asyncio.Semaphore(16)

        async def read(runtime_path, git_path, *, resource=False):
            if not isinstance(runtime_path, str) or not isinstance(git_path, str):
                raise GitDeliverySourceError("Recipe source paths must be strings")
            try:
                delivery_path(runtime_path)
                delivery_path(git_path)
            except ValueError as exc:
                raise GitDeliverySourceError("Recipe source path is unsafe") from exc
            if git_path not in index or not resource and (not runtime_path.endswith(".py") or not git_path.endswith(".py")):
                raise GitDeliverySourceError("Recipe must contain existing Python source")
            async with slots:
                content = await self.blob(index[git_path], limit=MAX_DEPLOYMENT_RESOURCE_BYTES if resource else MAX_SOURCE_BYTES)
                (resources if resource else files)[runtime_path] = content

        # Bound the complete desired tree before any concurrent blob reads.
        sizes = [index.get(path, {}).get("size") for path in recipe["files"].values() if isinstance(path, str)]
        if (len(sizes) != len(recipe["files"]) or any(type(size) is not int or size < 0 for size in sizes)
                or sum(sizes) > MAX_SOURCE_BYTES):
            raise GitDeliverySourceError("Complete source exceeds its artifact bound")
        resource_mapping = workflow_recipe.resources if workflow_recipe is not None else {}
        sizes = [index.get(path, {}).get("size") for path in resource_mapping.values()]
        if (any(type(size) is not int or not 1 <= size <= MAX_DEPLOYMENT_RESOURCE_BYTES for size in sizes)
                or sum(sizes) > MAX_DEPLOYMENT_RESOURCES_BYTES):
            raise GitDeliverySourceError("Complete resources exceed their artifact bounds")
        await asyncio.gather(*(read(path, git_path) for path, git_path in recipe["files"].items()),
            *(read(path, git_path, resource=True) for path, git_path in resource_mapping.items()))
        if sum(len(content) for content in files.values()) > MAX_SOURCE_BYTES:
            raise GitDeliverySourceError("Complete source exceeds its artifact bound")
        hashes = {path: "sha256:" + hashlib.sha256(content).hexdigest()
            for path, content in sorted({**files, **resources}.items())}
        digest_input = {"schema_version": recipe["schema_version"], "solution_id": str(solution_id),
            "source_commit_sha": commit_sha, "source_tree_sha": tree_sha, "recipe_path": recipe_path,
            "source_hashes": hashes}
        if workflow_recipe is not None:
            digest_input["reviewed_recipe"] = recipe
        digest = canonical_digest(digest_input)
        if digest != artifact_digest:
            raise GitDeliverySourceError("Protected Git artifact differs from the bound producer digest")
        control_hashes = {recipe_path: hashlib.sha256(recipe_bytes).hexdigest()}
        registry_path = "config/solution-delivery/installations.json"
        registry_proof = None
        if registry_path in index:
            raw_registry = await self.blob(index[registry_path], limit=128 * 1024)
            try:
                registry = json.loads(raw_registry, object_pairs_hook=_unique_json_object)
                if (not isinstance(registry, dict)
                        or set(registry) != {"schema_version", "installations"}
                        or registry["schema_version"] != "bifrost.solution-delivery-installations/v1"
                        or not isinstance(registry["installations"], list)
                        or not 1 <= len(registry["installations"]) <= 100):
                    raise ValueError("Invalid installation registry")
                rows = registry["installations"]
                for row in rows:
                    if (not isinstance(row, dict) or set(row) != {"target", "recipe"}
                            or row["target"] not in {"production", "canary"}):
                        raise ValueError("Invalid installation registry entry")
                    delivery_path(row["recipe"])
                selected = [row for row in rows if row["recipe"] == recipe_path]
                if len(selected) != 1:
                    raise ValueError("Recipe has no unique registry target")
                target = selected[0]["target"]
                recipes = [row["recipe"] for row in rows if row["target"] == target]
                if len(set(recipes)) != len(recipes) or set(recipes) != set(self.policy.solutions.values()):
                    raise ValueError("Registry target differs from configured installations")
                sizes = [index.get(path, {}).get("size") for path in self.policy.solutions.values()]
                if (any(type(size) is not int or not 0 <= size <= 128 * 1024 for size in sizes)
                        or sum(sizes) + len(raw_registry) > MAX_METADATA_BYTES):
                    raise ValueError("Registry recipes exceed their total metadata bound")

                async def installation(identity: UUID, path: str) -> tuple[str, dict]:
                    async with slots:
                        raw = recipe_bytes if path == recipe_path else await self.blob(index[path], limit=128 * 1024)
                    declared, reviewed = _reviewed_recipe(raw, identity)
                    resource_paths = reviewed.resources if reviewed is not None else {}
                    mappings = {**declared["files"], **resource_paths}
                    roots: set[str | None] = set()
                    for runtime_path, git_path in mappings.items():
                        if not isinstance(runtime_path, str) or not isinstance(git_path, str):
                            raise TypeError("Registry recipe source paths must be strings")
                        delivery_path(runtime_path)
                        delivery_path(git_path)
                        source_entry = index.get(git_path, {})
                        if (source_entry.get("type") != "blob" or source_entry.get("mode") not in {"100644", "100755"}
                                or not isinstance(source_entry.get("sha"), str)
                                or re.fullmatch(r"[0-9a-f]{40}", source_entry["sha"]) is None
                                or type(source_entry.get("size")) is not int or source_entry["size"] < 0
                                or runtime_path not in resource_paths
                                and (not runtime_path.endswith(".py") or not git_path.endswith(".py"))):
                            raise ValueError("Registry recipe requires existing regular source files")
                        match = re.match(r"^(solutions/[a-z0-9]+(?:-[a-z0-9]+)*)/", git_path)
                        roots.add(match.group(1) if match else None)
                    authored_root = next(iter(roots)) if len(roots) == 1 and None not in roots else None
                    scope = self.policy.organization_id_for(identity)
                    return str(identity), {"recipe_path": path,
                        "organization_id": str(scope) if scope is not None else None,
                        "repo_subpath": authored_root,
                        "package_subpaths": sorted(root for root in roots if root is not None)}

                installations = dict(await asyncio.gather(*(installation(identity, path)
                    for identity, path in self.policy.solutions.items())))
                registry_proof = {"path": registry_path, "target": target,
                    "installations": installations}
                control_hashes[registry_path] = hashlib.sha256(raw_registry).hexdigest()
            except (ValueError, TypeError, KeyError, UnicodeError) as exc:
                raise GitDeliverySourceError("Protected installation registry is invalid") from exc
        return VerifiedGitSource(solution_id, commit_sha, tree_sha, recipe_path, hashes, files, digest,
            workflow_recipe, resources, {**recipe["files"], **resource_mapping}, control_hashes, registry_proof)
