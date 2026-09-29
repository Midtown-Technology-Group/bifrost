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
    return WorkspaceSourceSupersessionEvidence(
        superseding_source_release_id=uuid4(),
        production_readback_id="sha256:" + "a" * 64,
        verified_at=verified_at or datetime.now(UTC),
        paths=paths
        or {
            "features/a.py": WorkspaceSourceSupersessionPath(
                current_git_sha256="b" * 64,
                runtime_owner="solution",
                runtime_source_sha256="c" * 64,
                runtime_ref=str(uuid4()),
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
        == "solution"
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
