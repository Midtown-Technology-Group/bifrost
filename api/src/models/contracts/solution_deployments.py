"""HTTP contracts for immutable Solution deployment revisions."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from bifrost.solution_delivery_review import ReviewedWorkflowRecipe
from bifrost.root_file_bindings import RootFileBinding, require_root_file_bindings
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.services.solutions.deployment_manifest import (
    CompiledDeploymentManifest,
    DeploymentResolutionMap,
    SharedRootTableBinding,
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


class SolutionGitSourceDeliveryRequest(BaseModel):
    """Source comes from the configured protected recipe, never uploaded bytes."""

    model_config = ConfigDict(extra="forbid")
    source_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    ci_run_id: int = Field(gt=0)
    ci_run_attempt: int = Field(gt=0)
    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SolutionGitSourceDeliveryResponse(BaseModel):
    state: Literal["active", "already_active"]
    solution_id: UUID
    deployment_id: UUID
    compiled_manifest_hash: str
    source_commit_sha: str
    source_tree_sha: str
    artifact_digest: str
    source_hashes: dict[str, str]
    receipt_id: UUID
    source_verified: Literal[True] = True
    runtime_verified: Literal[False] = False


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


class SolutionDeploymentRuntimeState(BaseModel):
    """Independent readback of the installed runtime pointer."""

    solution_id: UUID
    active_deployment_id: UUID | None
    execution_runtime_mode: str


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
    shared_tables: dict[str, SharedRootTableBinding] = Field(default_factory=dict, max_length=50)
    root_file_bindings: dict[str, RootFileBinding] = Field(default_factory=dict, max_length=100)

    @model_validator(mode="after")
    def unique_workflows(self):
        require_root_file_bindings(self.root_file_bindings)
        if len(self.workflow_ids) != len(set(self.workflow_ids)):
            raise ValueError("workflow IDs must be unique")
        if len({item.table_id for item in self.shared_tables.values()}) != len(self.shared_tables):
            raise ValueError("shared table IDs must be unique")
        return self


class SharedTableBindingPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table_ids: list[UUID] = Field(min_length=1, max_length=50)


class WorkspaceLiveHandoffPreflightResponse(BaseModel):
    solution_id: UUID
    deployment_id: UUID
    release_row_id: UUID
    release_id: str
    governed_manifest_id: str
    registration_state_fingerprint: str
    workflow_ids: list[UUID]
    verified_source_paths: list[str]
    verified_shared_tables: dict[str, SharedRootTableBinding] = Field(default_factory=dict)
    verified_root_file_bindings: dict[str, RootFileBinding] = Field(default_factory=dict)
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


class SolutionSourceFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    content_base64: str


class SolutionSourceRevisionRequest(BaseModel):
    """A source-only revision of the exact workflows in an active deployment."""

    model_config = ConfigDict(extra="forbid")

    expected_active_deployment_id: UUID
    expected_active_manifest_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    files: list[SolutionSourceFile] = Field(min_length=1, max_length=256)


class SolutionSourceRevisionInspectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_active_deployment_id: UUID
    expected_active_manifest_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SolutionSourceRevisionInspectResponse(BaseModel):
    solution_id: UUID
    deployment_id: UUID
    active_deployment_id: UUID
    source_commit_sha: str
    workflow_ids: list[UUID]
    active_subscription_ids: list[UUID]
    source_hashes: dict[str, str]
    evidence_id: str
    state: str


class SolutionSourceRevisionCommitRequest(SolutionSourceRevisionInspectRequest):
    expected_evidence_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SolutionWorkflowRevisionRequest(SolutionSourceRevisionRequest):
    """Reviewed operator revision, including registration and resource contracts."""

    reviewed_recipe: ReviewedWorkflowRecipe
    resources: list[SolutionSourceFile] = Field(default_factory=list, max_length=256)


class SolutionWorkflowRevisionInspectRequest(SolutionSourceRevisionInspectRequest):
    reviewed_recipe: ReviewedWorkflowRecipe


class SolutionWorkflowRevisionCommitRequest(SolutionSourceRevisionCommitRequest):
    reviewed_recipe: ReviewedWorkflowRecipe


class InitialWorkflowInstallRequest(BaseModel):
    """Reviewed source closure for the first immutable workflow install."""

    model_config = ConfigDict(extra="forbid")
    source_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    reviewed_recipe: ReviewedWorkflowRecipe
    files: list[SolutionSourceFile] = Field(min_length=1, max_length=256)
    resources: list[SolutionSourceFile] = Field(default_factory=list, max_length=256)


class InitialWorkflowInstallInspectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewed_recipe: ReviewedWorkflowRecipe


class InitialWorkflowInstallCommitRequest(InitialWorkflowInstallInspectRequest):
    expected_evidence_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class InitialWorkflowInstallInspectResponse(BaseModel):
    solution_id: UUID
    deployment_id: UUID
    organization_id: UUID | None
    source_commit_sha: str
    workflow_ids: list[UUID]
    source_hashes: dict[str, str]
    evidence_id: str
    state: str
