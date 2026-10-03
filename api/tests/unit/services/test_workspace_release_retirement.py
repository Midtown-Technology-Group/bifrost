"""Deterministic invariants for immutable Workspace Live retirement."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from bifrost.workspace_release import canonical_digest
from src.models.contracts.workspace_promotions import (
    WorkspaceLiveRetireRequest,
    WorkspaceRegistrationRetirementReview,
)
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
        lock_state="locked",
        retired_at=None,
        retirement_evidence=None,
        attention_deadline=datetime.now(UTC),
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
    service._require_no_loose_consumers = AsyncMock()  # type: ignore[method-assign]
    service._require_resolved_source_obligations = AsyncMock()  # type: ignore[method-assign]
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
    release.retired_at = datetime.now(UTC)
    release.retirement_evidence = {
        "schema_version": retirement_module.RETIREMENT_EVIDENCE_SCHEMA,
        "reason": request.reason,
        "governed_manifest_id": _descriptor.governed_manifest_id,
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


@pytest.mark.asyncio
async def test_retire_idempotent_retry_rejects_wrong_identity(monkeypatch) -> None:
    release, artifact, descriptor, request = _rows()
    release.activation_state = "retired"
    release.retired_at = datetime.now(UTC)
    release.retirement_evidence = {
        "schema_version": retirement_module.RETIREMENT_EVIDENCE_SCHEMA,
        "reason": request.reason,
        "governed_manifest_id": descriptor.governed_manifest_id,
        "governed_path_count": 1,
        "evidence_id": "sha256:" + "7" * 64,
    }
    _db, service, _audit = _service(
        release,
        artifact,
        live=None,
        retired=(release, artifact),
        monkeypatch=monkeypatch,
    )
    mismatched = request.model_copy(
        update={"governed_manifest_id": "sha256:" + "9" * 64}
    )

    with pytest.raises(WorkspaceReleaseRetirementError):
        await service.retire(mismatched, user_id=uuid4())


@pytest.mark.asyncio
async def test_inventory_refuses_truncated_registration_readback(monkeypatch) -> None:
    release, artifact, descriptor, _request = _rows()
    db, service, audit = _service(release, artifact, live=(release, artifact),
                                 retired=None, monkeypatch=monkeypatch)
    monkeypatch.setattr(retirement_module.WorkspaceReleaseDescriptor, "from_rows",
                        lambda *_args, **_kwargs: descriptor)
    db.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [None] * 1001))
    with pytest.raises(WorkspaceReleaseRetirementError, match="readback bound"):
        await service.inspect()
    db.commit.assert_not_awaited()
    db.flush.assert_not_awaited()
    audit.assert_not_awaited()


def _obsolete_fixture():
    from src.models.orm.workflows import Workflow
    from src.services.workflow_registration_retirement import (
        workflow_retirement_snapshot_hash,
    )

    row = Workflow(id=uuid4(), name="Obsolete", function_name="run", path="workflows/demo.py",
        organization_id=None, solution_id=None, is_active=True, roles=[],
        endpoint_enabled=True, public_endpoint=False, api_key_enabled=True,
        access_level="role_based", timeout_seconds=60, execution_mode="sync",
        time_saved=0, value=0, cache_ttl_seconds=0, parameters_schema={},
        display_name=None, description=None, category="General", tags=[],
        allowed_methods=["POST"], disable_global_key=True, retry_policy=None,
        tool_description=None, retirement_evidence=None)
    return row, {
        "workflow_id": row.id,
        "expected_registration_hash": workflow_retirement_snapshot_hash(row),
        "expected_consumer_inventory_digest": "sha256:" + "6" * 64,
        "reviewed_external_callers_digest": "sha256:" + "7" * 64,
        "review_reference": "Reviewed production caller closure and App dist receipt",
        "reason": "Removed obsolete registration; preserve its history and source",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["registration", "consumers", "native_callers", "accepted_work", "outside"])
async def test_obsolete_retirement_refuses_drift_callers_work_or_wrong_cohort(monkeypatch, drift):
    release, artifact, descriptor, request = _rows()
    db, service, audit = _service(release, artifact, live=(release, artifact),
                                 retired=None, monkeypatch=monkeypatch)
    row, review = _obsolete_fixture()
    if drift == "registration":
        row.name = "Changed after review"
    request = request.model_copy(update={"obsolete_registrations": [
        WorkspaceRegistrationRetirementReview(**review)
    ]})
    inventory = {"inventory_digest": "sha256:" + ("8" if drift == "consumers" else "6") * 64,
                 "native_callers": [{"id": "caller"}] if drift == "native_callers" else [],
                 "accepted_work": [{"id": "work"}] if drift == "accepted_work" else []}
    db.execute = AsyncMock()
    db.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [] if drift == "outside" else [row]))
    monkeypatch.setattr(retirement_module, "inspect_workflow_retirement_consumers", AsyncMock(return_value=inventory))
    monkeypatch.setattr(retirement_module.WorkspaceReleaseDescriptor, "from_rows", lambda *_args: descriptor)
    with pytest.raises(WorkspaceReleaseRetirementError):
        await service.retire(request, user_id=uuid4())
    assert row.is_active is True and row.retirement_evidence is None
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_reviewed_obsolete_retirement_seals_uuid_and_retains_credentials(monkeypatch):
    from src.services.workflow_registration_retirement import (
        validate_workflow_retirement_evidence,
    )

    release, artifact, descriptor, request = _rows()
    db, service, audit = _service(release, artifact, live=(release, artifact),
                                 retired=None, monkeypatch=monkeypatch)
    row, review = _obsolete_fixture()
    row.api_key_hash = "retained-credential"
    request = request.model_copy(update={"obsolete_registrations": [WorkspaceRegistrationRetirementReview(**review)]})
    db.execute = AsyncMock()
    db.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [row]))
    monkeypatch.setattr(retirement_module, "inspect_workflow_retirement_consumers",
        AsyncMock(return_value={"inventory_digest": review["expected_consumer_inventory_digest"],
                                "native_callers": [], "accepted_work": []}))
    monkeypatch.setattr(retirement_module.WorkspaceReleaseDescriptor, "from_rows", lambda *_args: descriptor)
    response = await service.retire(request, user_id=uuid4())
    marker = validate_workflow_retirement_evidence(row)
    assert row.id == review["workflow_id"] and row.api_key_hash == "retained-credential"
    assert row.is_active is False and row.endpoint_enabled is False and row.api_key_enabled is False
    assert marker["before_registration_hash"] == review["expected_registration_hash"]
    assert marker["artifact_id"] == str(artifact.id)
    assert "retained-credential" not in str(marker)
    assert release.retirement_evidence["retired_registration_evidence"] == [
        {"workflow_id": str(row.id), "evidence_id": marker["evidence_id"]}]
    assert response.release_id == artifact.release_id
    assert [call.args[1] for call in audit.await_args_list] == [
        "workflow.registration_retired", "workspace_release.retired"]
    assert all(call.kwargs["strict"] for call in audit.await_args_list)


def test_retirement_contract_rejects_duplicate_uuids_and_blank_reviews():
    from pydantic import ValidationError

    _release, _artifact, _descriptor, request = _rows()
    _row, review = _obsolete_fixture()
    data = request.model_dump()
    with pytest.raises(ValidationError, match="unique"):
        WorkspaceLiveRetireRequest(**{**data, "obsolete_registrations": [review, review]})
    with pytest.raises(ValidationError, match="review reference"):
        WorkspaceLiveRetireRequest(**{**data, "obsolete_registrations": [{**review, "review_reference": " "}]})
