"""Deterministic invariants for immutable Workspace Live retirement."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bifrost.workspace_release import canonical_digest
from src.models.contracts.workspace_promotions import WorkspaceLiveRetireRequest
from src.services import workspace_release_retirement as retirement_module
from src.services.workspace_release_retirement import (
    WorkspaceReleaseRetirementError,
    WorkspaceReleaseRetirementService,
)
from src.services.workspace_release_runtime import WorkspaceReleaseDescriptor


def _rows():
    organization_id = uuid4()
    artifact_id = uuid4()
    release_row_id = uuid4()
    release_id = "sha256:" + "b" * 64
    governed_manifest_id = "sha256:" + "c" * 64
    artifact = SimpleNamespace(id=artifact_id, release_id=release_id)
    descriptor = WorkspaceReleaseDescriptor(
        release_row_id=release_row_id,
        artifact_id=artifact_id,
        organization_id=organization_id,
        release_id=release_id,
        effective_manifest_id="sha256:" + "d" * 64,
        runtime_storage_prefix=(
            f"_workspace_releases/{organization_id}/"
            f"{release_id.removeprefix('sha256:')}/files/"
        ),
        source_hashes={"workflows/demo.py": "a" * 64},
        governed_paths=("workflows/demo.py",),
        governed_manifest_id=governed_manifest_id,
        effective_registrations={},
        effective_registration_manifest_id="sha256:" + "e" * 64,
        source_commit_sha="1" * 40,
        source_tree_sha="2" * 40,
        registration_state_fingerprint="sha256:" + "f" * 64,
    )
    release = SimpleNamespace(
        id=release_row_id,
        artifact_id=artifact_id,
        organization_id=organization_id,
        activation_state="live",
        retired_at=None,
        retirement_evidence=None,
        attention_deadline=datetime.now(timezone.utc),
    )
    request = WorkspaceLiveRetireRequest(
        expected_release_id=release_id,
        expected_artifact_id=artifact_id,
        governed_manifest_id=governed_manifest_id,
        reason="Emergency rollback of production Live",
        acknowledgement="retire-live-workspace-release",
    )
    return release, artifact, descriptor, request


class _Database:
    def __init__(self):
        self.flush = AsyncMock()
        self.commit = AsyncMock()
        self.refresh = AsyncMock()
        self.rollback = AsyncMock()


def _service(release, artifact, *, live, retired, monkeypatch):
    db = _Database()
    service = WorkspaceReleaseRetirementService(db, release.organization_id)
    service._live_release = AsyncMock(return_value=live)  # type: ignore[method-assign]
    service._retired_release = AsyncMock(return_value=retired)  # type: ignore[method-assign]
    monkeypatch.setattr(
        retirement_module, "acquire_workspace_release_lock", AsyncMock()
    )
    audit = AsyncMock()
    monkeypatch.setattr(retirement_module, "emit_audit", audit)
    return db, service, audit


@pytest.mark.asyncio
async def test_retire_sets_retired_state_evidence_and_audit(monkeypatch) -> None:
    release, artifact, descriptor, request = _rows()
    actor_id = uuid4()
    db, service, audit = _service(
        release, artifact, live=(release, artifact), retired=None, monkeypatch=monkeypatch
    )
    monkeypatch.setattr(
        retirement_module.WorkspaceReleaseDescriptor,
        "from_rows",
        lambda *_args, **_kwargs: descriptor,
    )

    response = await service.retire(request, user_id=actor_id)

    assert release.activation_state == "retired"
    assert release.retired_at is not None
    assert release.attention_deadline is None
    evidence = release.retirement_evidence
    assert evidence["schema_version"] == retirement_module.RETIREMENT_EVIDENCE_SCHEMA
    assert evidence["reason"] == request.reason
    assert evidence["retired_by_user_id"] == str(actor_id)
    assert evidence["governed_manifest_id"] == descriptor.governed_manifest_id
    assert evidence["governed_path_count"] == 1
    without_id = dict(evidence)
    evidence_id = without_id.pop("evidence_id")
    assert evidence_id == canonical_digest(without_id)
    assert response.release_row_id == release.id
    assert response.release_id == descriptor.release_id
    assert response.governed_path_count == 1
    assert response.evidence_id == evidence_id
    db.commit.assert_awaited_once()
    audit.assert_awaited_once()
    assert audit.await_args.args[1] == "workspace_release.retired"
    assert audit.await_args.kwargs["strict"] is True


@pytest.mark.asyncio
async def test_retire_rejects_identity_cas_mismatch(monkeypatch) -> None:
    release, artifact, descriptor, request = _rows()
    db, service, _audit = _service(
        release, artifact, live=(release, artifact), retired=None, monkeypatch=monkeypatch
    )
    monkeypatch.setattr(
        retirement_module.WorkspaceReleaseDescriptor,
        "from_rows",
        lambda *_args, **_kwargs: descriptor,
    )
    mismatched = request.model_copy(
        update={"expected_release_id": "sha256:" + "9" * 64}
    )

    with pytest.raises(WorkspaceReleaseRetirementError, match="identity CAS mismatch"):
        await service.retire(mismatched, user_id=uuid4())

    assert release.activation_state == "live"
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_retire_rejects_governed_manifest_cas_mismatch(monkeypatch) -> None:
    release, artifact, descriptor, request = _rows()
    db, service, _audit = _service(
        release, artifact, live=(release, artifact), retired=None, monkeypatch=monkeypatch
    )
    monkeypatch.setattr(
        retirement_module.WorkspaceReleaseDescriptor,
        "from_rows",
        lambda *_args, **_kwargs: descriptor,
    )
    mismatched = request.model_copy(
        update={"governed_manifest_id": "sha256:" + "9" * 64}
    )

    with pytest.raises(
        WorkspaceReleaseRetirementError, match="governed manifest CAS mismatch"
    ):
        await service.retire(mismatched, user_id=uuid4())

    assert release.activation_state == "live"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_retire_without_live_release_fails(monkeypatch) -> None:
    release, artifact, _descriptor, request = _rows()
    db, service, _audit = _service(
        release, artifact, live=None, retired=None, monkeypatch=monkeypatch
    )

    with pytest.raises(
        WorkspaceReleaseRetirementError, match="no Live Workspace release"
    ):
        await service.retire(request, user_id=uuid4())

    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_retire_already_retired_release_is_idempotent(monkeypatch) -> None:
    release, artifact, _descriptor, request = _rows()
    release.activation_state = "retired"
    release.retired_at = datetime.now(timezone.utc)
    release.retirement_evidence = {
        "schema_version": retirement_module.RETIREMENT_EVIDENCE_SCHEMA,
        "reason": request.reason,
        "governed_path_count": 1,
        "evidence_id": "sha256:" + "7" * 64,
    }
    db, service, _audit = _service(
        release,
        artifact,
        live=None,
        retired=(release, artifact),
        monkeypatch=monkeypatch,
    )

    response = await service.retire(request, user_id=uuid4())

    assert response.release_row_id == release.id
    assert response.release_id == artifact.release_id
    assert response.governed_path_count == 1
    assert response.evidence_id == release.retirement_evidence["evidence_id"]
    db.commit.assert_not_awaited()
