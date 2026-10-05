"""Deliver verified protected Git source with durable, atomic pointer receipts."""

import base64
import json

import httpx
from dataclasses import replace
from typing import Literal
from uuid import UUID, uuid5

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import SYSTEM_USER_UUID
from src.core.solution_delivery_policy import SolutionGitDeliveryPolicy
from src.models.contracts.solution_deployments import (
    SolutionGitSourceDeliveryRequest, SolutionGitSourceDeliveryResponse, SolutionSourceFile,
    SolutionSourceRevisionRequest, SolutionSourceRevisionCommitRequest, SolutionSourceRevisionInspectRequest,
)
from src.models.orm.solutions import Solution
from src.models.orm.solution_deployments import SolutionDeployment
from src.models.orm.workspace_promotions import SolutionDeployObligation, WorkspaceSourceRelease
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.operation_receipts import (
    OperationReceiptDisposition, canonical_operation_scope_key, canonical_request_fingerprint,
    claim_operation_receipt, complete_operation_receipt_success,
)
from src.services.solutions.deployment_manifest import canonical_json, validate_runtime_closure
from src.services.solutions.github_delivery_source import (
    MAX_ANCESTRY_COMMITS, MAX_HISTORICAL_AUTHORED_BYTES, MAX_HISTORICAL_AUTHORED_SOURCES,
    GitDeliveryIdentity, GitDeliverySourceError, ProtectedGitReader,
    VerifiedAuthoredSolution, VerifiedGitSource,
)
from src.services.solutions.source_revision import (
    SolutionSourceRevisionConflict, SolutionSourceRevisionService, retain_legacy_registration_names,
)
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


async def _unresolved_source_commits(db: AsyncSession, organization_id: UUID) -> set[str]:
    """Prioritize delayed producer declarations within the same bounded proof."""
    return set((await db.scalars(select(WorkspaceSourceRelease.source_commit_sha).where(
        WorkspaceSourceRelease.organization_id == organization_id,
        WorkspaceSourceRelease.declaration_actor == "github_actions_oidc",
        WorkspaceSourceRelease.disposition.in_(("pending", "attention_required", "deferred")))
        .order_by(WorkspaceSourceRelease.accounting_checked_at.asc().nulls_first(),
            WorkspaceSourceRelease.created_at, WorkspaceSourceRelease.id)
        .limit(MAX_ANCESTRY_COMMITS))).all())


async def _historical_authored_source(reader: ProtectedGitReader, record) -> VerifiedAuthoredSolution | None:
    from src.services.solutions.native_authored_accounting import require_declared_authored_source
    from src.services.solutions.native_authored_source import NativeAuthoredSourceMismatch

    try:
        prior = await reader.authored_source(record.source_commit_sha, record.repo_subpath,
            expected_tree_sha=record.source_tree_sha)
        require_declared_authored_source(record, prior)
        return prior
    except (GitDeliverySourceError, NativeAuthoredSourceMismatch, httpx.HTTPError):
        # This optional old input cannot settle its obligation. Verification of
        # CURRENT source/CI remains mandatory and its errors still propagate.
        return None


class GitSourceDeliveryService:
    def __init__(self, db: AsyncSession, policy: SolutionGitDeliveryPolicy, reader: ProtectedGitReader):
        self.db, self.policy, self.reader = db, policy, reader

    async def deliver(self, solution_id: UUID, request: SolutionGitSourceDeliveryRequest,
                      producer: GitDeliveryIdentity) -> SolutionGitSourceDeliveryResponse:
        # Authenticate before this method. CI and full Git source must be proved
        # before writing a receipt, artifact or candidate.
        await self.reader.verify_ci(request.source_commit_sha, request.ci_run_id, request.ci_run_attempt)
        source = await self.reader.source(solution_id, request.source_commit_sha, request.artifact_digest)
        # Native packages retain their complete authored tree separately from
        # the executable closure. Root-mapped workflows and Apps keep their
        # existing distinct delivery contracts.
        authored = None
        installed = await self.db.get(Solution, solution_id, populate_existing=True)
        if (installed is not None and isinstance(installed.repo_subpath, str)
                and installed.repo_subpath == f"solutions/{installed.slug}"
                and source.repository_paths
                and all(path.startswith(installed.repo_subpath + "/") for path in source.repository_paths.values())):
            authored = await self.reader.authored_source(source.commit_sha, installed.repo_subpath,
                expected_tree_sha=source.tree_sha)
        from src.config import get_settings
        from src.services.github_actions_oidc import workspace_source_release_tracking_organization_id
        tracking_org = workspace_source_release_tracking_organization_id(get_settings())
        if tracking_org is not None:
            older = await _unresolved_source_commits(self.db, tracking_org)
            historical = []
            history_query = None
            if authored is not None:
                history_query = (select(SolutionDeployObligation.id,
                        SolutionDeployObligation.source_commit_sha)
                    .join(WorkspaceSourceRelease, WorkspaceSourceRelease.id == SolutionDeployObligation.source_release_id)
                    .where(SolutionDeployObligation.organization_id == tracking_org,
                        SolutionDeployObligation.repo_subpath == authored.repo_subpath,
                        SolutionDeployObligation.solution_slug == authored.solution_slug,
                        SolutionDeployObligation.declared_disposition == "solution_deploy_required",
                        SolutionDeployObligation.disposition.in_(("pending", "attention_required")),
                        SolutionDeployObligation.source_commit_sha != source.commit_sha,
                        WorkspaceSourceRelease.organization_id == tracking_org,
                        WorkspaceSourceRelease.declaration_actor == "github_actions_oidc",
                        WorkspaceSourceRelease.source_commit_sha == SolutionDeployObligation.source_commit_sha,
                        WorkspaceSourceRelease.source_tree_sha == SolutionDeployObligation.source_tree_sha))
            if history_query is not None and await self.db.scalar(history_query.limit(1)) is not None:
                # The bounded verified chain filters eligible anchors in SQL;
                # old declarations outside that chain cannot occupy archive slots.
                source = replace(source, ancestor_commit_shas=await self.reader.verified_ancestors(source.commit_sha, None))
                historical = list((await self.db.execute(history_query.where(
                        SolutionDeployObligation.source_commit_sha.in_(source.ancestor_commit_shas))
                    # Every intended install must retain the same tranche until
                    # aggregate recovery settles it. Recovery rotates updated_at
                    # even for incomplete evidence, so it cannot order this read.
                    .order_by(SolutionDeployObligation.created_at, SolutionDeployObligation.id)
                    .limit(MAX_HISTORICAL_AUTHORED_SOURCES))).all())
            elif older:
                source = replace(source, ancestor_commit_shas=await self.reader.verified_ancestors(source.commit_sha, older))
            if historical:
                retained = []
                retained_bytes = 0
                for identity, commit in historical:
                    if commit not in source.ancestor_commit_shas:
                        continue
                    # Load one full manifest at a time rather than materializing
                    # 100 large JSON inventories before applying the byte cap.
                    record = await self.db.get(SolutionDeployObligation, identity, populate_existing=True)
                    if record is None or record.source_commit_sha != commit:
                        continue
                    prior = await _historical_authored_source(self.reader, record)
                    if prior is None:
                        continue
                    size = sum(len(raw) for raw in prior.files.values()) + len(canonical_json(prior.file_manifest()))
                    if retained_bytes + size > MAX_HISTORICAL_AUTHORED_BYTES:
                        continue
                    retained.append(prior)
                    retained_bytes += size
                source = replace(source, historical_authored_sources=tuple(retained))
        async with solution_write_lock(solution_id):
            result = await self._deliver_locked(source, request, producer, authored)
        # Receipt replays also reach accounting recovery. Release the one-install
        # writer before the aggregate Live -> installs -> source-row fence.
        from src.services.solution_source_accountability import reconcile_solution_owned_source
        from src.services.solutions.native_authored_accounting import reconcile_native_solution_deploy_obligations
        await reconcile_native_solution_deploy_obligations(self.db, policy=self.policy)
        await reconcile_solution_owned_source(self.db, policy=self.policy)
        await self.db.commit()
        return result

    async def _deliver_locked(self, source: VerifiedGitSource, request: SolutionGitSourceDeliveryRequest,
                              producer: GitDeliveryIdentity,
                              authored: VerifiedAuthoredSolution | None = None) -> SolutionGitSourceDeliveryResponse:
        organization_id = self.policy.organization_id_for(source.solution_id)
        solution = await self.db.get(Solution, source.solution_id, populate_existing=True)
        if (solution is None or solution.organization_id != organization_id
                or solution.status != "active" or solution.execution_runtime_mode != "deployment-v1"
                or solution.active_deployment_id is None):
            raise GitDeliverySourceError("Configured Solution is outside the adopted organization/runtime scope")
        repository = SolutionDeploymentRepository(self.db)
        base = await repository.get_runtime_closure(solution.active_deployment_id,
                                                    organization_id, solution.id)
        if base is None or base.state != "active":
            raise SolutionSourceRevisionConflict("Installed deployment is not active")
        manifest, resolution = validate_runtime_closure(base.compiled_manifest, base.resolution_map,
            base.dependencies, expected_manifest_hash=base.compiled_manifest_hash,
            expected_resolution_hash=base.resolution_map_hash)
        current_hashes = {path: ref.content_hash for path, ref in {**resolution.sources, **resolution.resources}.items()}
        matches = manifest.git.commit_sha == source.commit_sha and current_hashes == source.source_hashes
        if source.workflow_recipe is not None:
            desired = retain_legacy_registration_names(
                compile_workflow_registrations(source.workflow_recipe, source.files, WorkflowIndexer(self.db)),
                dict(resolution.workflows))
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
        proof = {"repository": self.policy.repository, "repository_id": self.policy.repository_id,
            "repository_owner_id": self.policy.repository_owner_id, "recipe_path": source.recipe_path,
            "organization_id": str(organization_id) if organization_id is not None else None,
            "commit_sha": source.commit_sha, "tree_sha": source.tree_sha,
            "artifact_digest": source.artifact_digest, "ci_run_id": request.ci_run_id,
            "ci_run_attempt": request.ci_run_attempt, "producer_run_id": producer.run_id,
            "producer_run_attempt": producer.run_attempt, "receipt_id": str(claim.receipt_id)}
        proof.update({"source_mapping_schema": "bifrost.solution-git-source-mapping/v1",
            "solution_id": str(source.solution_id), "repository_paths": source.repository_paths,
            "runtime_hashes": source.source_hashes, "control_hashes": source.control_hashes,
            "installation_registry": source.installation_registry,
            "ancestor_commit_shas": list(source.ancestor_commit_shas)})
        if claim.disposition != OperationReceiptDisposition.OWNER:
            if claim.disposition == OperationReceiptDisposition.SUCCEEDED and matches:
                await self.reader.verify_ci(source.commit_sha, request.ci_run_id, request.ci_run_attempt)
                # Fresh protected readback may recover mapping/ancestry evidence
                # after a delivery-before-declaration race. It never reclaims a
                # receipt or changes source, registrations or a runtime pointer.
                authored_state = await self._record_proof(base, source, organization_id, proof, authored)
                await self.db.commit()
                return self._response(source, base, claim.receipt_id, "already_active", authored_state)
            raise SolutionSourceRevisionConflict(
                f"Delivery receipt {claim.receipt_id} requires readback or a fresh job attempt")
        assert claim.owner_token is not None
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
            base = await repository.get_runtime_closure(deployment_id, organization_id, solution.id)
            assert base is not None
            state = "active"
        else:
            await self.reader.verify_ci(source.commit_sha, request.ci_run_id, request.ci_run_attempt)
        # Deploy-owned evidence uses the deployment write path. The global
        # Solution ORM guard deliberately rejects editing a loaded managed row.
        authored_state = await self._record_proof(base, source, organization_id, proof, authored)
        result = self._response(source, base, claim.receipt_id, state, authored_state)
        # Only small nonsecret metadata is retained in the replay envelope.
        # Complete receipt and pointer mutation in the same SQL transaction.
        await complete_operation_receipt_success(claim.receipt_id, claim.owner_token,
            {"solution_id": str(solution.id), "deployment_id": str(base.id),
             "compiled_manifest_hash": base.compiled_manifest_hash, "source_commit_sha": source.commit_sha,
             "artifact_digest": source.artifact_digest}, db=self.db)
        await self.db.commit()
        self.db.expire_all()
        observed = await self.db.get(Solution, source.solution_id)
        active = await repository.get_runtime_closure(result.deployment_id, organization_id, source.solution_id)
        if (observed is None or observed.organization_id != organization_id
                or observed.active_deployment_id != result.deployment_id
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

    async def _record_proof(self, base: SolutionDeployment, source: VerifiedGitSource,
                            organization_id: UUID | None, proof: dict,
                            authored: VerifiedAuthoredSolution | None = None) -> Literal["unmapped", "verified", "attention_required"]:
        authored_state: Literal["unmapped", "verified", "attention_required"] = "unmapped"
        if authored is not None:
            from src.services.solutions.authored_archive import retain_authored_archive
            from src.services.solutions.deployment_storage import SolutionDeploymentStorage
            storage = SolutionDeploymentStorage(source.solution_id, base.id)
            proof["authored_source"] = await retain_authored_archive(storage, authored)
            proof["authored_source_history"] = [await retain_authored_archive(storage, prior)
                for prior in source.historical_authored_sources]
            authored_state = await self._deliver_authored_readme(base, source, authored)
        recorded_id = await self.db.scalar(update(SolutionDeployment).where(
            SolutionDeployment.id == base.id,
            SolutionDeployment.solution_id == source.solution_id,
            SolutionDeployment.organization_id == organization_id,
            SolutionDeployment.state == "active",
            SolutionDeployment.compiled_manifest_hash == base.compiled_manifest_hash,
        ).values(validation_result={**(base.validation_result or {}), "github_delivery": proof})
            .returning(SolutionDeployment.id).execution_options(synchronize_session=False))
        if recorded_id is None:
            raise SolutionSourceRevisionConflict("Active deployment changed before delivery evidence")
        # Bulk SQL bypasses the loaded object's identity-map state. Replay may
        # use this same Session for a late declaration; retain the actual proof
        # rather than its pre-delivery validation metadata.
        await self.db.refresh(base, attribute_names=["validation_result"])
        return authored_state

    async def _deliver_authored_readme(self, base: SolutionDeployment, source: VerifiedGitSource,
                                      authored: VerifiedAuthoredSolution) -> Literal["verified", "attention_required"]:
        from src.services.solutions.native_authored_source import (
            NativeAuthoredSourceMismatch, native_authored_install_readback, native_authored_metadata,
        )
        # This metadata write does not mint a runtime deployment or overwrite
        # source.zip. The existing install writer and exact pointer/README CAS
        # serialize it, and all other authored components must read back before
        # that write can commit. Unsupported component delivery stays explicit.
        try:
            _manifest, resolution = validate_runtime_closure(
                base.compiled_manifest,
                base.resolution_map,
                base.dependencies,
                expected_manifest_hash=base.compiled_manifest_hash,
                expected_resolution_hash=base.resolution_map_hash,
            )
            metadata = native_authored_metadata(
                authored, resource_paths=frozenset(resolution.resources)
            )
            async with self.db.begin_nested():
                installed = await self.db.scalar(select(Solution).where(Solution.id == source.solution_id,
                    Solution.active_deployment_id == base.id, Solution.status == "active")
                    .with_for_update().execution_options(populate_existing=True))
                if installed is None:
                    raise SolutionSourceRevisionConflict("Active pointer changed before authored metadata")
                if installed.readme != metadata.readme:
                    changed = await self.db.scalar(update(Solution).where(Solution.id == installed.id,
                        Solution.active_deployment_id == base.id, Solution.readme == installed.readme)
                        .values(readme=metadata.readme).returning(Solution.id)
                        .execution_options(synchronize_session=False))
                    if changed is None:
                        raise SolutionSourceRevisionConflict("Authored README compare-and-swap failed")
                    await self.db.refresh(installed, attribute_names=["readme"])
                await native_authored_install_readback(self.db, installed, authored,
                    expected_active_deployment_id=base.id, expected_active_manifest_hash=base.compiled_manifest_hash)
            return "verified"
        except SolutionSourceRevisionConflict:
            raise
        except (NativeAuthoredSourceMismatch, ValueError):
            # Savepoint rollback preserves runtime success and prior README.
            # Do not report complete Source or close the ledger from a partial
            # runtime mapping. Transport/storage failures still propagate.
            return "attention_required"

    @staticmethod
    def _response(source: VerifiedGitSource, deployment: SolutionDeployment, receipt_id: UUID,
                  state: Literal["active", "already_active"],
                  authored_state: Literal["unmapped", "verified", "attention_required"] = "unmapped") -> SolutionGitSourceDeliveryResponse:
        return SolutionGitSourceDeliveryResponse(state=state, solution_id=source.solution_id,
            deployment_id=deployment.id, compiled_manifest_hash=deployment.compiled_manifest_hash,
            source_commit_sha=source.commit_sha, source_tree_sha=source.tree_sha,
            artifact_digest=source.artifact_digest, source_hashes=source.source_hashes, receipt_id=receipt_id,
            authored_source_state=authored_state)
