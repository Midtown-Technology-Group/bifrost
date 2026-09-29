"""Old source obligations need later release and fresh per-path readback."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.models.contracts.workspace_promotions import (
    WorkspaceSourceReleaseDispositionRequest,
    WorkspaceSourceSupersessionEvidence,
    WorkspaceSourceSupersessionPath,
)
from src.services import workspace_source_releases
from src.services.workspace_source_releases import (
    WorkspaceSourceReleaseConflict,
    WorkspaceSourceReleaseService,
)


def _evidence(*, paths=None, verified_at=None):
    release_id = "sha256:" + "e" * 64
    return WorkspaceSourceSupersessionEvidence(
        superseding_source_release_id=uuid4(),
        production_readback_id="sha256:" + "a" * 64,
        verified_at=verified_at or datetime.now(UTC),
        paths=paths
        or {
            "features/a.py": WorkspaceSourceSupersessionPath(
                current_git_sha256="b" * 64,
                runtime_owner="workspace",
                runtime_source_sha256="c" * 64,
                runtime_ref=release_id,
            )
        },
    )


def _records(evidence):
    now = datetime.now(UTC)
    old = SimpleNamespace(
        id=uuid4(),
        source_commit_sha="1" * 40,
        paths={"features/a.py": "2" * 64},
        disposition="attention_required",
        reason="old source not deployed",
        completion_evidence=None,
        created_at=now - timedelta(days=2),
        resolved_at=None,
    )
    later = SimpleNamespace(
        id=evidence.superseding_source_release_id,
        source_commit_sha="3" * 40,
        disposition="released",
        completion_evidence={
            "schema_version": "bifrost.workspace-source-release-completion/v1",
            "evidence_id": "sha256:" + "d" * 64,
            "workspace_release_id": "sha256:" + "e" * 64,
            "runtime_sha256": {"features/a.py": "c" * 64},
        },
        created_at=now - timedelta(days=1),
    )
    return old, later


@pytest.mark.asyncio
async def test_supersession_records_immutable_later_release_and_path_readback(
    monkeypatch,
):
    evidence = _evidence()
    old, later = _records(evidence)
    db = AsyncMock()
    service = WorkspaceSourceReleaseService(db, uuid4())
    service._get = AsyncMock(side_effect=[old, later, old, later])
    monkeypatch.setattr(
        workspace_source_releases,
        "active_workspace_release",
        AsyncMock(
            return_value=SimpleNamespace(
                release_id="sha256:" + "e" * 64,
                source_hashes={"features/a.py": "c" * 64},
            )
        ),
    )
    monkeypatch.setattr(
        workspace_source_releases, "source_release_response", lambda record: record
    )

    result = await service.set_manual_disposition(
        old.id,
        disposition="superseded",
        reason="Later reviewed source owns the production behavior",
        supersession_evidence=evidence,
    )

    assert result is old
    assert old.disposition == "superseded"
    assert old.completion_evidence["schema_version"] == (
        "bifrost.workspace-source-release-supersession/v1"
    )
    assert (
        old.completion_evidence["superseding_source_commit_sha"]
        == later.source_commit_sha
    )
    assert (
        old.completion_evidence["review"]["paths"]["features/a.py"]["runtime_owner"]
        == "workspace"
    )
    assert old.completion_evidence["evidence_id"].startswith("sha256:")
    db.commit.assert_awaited_once()

    replay = await service.set_manual_disposition(
        old.id,
        disposition="superseded",
        reason="Later reviewed source owns the production behavior",
        supersession_evidence=evidence,
    )
    assert replay is old
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_supersession_rejects_changed_live_pointer(monkeypatch):
    evidence = _evidence()
    old, later = _records(evidence)
    db = AsyncMock()
    service = WorkspaceSourceReleaseService(db, uuid4())
    service._get = AsyncMock(side_effect=[old, later])
    monkeypatch.setattr(
        workspace_source_releases,
        "active_workspace_release",
        AsyncMock(
            return_value=SimpleNamespace(
                release_id="sha256:" + "f" * 64,
                source_hashes={"features/a.py": "c" * 64},
            )
        ),
    )

    with pytest.raises(WorkspaceSourceReleaseConflict, match="Workspace runtime"):
        await service.set_manual_disposition(
            old.id,
            disposition="superseded",
            reason="Later source replaced old source",
            supersession_evidence=evidence,
        )
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", [None, "pointer", "source_hash", "stale_deployment"])
async def test_supersession_checks_active_reviewed_solution_runtime(
    monkeypatch, defect
):
    deployment_id = uuid4()
    source_hash = "c" * 64
    evidence = WorkspaceSourceSupersessionEvidence(
        superseding_solution_deployment_ids=[deployment_id],
        production_readback_id="sha256:" + "a" * 64,
        verified_at=datetime.now(UTC),
        paths={
            "features/a.py": WorkspaceSourceSupersessionPath(
                current_git_sha256="b" * 64,
                runtime_owner="solution",
                runtime_source_sha256=source_hash,
                runtime_ref=str(deployment_id),
            )
        },
    )
    old, _later = _records(evidence)
    organization_id = uuid4()
    solution_id = uuid4()
    deployment = SimpleNamespace(
        id=deployment_id,
        organization_id=organization_id,
        solution_id=solution_id,
        state="active",
        activated_at=(
            old.created_at - timedelta(days=1)
            if defect == "stale_deployment"
            else datetime.now(UTC)
        ),
        validation_result={"schema_version": "bifrost.workspace-live-handoff/v1"},
        compiled_manifest={},
        resolution_map={},
        dependencies=[],
        compiled_manifest_hash="sha256:" + "d" * 64,
        resolution_map_hash="sha256:" + "e" * 64,
    )
    repository = SimpleNamespace(
        get_by_id_for_runtime=AsyncMock(return_value=deployment)
    )
    monkeypatch.setattr(
        workspace_source_releases,
        "SolutionDeploymentRepository",
        lambda _db: repository,
    )
    monkeypatch.setattr(
        workspace_source_releases,
        "validate_runtime_closure",
        lambda *_args, **_kwargs: (
            object(),
            SimpleNamespace(
                sources={
                    "features/a.py": SimpleNamespace(
                        content_hash="sha256:" + source_hash
                    )
                }
            ),
        ),
    )
    monkeypatch.setattr(
        workspace_source_releases, "source_release_response", lambda record: record
    )
    db = AsyncMock()
    db.scalar = AsyncMock(
        return_value=SimpleNamespace(
            id=solution_id,
            status="active",
            active_deployment_id=(uuid4() if defect == "pointer" else deployment_id),
        )
    )
    service = WorkspaceSourceReleaseService(db, organization_id)
    service._get = AsyncMock(return_value=old)

    if defect == "source_hash":
        evidence.paths["features/a.py"].runtime_source_sha256 = "f" * 64

    async def decide():
        return await service.set_manual_disposition(
            old.id,
            disposition="superseded",
            reason="Reviewed Solution runtime replaced the old loose source",
            supersession_evidence=evidence,
        )

    if defect:
        with pytest.raises(WorkspaceSourceReleaseConflict):
            await decide()
        db.commit.assert_not_awaited()
        return
    result = await decide()

    assert result.disposition == "superseded"
    assert result.completion_evidence["review"]["paths"]["features/a.py"][
        "runtime_ref"
    ] == str(deployment_id)
    assert db.scalar.await_args.args[0]._for_update_arg is not None
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["missing_path", "unreleased", "stale_readback"])
async def test_supersession_rejects_incomplete_proof(monkeypatch, defect):
    evidence = _evidence(
        paths={
            "features/other.py": WorkspaceSourceSupersessionPath(
                runtime_owner="removed"
            )
        }
        if defect == "missing_path"
        else None,
        verified_at=datetime.now(UTC) - timedelta(days=2)
        if defect == "stale_readback"
        else None,
    )
    old, later = _records(evidence)
    if defect == "unreleased":
        later.disposition = "attention_required"
    db = AsyncMock()
    service = WorkspaceSourceReleaseService(db, uuid4())
    service._get = AsyncMock(side_effect=[old, later])
    monkeypatch.setattr(
        workspace_source_releases, "source_release_response", lambda record: record
    )

    with pytest.raises(WorkspaceSourceReleaseConflict):
        await service.set_manual_disposition(
            old.id,
            disposition="superseded",
            reason="Later review",
            supersession_evidence=evidence,
        )
    db.commit.assert_not_awaited()


def test_supersession_request_requires_evidence_and_active_runtime_hash():
    with pytest.raises(ValidationError):
        WorkspaceSourceReleaseDispositionRequest(
            disposition="superseded", reason="Later release"
        )
    with pytest.raises(ValidationError):
        WorkspaceSourceSupersessionPath(runtime_owner="solution")


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["workspace", "solution"])
async def test_removed_disposition_rejects_source_still_in_immutable_runtime(monkeypatch, owner):
    evidence = _evidence(paths={"features/a.py": WorkspaceSourceSupersessionPath(runtime_owner="removed")})
    old, later = _records(evidence)
    db = AsyncMock()
    db.scalar.return_value = uuid4() if owner == "solution" else None
    service = WorkspaceSourceReleaseService(db, uuid4())
    service._get = AsyncMock(side_effect=[old, later])
    monkeypatch.setattr(
        workspace_source_releases, "active_workspace_release",
        AsyncMock(return_value=SimpleNamespace(source_hashes={"features/a.py": "c" * 64} if owner == "workspace" else {})),
    )
    with pytest.raises(WorkspaceSourceReleaseConflict, match="still present"):
        await service.set_manual_disposition(
            old.id, disposition="superseded", reason="Reviewed removal", supersession_evidence=evidence,
        )
    db.commit.assert_not_awaited()
