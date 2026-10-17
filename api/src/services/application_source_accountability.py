"""Read original App publication evidence into the existing source ledger.

Immutable Git metadata is read before aggregate locks. The final fence verifies
the original job, every compiled output, installed controls and current source.
This adapter cannot build, publish, enqueue, change controls or clear an intent.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from bifrost.workspace_release import canonical_digest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.application_delivery_policy import InlineAppGitDeliveryPolicy
from src.core.constants import SYSTEM_USER_UUID
from src.core.solution_delivery_policy import SolutionGitDeliveryPolicy, delivery_path, reviewed_package_registry
from src.models.contracts.applications import ApplicationGitPublicationInput, ApplicationGitSourcePublicationRequest
from src.models.orm.applications import Application
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.workspace_promotions import WorkspaceSourceRelease
from src.services.app_storage import AppStorageService
from src.services.application_git_delivery import app_git_job_id
from src.services.application_publication import publication_controls_hash
from src.services.application_publication_evidence import read_app_publication_runtime_pin
from src.services.github_config import get_github_config
from src.services.github_actions_oidc import workspace_source_release_tracking_organization_id
from src.services.solution_source_accountability import AppSourceConsumer
from src.services.solutions.github_delivery_source import (
    MAX_METADATA_BYTES,
    GitDeliveryIdentity,
    GitDeliverySourceError,
    ProtectedGitReader,
    _reviewed_recipe,
    _unique_json_object,
)

REGISTRY_PATH = "config/solution-delivery/installations.json"
APP_RECIPE_SCHEMA = "bifrost.inline-app-delivery/v1"


def _same_repository(app_policy: InlineAppGitDeliveryPolicy, policy: SolutionGitDeliveryPolicy) -> bool:
    fields = ("repository", "repository_id", "repository_owner_id", "organization_id",
        "workflow_path", "ci_workflow_path", "ci_workflow_id")
    return all(getattr(app_policy, name) == getattr(policy, name) for name in fields)


async def read_app_accounting_metadata(
    reader: ProtectedGitReader, *, policy: SolutionGitDeliveryPolicy,
    app_policy: InlineAppGitDeliveryPolicy, application_id: UUID, source: dict[str, Any],
) -> dict[str, Any]:
    """Bind recipe/registry bytes to the exact published protected root tree.

    The historical publication already proved its admission CI. Recovery reads
    its immutable commit, rather than pretending it is still today's Main.
    """
    if not _same_repository(app_policy, policy) or reader.policy != app_policy:
        raise GitDeliverySourceError("App accounting repository/producer policy differs")
    enrollment = app_policy.enrollment_for(application_id)
    commit_sha, tree_sha = source.get("source_commit_sha"), source.get("source_tree_sha")
    if (any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None
            for value in (commit_sha, tree_sha))
            or source.get("repository") != policy.repository
            or source.get("repository_id") != policy.repository_id
            or source.get("repository_owner_id") != policy.repository_owner_id
            or source.get("application_id") != str(application_id)
            or source.get("organization_id") != (str(enrollment.organization_id) if enrollment.organization_id else None)
            or source.get("repo_subpath") != enrollment.repo_subpath):
        raise GitDeliverySourceError("App accounting source identity differs")
    commit = await reader.document(f"git/commits/{commit_sha}")
    if commit.get("sha") != commit_sha or commit.get("tree", {}).get("sha") != tree_sha:
        raise GitDeliverySourceError("App accounting commit/tree identity differs")
    tree = await reader.document(f"git/trees/{tree_sha}?recursive=1")
    entries = tree.get("tree")
    if tree.get("sha") != tree_sha or tree.get("truncated") is not False or not isinstance(entries, list):
        raise GitDeliverySourceError("App accounting root tree is incomplete")
    index = {}
    for entry in entries:
        if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str)
                or entry["path"] in index):
            raise GitDeliverySourceError("App accounting root paths are ambiguous")
        delivery_path(entry["path"])
        index[entry["path"]] = entry
    total = 0

    async def metadata(path: str) -> bytes:
        nonlocal total
        entry = index.get(path, {})
        if (entry.get("type") != "blob" or entry.get("mode") not in {"100644", "100755"}
                or type(entry.get("size")) is not int or not 0 <= entry["size"] <= 128 * 1024):
            raise GitDeliverySourceError("App accounting requires bounded regular metadata files")
        total += entry["size"]
        if total > MAX_METADATA_BYTES:
            raise GitDeliverySourceError("App accounting metadata exceeds its total bound")
        return await reader.blob(entry, limit=128 * 1024)

    raw_registry = await metadata(REGISTRY_PATH)
    rows = reviewed_package_registry(json.loads(raw_registry, object_pairs_hook=_unique_json_object))
    targets = [target for target in ("production", "canary")
        if {row["recipe"] for row in rows if row["target"] == target and row["kind"] == "solution"}
        == set(policy.solutions.values())]
    if len(targets) != 1:
        raise GitDeliverySourceError("App accounting has no unique configured registry target")
    target = targets[0]
    installations = {}
    for identity, path in policy.solutions.items():
        _reviewed_recipe(await metadata(path), identity)
        scope = policy.organization_id_for(identity)
        installations[str(identity)] = {"recipe_path": path,
            "organization_id": str(scope) if scope is not None else None}
    app_rows = [row for row in rows if row["target"] == target and row["kind"] == "inline_app"]
    expected_apps = {}
    selected_path, selected_raw = None, None
    for row in app_rows:
        raw = await metadata(row["recipe"])
        recipe = json.loads(raw, object_pairs_hook=_unique_json_object)
        if (not isinstance(recipe, dict)
                or set(recipe) != {"schema_version", "application_id", "organization_id", "repo_subpath"}
                or recipe.get("schema_version") != APP_RECIPE_SCHEMA):
            raise GitDeliverySourceError("App accounting recipe is invalid")
        identity = UUID(recipe["application_id"])
        scope = UUID(recipe["organization_id"]) if recipe["organization_id"] is not None else None
        if str(identity) != recipe["application_id"] or scope is not None and str(scope) != recipe["organization_id"]:
            raise GitDeliverySourceError("App accounting recipe identities must be canonical")
        enrolled = app_policy.enrollment_for(identity)
        if (identity in expected_apps or scope != enrolled.organization_id
                or recipe["repo_subpath"] != enrolled.repo_subpath):
            raise GitDeliverySourceError("App accounting registry retargets an enrolled App")
        expected_apps[identity] = row["recipe"]
        if identity == application_id:
            selected_path, selected_raw = row["recipe"], raw
    if set(expected_apps) != set(app_policy.applications) or selected_path is None or selected_raw is None:
        raise GitDeliverySourceError("App accounting registry differs from the complete App allowlist")
    return {"commit_sha": commit_sha, "tree_sha": tree_sha,
        "artifact_digest": source["artifact_digest"], "recipe_path": selected_path,
        "control_hashes": {selected_path: hashlib.sha256(selected_raw).hexdigest(),
            REGISTRY_PATH: hashlib.sha256(raw_registry).hexdigest()},
        "package_registry_requirements": {"path": REGISTRY_PATH, "target": target,
            "installations": installations, "application_recipes": sorted(expected_apps.values())}}


@dataclass(frozen=True)
class PreparedAppAccounting:
    application_id: UUID
    publication_job_id: UUID
    organization_id: UUID | None
    repo_subpath: str
    original_evidence_hash: str
    runtime_pin: dict[str, Any]
    proof: dict[str, Any]


async def _latest_publication(db: AsyncSession, application_id: UUID) -> PlatformJob | None:
    # A later manual or uncertain publication invalidates an older Git anchor.
    return (await db.execute(select(PlatformJob).where(PlatformJob.job_type == "application.publish",
        PlatformJob.resource_type == "application", PlatformJob.resource_id == str(application_id))
        .order_by(PlatformJob.created_at.desc(), PlatformJob.id.desc()).limit(1)
        .execution_options(populate_existing=True))).scalar_one_or_none()


async def prepare_app_accounting(db: AsyncSession, policy: SolutionGitDeliveryPolicy) -> list[PreparedAppAccounting]:
    """Omit unproven Apps; they leave their own paths/registry debt outstanding."""
    app_policy = get_settings().inline_app_git_delivery_policy
    if app_policy is None or not _same_repository(app_policy, policy):
        return []
    config = await get_github_config(db, app_policy.organization_id)
    if (config is None or not config.token or config.repo_url not in {
            f"https://github.com/{policy.repository}", f"https://github.com/{policy.repository}.git"}):
        return []
    applications = (await db.scalars(select(Application).where(Application.id.in_(list(app_policy.applications)),
        Application.solution_id.is_(None), Application.app_model == "inline_v1",
        Application.published_at.is_not(None)).order_by(Application.id))).all()
    prepared = []
    ancestry: dict[str, tuple[str, ...]] = {}
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        reader = ProtectedGitReader(app_policy, config.token, client)
        for application in applications:
            job = await _latest_publication(db, application.id)
            try:
                enrollment = app_policy.enrollment_for(application.id)
                if (job is None or job.status != "succeeded" or not application.published_snapshot
                        or application.organization_id != enrollment.organization_id
                        or application.repo_path != enrollment.repo_subpath
                        or job.organization_id != enrollment.organization_id
                        or job.requested_by_user_id != str(SYSTEM_USER_UUID)
                        or not isinstance(job.payload, dict)
                        or job.payload.get("application_id") != str(application.id)
                        or not isinstance(job.result, dict) or not job.result.get("publication_verified")):
                    continue
                protected = ApplicationGitPublicationInput.model_validate(job.payload["protected_git"])
                identity = app_git_job_id(application.id,
                    ApplicationGitSourcePublicationRequest.model_validate({key: getattr(protected, key)
                        for key in ApplicationGitSourcePublicationRequest.model_fields}),
                    GitDeliveryIdentity(protected.producer_run_id, protected.producer_run_attempt))
                if job.id != identity:
                    continue
                pin = job.result["runtime_pin"]
                proof = await read_app_accounting_metadata(reader, policy=policy,
                    app_policy=app_policy, application_id=application.id, source=pin["source"])
                commit = protected.source_commit_sha
                if commit not in ancestry:
                    ancestry[commit] = await reader.verified_ancestors(commit, None)
                proof["ancestor_commit_shas"] = list(ancestry[commit])
                prepared.append(PreparedAppAccounting(application.id, job.id, enrollment.organization_id,
                    enrollment.repo_subpath, canonical_digest({"payload": job.payload, "result": job.result}), pin, proof))
            except (ValueError, KeyError, TypeError):
                # Partial evidence never completes this App or the shared registry.
                continue
    return prepared


async def verify_app_accounting(db: AsyncSession, prepared: list[PreparedAppAccounting]) -> list[AppSourceConsumer]:
    """Final exact row/control fence plus full storage readback; no writes."""
    if not prepared:
        return []
    applications = (await db.scalars(select(Application).where(
        Application.id.in_([item.application_id for item in prepared])).order_by(Application.id)
        .with_for_update().execution_options(populate_existing=True))).all()
    current = {row.id: row for row in applications}
    consumers = []
    storage = AppStorageService()
    for item in prepared:
        application = current.get(item.application_id)
        job = await _latest_publication(db, item.application_id)
        try:
            if (application is None or application.organization_id != item.organization_id
                    or application.repo_path != item.repo_subpath or application.solution_id is not None
                    or application.app_model != "inline_v1" or not application.published_snapshot
                    or application.published_at is None or job is None or job.id != item.publication_job_id
                    or job.status != "succeeded" or job.organization_id != item.organization_id
                    or job.requested_by_user_id != str(SYSTEM_USER_UUID)
                    or canonical_digest({"payload": job.payload, "result": job.result}) != item.original_evidence_hash):
                continue
            intent = job.result["publication_intent"]
            if set(application.published_snapshot) != set(intent["artifact_hashes"]):
                continue
            if await publication_controls_hash(db, application.id, lock=True) != intent["controls_hash"]:
                continue
            await storage.verify_publication(str(application.id), intent)
            pin = await read_app_publication_runtime_pin(storage, application_id=application.id,
                publication_job_id=job.id, organization_id=item.organization_id,
                intent=intent, protected_git=job.payload["protected_git"])
            if (pin != item.runtime_pin or pin["source"]["repo_subpath"] != item.repo_subpath
                    or pin["source"]["source_commit_sha"] != item.proof["commit_sha"]
                    or pin["source"]["source_tree_sha"] != item.proof["tree_sha"]
                    or pin["source"]["artifact_digest"] != item.proof["artifact_digest"]):
                continue
            sources = {f"{item.repo_subpath}/{path}": {path: digest.removeprefix("sha256:")}
                for path, digest in pin["source"]["source_hashes"].items()}
            consumers.append(AppSourceConsumer(str(application.id), str(job.id),
                str(item.organization_id) if item.organization_id else None, pin["manifest_hash"],
                pin["runtime_pin_hash"], pin["output_hashes"], sources, item.proof))
        except (ValueError, KeyError, TypeError):
            continue
    return consumers


def app_accounting_readback(record: Any, pin: dict[str, Any]) -> dict[str, Any]:
    """Project existing ledger proof without changing the original job or ledger."""
    source = pin["source"]
    result = {"schema_version": "bifrost.inline-app-source-accounting/v1",
        "application_id": pin["application_id"], "publication_job_id": pin["publication_job_id"],
        "runtime_pin_hash": pin["runtime_pin_hash"], "source_commit_sha": source["source_commit_sha"],
        "source_tree_sha": source["source_tree_sha"], "source_release_id": str(record.id) if record else None,
        "verified": False}
    if (record is None or record.declaration_actor != "github_actions_oidc"
            or record.source_commit_sha != source["source_commit_sha"]
            or record.source_tree_sha != source["source_tree_sha"]):
        return {**result, "state": "declaration_unproven"}
    prefix = source["repo_subpath"] + "/"
    paths = {path: digest for path, digest in (record.paths or {}).items()
        if path.startswith(prefix) or path.startswith("config/app-delivery/") or path == REGISTRY_PATH}
    if not paths:
        return {**result, "verified": True, "state": "no_app_source_obligation"}
    evidence = record.completion_evidence or {}
    if (record.disposition != "released" or record.resolved_at is None
            or evidence.get("schema_version") != "bifrost.package-owned-source-completion/v1"
            or evidence.get("source_commit_sha") != record.source_commit_sha
            or evidence.get("source_tree_sha") != record.source_tree_sha):
        return {**result, "state": "accounting_pending"}
    if evidence.get("evidence_id") != canonical_digest({key: value for key, value in evidence.items()
            if key != "evidence_id"}):
        return {**result, "state": "accounting_evidence_differs"}
    for path, digest in paths.items():
        entry = evidence.get("paths", {}).get(path) or {}
        anchors = entry.get("protected_git_anchors", [])
        if entry.get("sha256") != digest:
            return {**result, "state": "accounting_evidence_differs"}
        if path.startswith("config/app-delivery/") and not any(
                item.get("application_id") == pin["application_id"] for item in anchors):
            # Another App's recipe must have its own publication evidence. Do
            # not label it as this App's obligation or expose its private data.
            if not any(item.get("kind") == "inline_app" for item in anchors):
                return {**result, "state": "accounting_evidence_differs"}
            continue
        if not any(item.get("kind") == "inline_app"
                and item.get("application_id") == pin["application_id"]
                and item.get("publication_job_id") == pin["publication_job_id"]
                and item.get("runtime_pin_hash") == pin["runtime_pin_hash"] for item in anchors):
            return {**result, "state": "accounting_evidence_differs"}
        if path.startswith(prefix) and source["source_hashes"].get(path[len(prefix):]) != "sha256:" + str(digest):
            return {**result, "state": "accounting_evidence_differs"}
    return {**result, "verified": True, "state": "accounted", "evidence_id": evidence.get("evidence_id")}


async def read_app_source_accounting(db: AsyncSession, pin: dict[str, Any]) -> dict[str, Any]:
    """Read-only status on the already authenticated original App publication."""
    settings = get_settings()
    app_policy = settings.inline_app_git_delivery_policy
    organization_id = workspace_source_release_tracking_organization_id(settings)
    if (app_policy is None or organization_id is None
            or settings.workspace_source_release_oidc_repository != app_policy.repository
            or settings.workspace_source_release_oidc_repository_id != app_policy.repository_id
            or settings.workspace_source_release_oidc_repository_owner_id != app_policy.repository_owner_id):
        return app_accounting_readback(None, pin)
    source = pin["source"]
    record = (await db.execute(select(WorkspaceSourceRelease).where(
        WorkspaceSourceRelease.organization_id == organization_id,
        WorkspaceSourceRelease.source_commit_sha == source["source_commit_sha"],
        WorkspaceSourceRelease.source_tree_sha == source["source_tree_sha"])
        .execution_options(populate_existing=True))).scalar_one_or_none()
    return app_accounting_readback(record, pin)
