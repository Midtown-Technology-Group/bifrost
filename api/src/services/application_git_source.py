"""App-specific producer authorization over the common protected Git reader."""

from typing import Any
from uuid import UUID

from bifrost.workspace_release import canonical_digest

from src.core.application_delivery_policy import InlineAppGitDeliveryPolicy
from src.services.solutions.github_delivery_source import (
    GitDeliveryIdentity,
    GitDeliverySourceError,
    ProtectedGitReader,
    VerifiedInlineAppSource,
    authenticate_protected_git_producer,
)

APP_GIT_SOURCE_SCHEMA = "bifrost.inline-app-git-source/v1"


def app_delivery_audience(application_id: UUID, commit_sha: str, ci_run_id: int,
                         ci_run_attempt: int, artifact_digest: str) -> str:
    # A Solution producer token can never authorize an App, even for equal UUIDs.
    return (f"bifrost-inline-app-git-delivery/v1:{application_id}:{commit_sha}:"
            f"{ci_run_id}:{ci_run_attempt}:{artifact_digest}")


async def authenticate_app_git_delivery(
    token: str, *, policy: InlineAppGitDeliveryPolicy, application_id: UUID,
    commit_sha: str, ci_run_id: int, ci_run_attempt: int, artifact_digest: str,
    jwks: dict[str, Any] | None = None,
) -> GitDeliveryIdentity:
    try:
        policy.enrollment_for(application_id)
    except ValueError as exc:
        raise GitDeliverySourceError(str(exc)) from exc
    return await authenticate_protected_git_producer(token, policy=policy, commit_sha=commit_sha,
        audience=app_delivery_audience(application_id, commit_sha, ci_run_id, ci_run_attempt,
            artifact_digest), jwks=jwks)


def app_source_evidence(application_id: UUID, policy: InlineAppGitDeliveryPolicy,
                        source: VerifiedInlineAppSource) -> dict[str, Any]:
    enrollment = policy.enrollment_for(application_id)
    if source.repo_subpath != enrollment.repo_subpath:
        raise GitDeliverySourceError("Verified source is outside the enrolled App subtree")
    return {"schema_version": APP_GIT_SOURCE_SCHEMA, "application_id": str(application_id),
        "repository": policy.repository, "repository_id": policy.repository_id,
        "repository_owner_id": policy.repository_owner_id,
        "organization_id": str(enrollment.organization_id) if enrollment.organization_id else None,
        "source_commit_sha": source.commit_sha, "source_tree_sha": source.tree_sha,
        "source_subtree_sha": source.subtree_sha, "repo_subpath": source.repo_subpath,
        "source_hashes": source.snapshot.hashes(), "file_modes": dict(source.file_modes),
        "metadata_mode": "preserve_installed_controls"}


async def read_app_git_source(
    reader: ProtectedGitReader, *, policy: InlineAppGitDeliveryPolicy, application_id: UUID,
    commit_sha: str, ci_run_id: int, ci_run_attempt: int, artifact_digest: str,
) -> VerifiedInlineAppSource:
    """Require current Main CI and actual source bytes before returning a capture."""
    enrollment = policy.enrollment_for(application_id)
    if reader.policy != policy:
        raise GitDeliverySourceError("App source reader is outside the enrolled repository policy")
    await reader.verify_ci(commit_sha, ci_run_id, ci_run_attempt)
    commit = await reader.document(f"git/commits/{commit_sha}")
    tree_sha = commit.get("tree", {}).get("sha")
    if commit.get("sha") != commit_sha or not isinstance(tree_sha, str):
        raise GitDeliverySourceError("App source commit/tree identity differs")
    source = await reader.inline_app_source(commit_sha, enrollment.repo_subpath, tree_sha)
    if canonical_digest(app_source_evidence(application_id, policy, source)) != artifact_digest:
        raise GitDeliverySourceError("App source differs from the producer-bound artifact")
    # Recheck after the full bounded tree/blob read. A merge during that read
    # must never authorize old captured bytes as the newly current Main.
    await reader.verify_ci(commit_sha, ci_run_id, ci_run_attempt)
    return source
