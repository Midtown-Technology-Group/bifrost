"""End-to-end coverage for governed Workspace Live release retirement."""

import base64
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from bifrost.workspace_release import (
    workspace_manifest_id,
    workspace_registration_manifest_id,
)
from sqlalchemy import delete
from src.core.constants import PROVIDER_ORG_ID
from src.models.contracts.workspace_promotions import WorkspaceLiveRetireRequest
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.workspace_promotions import (
    WorkspacePromotionArtifact,
    WorkspacePromotionRelease,
)
from src.models.orm.workspace_repo_changesets import WorkspaceRepoChangeset
from src.services.audit_context import ActorContext, clear_actor, set_actor
from src.services.workspace_release_retirement import (
    WorkspaceReleaseRetirementService,
)
from src.services.workspace_release_runtime import (
    PinnedWorkspaceRuntime,
    WorkspaceReleaseDescriptor,
    resolve_pinned_workspace_runtime,
)

_RUNTIME_BOUNDS = {
    "max_duration_seconds": 30,
    "max_external_calls": 10,
    "max_records_read": 100,
    "max_output_bytes": 4096,
}


def _digest() -> str:
    return "sha256:" + uuid4().hex + uuid4().hex


async def _seed_live_release(
    db_session,
    *,
    source_path: str,
    function_name: str,
    user_id: UUID,
) -> tuple[WorkspacePromotionArtifact, WorkspacePromotionRelease, PlatformJob]:
    release_id = _digest()
    source_sha = "a" * 64
    files = {source_path: source_sha}
    registration = {
        "path": source_path,
        "function": function_name,
        "workflow_id": str(uuid4()),
        "type": "workflow",
        "name": "Retirement governed workflow",
        "organization_id": str(PROVIDER_ORG_ID),
        "is_active": True,
        "source_sha256": source_sha,
        "runtime_bounds": dict(_RUNTIME_BOUNDS),
        "access_level": "role_based",
        "role_ids": [],
        "endpoint_enabled": False,
        "public_endpoint": False,
        "api_key_enabled": False,
    }
    registrations = {f"{source_path}::{function_name}": registration}
    manifest = {
        "schema_version": "bifrost.workspace-release-artifact/v1",
        "release_id": release_id,
        "effective_manifest_id": workspace_manifest_id(files),
        "effective_files": files,
        "governed_paths": sorted(files),
        "governed_manifest_id": workspace_manifest_id(files),
        "effective_registration_manifest_id": (
            workspace_registration_manifest_id(registrations)
        ),
        "effective_registrations": registrations,
        "protected_source": {"commit_sha": "1" * 40, "tree_sha": "2" * 40},
        "registration": {"state_fingerprint": _digest()},
        "bounds": dict(_RUNTIME_BOUNDS),
    }
    job = PlatformJob(
        job_type="test.workspace_release_retirement",
        payload_version=1,
        payload={},
        dedupe_key=release_id,
        resource_lock_key=f"workspace-release:{PROVIDER_ORG_ID}",
        organization_id=PROVIDER_ORG_ID,
        requested_by_user_id=str(user_id),
        requested_by_email="retirement-e2e@bifrost.test",
        requested_by_name="Retirement E2E",
        resource_type="workspace_promotion_release",
        resource_id=release_id,
        title="Retirement E2E durable lock job",
        status="succeeded",
    )
    db_session.add(job)
    await db_session.flush()
    artifact = WorkspacePromotionArtifact(
        organization_id=PROVIDER_ORG_ID,
        candidate_id=_digest(),
        content_id=_digest(),
        closure_id=_digest(),
        release_id=release_id,
        base_release_id="repo-v1:" + "0" * 64,
        base_manifest_id=workspace_manifest_id({}),
        effective_manifest_id=manifest["effective_manifest_id"],
        effective_registration_manifest_id=(
            manifest["effective_registration_manifest_id"]
        ),
        registration_intent_fingerprint=_digest(),
        registration_state_fingerprint=manifest["registration"][
            "state_fingerprint"
        ],
        schema_version="bifrost.workspace-promotion-bundle/v2",
        target_kind="workspace",
        entity_type="workflow",
        entry_path=source_path,
        entry_function=function_name,
        snapshot_id=_digest(),
        source_revision="1" * 40,
        source_tree_sha="2" * 40,
        source_artifact_key=f"test/retirement/{uuid4().hex}/source.zip",
        manifest_key=f"test/retirement/{uuid4().hex}/manifest.json",
        manifest=manifest,
        risk_class="R0",
        disposition="eligible",
        artifact_state="eligible",
        policy_version="workspace-release-artifact/2026-08-20",
        created_by=user_id,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db_session.add(artifact)
    await db_session.flush()
    release = WorkspacePromotionRelease(
        organization_id=PROVIDER_ORG_ID,
        artifact_id=artifact.id,
        activation_state="live",
        lock_state="locked",
        lock_in_job_id=job.id,
        created_by=user_id,
    )
    db_session.add(release)
    await db_session.commit()
    await db_session.refresh(artifact)
    await db_session.refresh(release)
    await db_session.refresh(job)
    return artifact, release, job


def _pinned_evidence(
    artifact: WorkspacePromotionArtifact,
    release: WorkspacePromotionRelease,
    source_path: str,
    function_name: str,
) -> tuple[dict, UUID]:
    descriptor = WorkspaceReleaseDescriptor.from_rows(release, artifact)
    registration = descriptor.effective_registrations[
        f"{source_path}::{function_name}"
    ]
    workflow_id = UUID(registration["workflow_id"])
    pinned = PinnedWorkspaceRuntime(
        workflow_id=workflow_id,
        release=descriptor,
        name=registration["name"],
        function_name=registration["function"],
        path=source_path,
        source_hash=registration["source_sha256"],
        timeout_seconds=_RUNTIME_BOUNDS["max_duration_seconds"],
        time_saved=0,
        value=0,
        execution_mode="async",
        workflow_type=registration["type"],
        cache_ttl_seconds=0,
        organization_id=registration["organization_id"],
        runtime_bounds=dict(_RUNTIME_BOUNDS),
    )
    return pinned.queue_evidence(), workflow_id


async def _retire(
    db_session,
    artifact: WorkspacePromotionArtifact,
    release: WorkspacePromotionRelease,
    *,
    user_id: UUID,
    reason: str,
):
    await db_session.refresh(artifact)
    await db_session.refresh(release)
    request = WorkspaceLiveRetireRequest(
        expected_release_id=artifact.release_id,
        expected_artifact_id=artifact.id,
        governed_manifest_id=artifact.manifest["governed_manifest_id"],
        reason=reason,
        acknowledgement="retire-live-workspace-release",
    )
    token = set_actor(
        ActorContext(
            user_id=user_id,
            organization_id=release.organization_id,
            source="http",
        )
    )
    try:
        return await WorkspaceReleaseRetirementService(
            db_session, release.organization_id
        ).retire(request, user_id=user_id)
    finally:
        clear_actor(token)


async def _cleanup(
    db_session,
    *,
    release_id: UUID | None,
    job_id: UUID | None,
    changeset_ids: tuple[UUID, ...] = (),
) -> None:
    await db_session.rollback()
    for changeset_id in changeset_ids:
        await db_session.execute(
            delete(WorkspaceRepoChangeset).where(
                WorkspaceRepoChangeset.id == changeset_id
            )
        )
    if release_id is not None:
        await db_session.execute(
            delete(WorkspacePromotionRelease).where(
                WorkspacePromotionRelease.id == release_id
            )
        )
    if job_id is not None:
        await db_session.execute(delete(PlatformJob).where(PlatformJob.id == job_id))
    await db_session.commit()


@pytest.mark.e2e
async def test_workspace_release_retirement_unblocks_legacy_lane_and_preserves_pins(
    e2e_client,
    platform_admin,
    db_session,
) -> None:
    suffix = uuid4().hex
    scope = f"governed_retirement_{suffix}"
    source_path = f"{scope}/config.json"
    function_name = f"retirement_{suffix}"
    headers = platform_admin.headers
    user_id = platform_admin.user_id
    activity = b'{"enabled": true}\n'
    release_row_id = None
    job_id = None
    seeded_changeset_id = None
    try:
        artifact, release, job = await _seed_live_release(
            db_session,
            source_path=source_path,
            function_name=function_name,
            user_id=user_id,
        )
        release_row_id = release.id
        job_id = job.id
        expected_release_id = artifact.release_id
        pinned_evidence, workflow_id = _pinned_evidence(
            artifact, release, source_path, function_name
        )

        state = e2e_client.get(
            "/api/workspace-repo-changesets/state",
            headers=headers,
            params={"scope": scope},
        )
        assert state.status_code == 200, state.text
        assert state.json()["source_authority"] == "workspace-release-v1"
        assert state.json()["governed_paths"] == [source_path]

        stated = e2e_client.post(
            "/api/workspace-repo-changesets",
            headers=headers,
            json={"scope": scope},
        )
        assert stated.status_code == 201, stated.text
        blocked_stage = e2e_client.post(
            f"/api/workspace-repo-changesets/{stated.json()['id']}/files",
            headers=headers,
            json={
                "path": source_path,
                "operation": "write",
                "content_base64": base64.b64encode(activity).decode(),
            },
        )
        assert blocked_stage.status_code == 422, blocked_stage.text
        assert "governed" in blocked_stage.json()["detail"]

        seeded_changeset = WorkspaceRepoChangeset(
            organization_id=PROVIDER_ORG_ID,
            scope=scope,
            base_revision="0" * 64,
            base_files={},
            mutations=[
                {
                    "path": source_path,
                    "operation": "write",
                    "content_base64": None,
                    "before_hash": None,
                    "after_hash": None,
                    "force_deactivation": False,
                }
            ],
            status="staged",
            created_by=user_id,
        )
        db_session.add(seeded_changeset)
        await db_session.commit()
        seeded_changeset_id = seeded_changeset.id
        blocked_validate = e2e_client.post(
            f"/api/workspace-repo-changesets/{seeded_changeset_id}/validate",
            headers=headers,
        )
        assert blocked_validate.status_code == 422, blocked_validate.text

        reason = "Retire Live after workflow family migration"
        retired = await _retire(
            db_session,
            artifact,
            release,
            user_id=user_id,
            reason=reason,
        )
        assert retired.release_id == expected_release_id
        assert retired.governed_path_count == 1
        assert retired.evidence_id

        live = e2e_client.get("/api/workspace-promotions/live", headers=headers)
        assert live.status_code == 200, live.text
        assert live.json()["state"] == "retired"
        assert live.json()["active_release"] is None
        assert live.json()["retirement_reason"] == reason

        released_state = e2e_client.get(
            "/api/workspace-repo-changesets/state",
            headers=headers,
            params={"scope": scope},
        )
        assert released_state.status_code == 200, released_state.text
        assert released_state.json()["source_authority"] == "repo-v1"
        assert released_state.json()["governed_paths"] == []

        unblocked = e2e_client.post(
            "/api/workspace-repo-changesets",
            headers=headers,
            json={"scope": scope},
        )
        assert unblocked.status_code == 201, unblocked.text
        allowed_stage = e2e_client.post(
            f"/api/workspace-repo-changesets/{unblocked.json()['id']}/files",
            headers=headers,
            json={
                "path": source_path,
                "operation": "write",
                "content_base64": base64.b64encode(activity).decode(),
            },
        )
        assert allowed_stage.status_code == 200, allowed_stage.text
        allowed_validate = e2e_client.post(
            f"/api/workspace-repo-changesets/{unblocked.json()['id']}/validate",
            headers=headers,
        )
        assert allowed_validate.status_code == 200, allowed_validate.text
        assert allowed_validate.json()["valid"] is True

        resolved = await resolve_pinned_workspace_runtime(
            db_session, pinned_evidence, workflow_id
        )
        assert resolved.queue_evidence() == pinned_evidence
        assert resolved.release.release_id == expected_release_id
    finally:
        await _cleanup(
            db_session,
            release_id=release_row_id,
            job_id=job_id,
            changeset_ids=(seeded_changeset_id,) if seeded_changeset_id else (),
        )


@pytest.mark.e2e
async def test_workspace_release_retirement_emits_audit_event(
    e2e_client,
    platform_admin,
    db_session,
) -> None:
    suffix = uuid4().hex
    scope = f"governed_retirement_audit_{suffix}"
    source_path = f"{scope}/config.json"
    function_name = f"audit_{suffix}"
    release_row_id = None
    job_id = None
    try:
        artifact, release, job = await _seed_live_release(
            db_session,
            source_path=source_path,
            function_name=function_name,
            user_id=platform_admin.user_id,
        )
        release_row_id = release.id
        job_id = job.id

        reason = "Audited retirement of governed Live"
        retired = await _retire(
            db_session,
            artifact,
            release,
            user_id=platform_admin.user_id,
            reason=reason,
        )

        audit = e2e_client.get(
            "/api/audit",
            headers=platform_admin.headers,
            params={"action": "workspace_release.retired", "limit": 200},
        )
        assert audit.status_code == 200, audit.text
        matching = [
            entry
            for entry in audit.json()["entries"]
            if entry["action"] == "workspace_release.retired"
            and entry.get("resource_id") == str(release_row_id)
        ]
        assert matching, audit.json()
        entry = matching[0]
        assert entry["outcome"] == "success"
        assert entry["details"]["release_id"] == retired.release_id
        assert entry["details"]["reason"] == reason
        assert entry["details"]["governed_path_count"] == 1
        assert entry["details"]["retirement_evidence_id"] == retired.evidence_id
    finally:
        await _cleanup(db_session, release_id=release_row_id, job_id=job_id)
