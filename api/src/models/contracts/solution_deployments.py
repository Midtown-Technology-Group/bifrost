"""HTTP contracts for immutable Solution deployment revisions."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.services.solutions.deployment_manifest import (
    CompiledDeploymentManifest,
    DeploymentResolutionMap,
)


class SolutionDeploymentCreate(BaseModel):
    """Register already revision-addressed source/runtime references.

    This endpoint does not upload a source archive or runtime files. The
    manifest references must already exist at the canonical deployment keys.
    """

    model_config = ConfigDict(extra="forbid")
    compiled_manifest: CompiledDeploymentManifest
    resolution_map: DeploymentResolutionMap
    base_deployment_id: UUID | None = None
    parent_deployment_id: UUID | None = None
    declared_version: str | None = None
    git_repository: str | None = None
    git_ref: str | None = None
    git_commit_sha: str | None = None
    codex_worker_id: str | None = None


class DeploymentPointerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_active_deployment_id: UUID | None


class SolutionDeploymentPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID | None
    solution_id: UUID
    parent_deployment_id: UUID | None
    base_deployment_id: UUID | None
    state: str
    bundle_hash: str
    compiled_manifest_hash: str
    resolution_map_hash: str
    source_artifact_key: str
    runtime_storage_prefix: str
    created_at: datetime
    failure_detail: dict | None = None


class DeploymentActivationPublic(BaseModel):
    deployment_id: UUID
    solution_id: UUID
    state: str
    previous_active_deployment_id: UUID | None
    active_deployment_id: UUID | None
    conflict: dict | None = None
    recovery: dict | None = None


class SolutionDeploymentCapabilities(BaseModel):
    registration: bool = True
    inspection: bool = True
    artifact_upload: bool = False
    server_side_compilation: bool = False
    activation_configured: bool = False
    safe_for_end_to_end_cs_deploy: bool = False


class WorkspaceLiveHandoffPreflightRequest(BaseModel):
    """Expected Live and Solution state for a read-only adoption inspection."""

    model_config = ConfigDict(extra="forbid")

    expected_release_row_id: UUID
    expected_release_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_artifact_id: UUID
    expected_governed_manifest_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_registration_state_fingerprint: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$"
    )
    expected_active_deployment_id: UUID | None
    workflow_ids: list[UUID] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_workflows(self):
        if len(self.workflow_ids) != len(set(self.workflow_ids)):
            raise ValueError("workflow IDs must be unique")
        return self


class WorkspaceLiveHandoffPreflightResponse(BaseModel):
    solution_id: UUID
    deployment_id: UUID
    release_row_id: UUID
    release_id: str
    governed_manifest_id: str
    registration_state_fingerprint: str
    workflow_ids: list[UUID]
    verified_source_paths: list[str]
    expected_active_deployment_id: UUID | None
    evidence_id: str


class WorkspaceLiveHandoffCommitRequest(WorkspaceLiveHandoffPreflightRequest):
    """Preflight expectation that must still hold at the locked commit."""

    expected_evidence_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class WorkspaceLiveHandoffCommitResponse(BaseModel):
    solution_id: UUID
    deployment_id: UUID
    release_row_id: UUID
    release_id: str
    workflow_ids: list[UUID]
    evidence_id: str
    state: str
