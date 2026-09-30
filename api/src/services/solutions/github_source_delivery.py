"""Deliver verified protected Git source with durable, atomic pointer receipts."""

import base64
import json
from typing import Literal
from uuid import UUID, uuid5

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import SYSTEM_USER_UUID
from src.core.solution_delivery_policy import SolutionGitDeliveryPolicy
from src.models.contracts.solution_deployments import (
    SolutionGitSourceDeliveryRequest, SolutionGitSourceDeliveryResponse, SolutionSourceFile,
    SolutionSourceRevisionRequest, SolutionSourceRevisionCommitRequest, SolutionSourceRevisionInspectRequest,
)
from src.models.orm.solutions import Solution
from src.models.orm.solution_deployments import SolutionDeployment
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.operation_receipts import (
    OperationReceiptDisposition, canonical_operation_scope_key, canonical_request_fingerprint,
    claim_operation_receipt, complete_operation_receipt_success,
)
from src.services.solutions.deployment_manifest import validate_runtime_closure
from src.services.solutions.github_delivery_source import (
    GitDeliveryIdentity, GitDeliverySourceError, ProtectedGitReader, VerifiedGitSource,
)
from src.services.solutions.source_revision import SolutionSourceRevisionConflict, SolutionSourceRevisionService
from src.services.file_storage.indexers.workflow import WorkflowIndexer
from src.services.solutions.workflow_revision import SolutionWorkflowRevisionService
from src.services.solutions.workflow_revision_recipe import compile_workflow_registrations
from src.services.solutions.write_lock import solution_write_lock

NAMESPACE = UUID("3e88982c-3d73-4ac1-a20a-d46c21228204")


def source_candidate_id(source: VerifiedGitSource, base_id: UUID, base_hash: str) -> UUID:
    """Matches the existing exact-Git operator's create-only recovery identity."""
    identity = json.dumps({"solution_id": str(source.solution_id), "commit": source.commit_sha,
        "hashes": source.source_hashes, "base": str(base_id), "base_hash": base_hash,
        **({"workflow_artifact_digest": source.artifact_digest} if source.workflow_recipe else {})},
        sort_keys=True, separators=(",", ":"))
    return uuid5(NAMESPACE, identity)


class GitSourceDeliveryService:
    def __init__(self, db: AsyncSession, policy: SolutionGitDeliveryPolicy, reader: ProtectedGitReader):
        self.db, self.policy, self.reader = db, policy, reader

    async def deliver(self, solution_id: UUID, request: SolutionGitSourceDeliveryRequest,
                      producer: GitDeliveryIdentity) -> SolutionGitSourceDeliveryResponse:
        # Authenticate before this method. CI and full Git source must be proved
        # before writing a receipt, artifact or candidate.
        await self.reader.verify_ci(request.source_commit_sha, request.ci_run_id, request.ci_run_attempt)
        source = await self.reader.source(solution_id, request.source_commit_sha, request.artifact_digest)
        async with solution_write_lock(solution_id):
            return await self._deliver_locked(source, request, producer)

    async def _deliver_locked(self, source: VerifiedGitSource, request: SolutionGitSourceDeliveryRequest,
                              producer: GitDeliveryIdentity) -> SolutionGitSourceDeliveryResponse:
        solution = await self.db.get(Solution, source.solution_id, populate_existing=True)
        if (solution is None or solution.organization_id != self.policy.organization_id
                or solution.status != "active" or solution.execution_runtime_mode != "deployment-v1"
                or solution.active_deployment_id is None):
            raise GitDeliverySourceError("Configured Solution is outside the adopted organization/runtime scope")
        repository = SolutionDeploymentRepository(self.db)
        base = await repository.get_runtime_closure(solution.active_deployment_id,
                                                    solution.organization_id, solution.id)
        if base is None or base.state != "active":
            raise SolutionSourceRevisionConflict("Installed deployment is not active")
        manifest, resolution = validate_runtime_closure(base.compiled_manifest, base.resolution_map,
            base.dependencies, expected_manifest_hash=base.compiled_manifest_hash,
            expected_resolution_hash=base.resolution_map_hash)
        current_hashes = {path: ref.content_hash for path, ref in {**resolution.sources, **resolution.resources}.items()}
        matches = manifest.git.commit_sha == source.commit_sha and current_hashes == source.source_hashes
        if source.workflow_recipe is not None:
            desired = compile_workflow_registrations(source.workflow_recipe, source.files, WorkflowIndexer(self.db))
            matches = matches and desired == resolution.workflows and source.workflow_recipe.shared_tables == resolution.shared_tables
        if matches:
            expected_current = SolutionSourceRevisionInspectRequest(expected_active_deployment_id=base.id,
                expected_active_manifest_hash=base.compiled_manifest_hash)
            if source.workflow_recipe is not None:
                await SolutionWorkflowRevisionService(self.db).verify_current_workflows(solution.id, expected_current, source.workflow_recipe)
            else:
                await SolutionSourceRevisionService(self.db).verify_current_source(solution.id, expected_current)
        identity = {"repository_id": self.policy.repository_id, "solution_id": str(solution.id),
            "source_commit_sha": source.commit_sha, "artifact_digest": source.artifact_digest,
            "ci_run_id": request.ci_run_id, "ci_run_attempt": request.ci_run_attempt,
            "producer_run_id": producer.run_id, "producer_run_attempt": producer.run_attempt}
        claim = await claim_operation_receipt(namespace="solution.git-source-delivery",
            scope_key=canonical_operation_scope_key(identity), request_fingerprint=canonical_request_fingerprint(identity))
        if claim.disposition != OperationReceiptDisposition.OWNER:
            if claim.disposition == OperationReceiptDisposition.SUCCEEDED and matches:
                return self._response(source, base, claim.receipt_id, "already_active")
            # Same job may still be running or may have lost its response. Never
            # reclaim its effect. A fresh CI job attempt re-reads the pointer;
            # deterministic candidates resume staging against the observed base.
            raise SolutionSourceRevisionConflict(
                f"Delivery receipt {claim.receipt_id} requires readback or a fresh job attempt")
        assert claim.owner_token is not None
        proof = {"repository": self.policy.repository, "repository_id": self.policy.repository_id,
            "repository_owner_id": self.policy.repository_owner_id, "recipe_path": source.recipe_path,
            "commit_sha": source.commit_sha, "tree_sha": source.tree_sha,
            "artifact_digest": source.artifact_digest, "ci_run_id": request.ci_run_id,
            "ci_run_attempt": request.ci_run_attempt, "producer_run_id": producer.run_id,
            "producer_run_attempt": producer.run_attempt, "receipt_id": str(claim.receipt_id)}
        state: Literal["active", "already_active"] = "already_active"
        if not matches:
            deployment_id = source_candidate_id(source, base.id, base.compiled_manifest_hash)
            expected = {"expected_active_deployment_id": base.id,
                        "expected_active_manifest_hash": base.compiled_manifest_hash}
            service = SolutionSourceRevisionService(self.db)
            workflow_service = SolutionWorkflowRevisionService(self.db)
            if source.workflow_recipe is not None:
                inspected = await workflow_service.stage_workflows(solution.id, deployment_id, SYSTEM_USER_UUID,
                    SolutionSourceRevisionInspectRequest(**expected), source.workflow_recipe, source.files, source.commit_sha,
                    source.resources)
            else:
                inspected = await service.stage(solution.id, deployment_id, SYSTEM_USER_UUID,
                    SolutionSourceRevisionRequest(**expected, source_commit_sha=source.commit_sha,
                        files=[SolutionSourceFile(path=path, content_base64=base64.b64encode(content).decode())
                               for path, content in sorted(source.files.items())]))
            if inspected.source_hashes != source.source_hashes or inspected.source_commit_sha != source.commit_sha:
                raise GitDeliverySourceError("Staged source differs from verified Git")
            await self.db.commit()
            # A newer merge or CI rerun must stop this job before activation.
            await self.reader.verify_ci(source.commit_sha, request.ci_run_id, request.ci_run_attempt)
            activation = SolutionSourceRevisionCommitRequest(**expected, expected_evidence_id=inspected.evidence_id)
            if source.workflow_recipe is not None:
                await workflow_service.activate_workflows(solution.id, deployment_id, activation, source.workflow_recipe)
            else:
                await service.activate(solution.id, deployment_id, activation)
            base = await repository.get_runtime_closure(deployment_id, self.policy.organization_id, solution.id)
            assert base is not None
            state = "active"
        else:
            await self.reader.verify_ci(source.commit_sha, request.ci_run_id, request.ci_run_attempt)
        # Deploy-owned evidence uses the deployment write path. The global
        # Solution ORM guard deliberately rejects editing a loaded managed row.
        recorded_id = await self.db.scalar(update(SolutionDeployment).where(
            SolutionDeployment.id == base.id,
            SolutionDeployment.solution_id == source.solution_id,
            SolutionDeployment.organization_id == self.policy.organization_id,
            SolutionDeployment.state == "active",
            SolutionDeployment.compiled_manifest_hash == base.compiled_manifest_hash,
        ).values(validation_result={**(base.validation_result or {}), "github_delivery": proof})
            .returning(SolutionDeployment.id).execution_options(synchronize_session=False))
        if recorded_id is None:
            raise SolutionSourceRevisionConflict("Active deployment changed before delivery evidence")
        result = self._response(source, base, claim.receipt_id, state)
        # Only small nonsecret metadata is retained in the replay envelope.
        # Complete receipt and pointer mutation in the same SQL transaction.
        await complete_operation_receipt_success(claim.receipt_id, claim.owner_token,
            {"solution_id": str(solution.id), "deployment_id": str(base.id),
             "compiled_manifest_hash": base.compiled_manifest_hash, "source_commit_sha": source.commit_sha,
             "artifact_digest": source.artifact_digest}, db=self.db)
        await self.db.commit()
        self.db.expire_all()
        observed = await self.db.get(Solution, source.solution_id)
        active = await repository.get_runtime_closure(result.deployment_id, self.policy.organization_id, source.solution_id)
        if (observed is None or observed.active_deployment_id != result.deployment_id
                or observed.execution_runtime_mode != "deployment-v1" or active is None
                or active.state != "active" or active.compiled_manifest_hash != result.compiled_manifest_hash):
            raise SolutionSourceRevisionConflict("Independent installed pointer readback differs")
        if source.workflow_recipe is not None:
            await SolutionWorkflowRevisionService(self.db).verify_current_workflows(source.solution_id,
                SolutionSourceRevisionInspectRequest(expected_active_deployment_id=result.deployment_id,
                    expected_active_manifest_hash=result.compiled_manifest_hash), source.workflow_recipe)
            from src.core.redis_client import get_redis_client
            redis_client = get_redis_client()
            for item in source.workflow_recipe.workflows:
                await redis_client.invalidate_workflow_metadata_cache(str(item.id))
        return result

    @staticmethod
    def _response(source: VerifiedGitSource, deployment: SolutionDeployment, receipt_id: UUID,
                  state: Literal["active", "already_active"]) -> SolutionGitSourceDeliveryResponse:
        return SolutionGitSourceDeliveryResponse(state=state, solution_id=source.solution_id,
            deployment_id=deployment.id, compiled_manifest_hash=deployment.compiled_manifest_hash,
            source_commit_sha=source.commit_sha, source_tree_sha=source.tree_sha,
            artifact_digest=source.artifact_digest, source_hashes=source.source_hashes, receipt_id=receipt_id)
