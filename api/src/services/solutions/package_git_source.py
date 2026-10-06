"""Protected full-package source proof, without deploy or accounting effects."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from bifrost.solution_package_delivery import (
    build_solution_package_archive,
    load_solution_package_recipe,
    review_solution_package_source,
)
from bifrost.workspace_release import canonical_digest

from src.core.solution_package_delivery_policy import SolutionPackageGitDeliveryPolicy
from src.services.solutions.github_delivery_source import (
    GitDeliveryIdentity,
    GitDeliverySourceError,
    ProtectedGitReader,
    VerifiedAuthoredSolution,
    authenticate_protected_git_producer,
)

PACKAGE_GIT_SOURCE_SCHEMA = "bifrost.solution-package-git-source/v1"


@dataclass(frozen=True)
class VerifiedSolutionPackageSource:
    authored: VerifiedAuthoredSolution
    source_archive: bytes
    evidence_json: bytes
    artifact_digest: str

    def evidence(self) -> dict[str, Any]:
        # Return a fresh copy: callers cannot mutate the retained byte evidence.
        return json.loads(self.evidence_json)


def package_delivery_audience(
    solution_id: UUID,
    commit_sha: str,
    ci_run_id: int,
    ci_run_attempt: int,
    artifact_digest: str,
) -> str:
    return (
        f"bifrost-solution-package-git-delivery/v1:{solution_id}:{commit_sha}:"
        f"{ci_run_id}:{ci_run_attempt}:{artifact_digest}"
    )


async def authenticate_package_git_delivery(
    token: str,
    *,
    policy: SolutionPackageGitDeliveryPolicy,
    solution_id: UUID,
    commit_sha: str,
    ci_run_id: int,
    ci_run_attempt: int,
    artifact_digest: str,
    jwks: dict[str, Any] | None = None,
) -> GitDeliveryIdentity:
    try:
        policy.enrollment_for(solution_id)
    except ValueError as exc:
        raise GitDeliverySourceError(str(exc)) from exc
    return await authenticate_protected_git_producer(
        token,
        policy=policy,
        commit_sha=commit_sha,
        audience=package_delivery_audience(
            solution_id, commit_sha, ci_run_id, ci_run_attempt, artifact_digest
        ),
        jwks=jwks,
    )


def package_source_evidence(
    solution_id: UUID,
    policy: SolutionPackageGitDeliveryPolicy,
    source: VerifiedAuthoredSolution,
    recipe_bytes: bytes,
    *,
    ci_run_id: int,
    ci_run_attempt: int,
) -> dict[str, Any]:
    """Bind source and reviewed manifests; installed/runtime proof stays false."""
    enrollment = policy.enrollment_for(solution_id)
    recipe = load_solution_package_recipe(recipe_bytes)
    if (
        recipe.solution_id != solution_id
        or recipe.organization_id != enrollment.organization_id
        or recipe.repo_subpath != enrollment.repo_subpath
        or source.repo_subpath != enrollment.repo_subpath
    ):
        raise GitDeliverySourceError("Package recipe/source differs from enrollment")
    prefix = enrollment.repo_subpath + "/"
    modes = {item.path.removeprefix(prefix): item.mode for item in source.source_files}
    proof = review_solution_package_source(
        recipe.model_dump(mode="json"),
        source.files,
        modes,
        source_commit_sha=source.commit_sha,
        source_tree_sha=source.tree_sha,
    )
    return {
        "schema_version": PACKAGE_GIT_SOURCE_SCHEMA,
        "repository": policy.repository,
        "repository_id": policy.repository_id,
        "repository_owner_id": policy.repository_owner_id,
        "recipe_path": enrollment.recipe_path,
        "recipe_sha256": hashlib.sha256(recipe_bytes).hexdigest(),
        "source_subtree_sha": source.subtree_sha,
        "ci_run_id": ci_run_id,
        "ci_run_attempt": ci_run_attempt,
        "package": proof,
    }


async def read_package_git_source(
    reader: ProtectedGitReader,
    *,
    policy: SolutionPackageGitDeliveryPolicy,
    solution_id: UUID,
    commit_sha: str,
    ci_run_id: int,
    ci_run_attempt: int,
    artifact_digest: str,
) -> VerifiedSolutionPackageSource:
    """Read only current, CI-qualified Git; no uploaded source or caller recipe."""
    try:
        enrollment = policy.enrollment_for(solution_id)
        if reader.policy != policy:
            raise GitDeliverySourceError(
                "Package reader policy differs from enrollment"
            )
        async with asyncio.timeout(200):
            await reader.verify_ci(commit_sha, ci_run_id, ci_run_attempt)
            commit = await reader.document(f"git/commits/{commit_sha}")
            tree_sha = commit.get("tree", {}).get("sha")
            if commit.get("sha") != commit_sha or not isinstance(tree_sha, str):
                raise GitDeliverySourceError("Package source commit/tree differs")
            tree = await reader.document(f"git/trees/{tree_sha}?recursive=1")
            entries = tree.get("tree")
            if (
                tree.get("sha") != tree_sha
                or tree.get("truncated") is not False
                or not isinstance(entries, list)
                or any(not isinstance(entry, dict) for entry in entries)
            ):
                raise GitDeliverySourceError("Package recipe tree is incomplete")
            recipes = [
                row for row in entries if row.get("path") == enrollment.recipe_path
            ]
            if len(recipes) != 1:
                raise GitDeliverySourceError(
                    "Reviewed package recipe is missing or ambiguous"
                )
            recipe_bytes = await reader.blob(recipes[0], limit=128 * 1024)
            source = await reader.authored_source(
                commit_sha, enrollment.repo_subpath, tree_sha
            )
            evidence = package_source_evidence(
                solution_id,
                policy,
                source,
                recipe_bytes,
                ci_run_id=ci_run_id,
                ci_run_attempt=ci_run_attempt,
            )
            if canonical_digest(evidence) != artifact_digest:
                raise GitDeliverySourceError(
                    "Package source differs from producer artifact"
                )
            await reader.verify_ci(commit_sha, ci_run_id, ci_run_attempt)
            prefix = enrollment.repo_subpath + "/"
            archive = build_solution_package_archive(
                source.files,
                {
                    item.path.removeprefix(prefix): item.mode
                    for item in source.source_files
                },
            )
            return VerifiedSolutionPackageSource(
                source,
                archive,
                json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode(),
                artifact_digest,
            )
    except ValueError as exc:
        if isinstance(exc, GitDeliverySourceError):
            raise
        raise GitDeliverySourceError(
            "Reviewed package source contract is invalid"
        ) from exc
    except TimeoutError as exc:
        raise GitDeliverySourceError("Package source read budget exhausted") from exc
