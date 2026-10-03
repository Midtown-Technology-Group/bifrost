from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from src.services.solutions.deployment_manifest import (
    CompiledDeploymentManifest,
    DeploymentGitProvenance,
    DeploymentResolutionMap,
    DeploymentSource,
    RuntimeEntityDefinition,
    RuntimeSourceResolution,
    WORKFLOW_PARAMETERS_SCHEMA_CONTRACT,
    canonical_json,
    sha256_digest,
)
from src.services.solutions.deployment_runtime import (
    DeploymentRuntimeError,
    pin_workflow_runtime,
    resolve_pinned_workflow_runtime,
    verify_runtime_evidence,
    workflow_data_from_evidence,
)


class _Result:
    def __init__(self, row):
        self.row = row

    def one_or_none(self):
        return self.row


def _closure(*, deployment_id, solution_id, workflow_id, source_text, runtime_bounds=None,
             definition_extra=None):
    source_hash = sha256_digest(source_text.encode())
    entity = RuntimeEntityDefinition(
        portable_ref="workflows/demo.py::demo",
        resolved_id=workflow_id,
        source_ref="workflows/demo.py",
        source_hash=source_hash,
        definition={
            "name": f"demo-{source_text}",
            "function_name": "demo",
            "path": "workflows/demo.py",
            "timeout_seconds": 30,
            "type": "workflow",
            **({"runtime_bounds": runtime_bounds} if runtime_bounds else {}),
            **(definition_extra or {}),
        },
    )
    resolution = DeploymentResolutionMap(
        workflows={entity.portable_ref: entity},
        sources={
            "workflows/demo.py": RuntimeSourceResolution(
                object_key=f"_solutions/{solution_id}/{deployment_id}/workflows/demo.py",
                content_hash=source_hash,
            )
        },
    )
    resolution_hash = sha256_digest(canonical_json(resolution))
    manifest = CompiledDeploymentManifest(
        solution_id=solution_id,
        deployment_id=deployment_id,
        bundle_hash=sha256_digest(source_text.encode()),
        resolution_map_hash=resolution_hash,
        source=DeploymentSource(
            artifact_key=f"_solution_artifacts/{solution_id}/{deployment_id}/source.zip",
            runtime_prefix=f"_solutions/{solution_id}/{deployment_id}/",
        ),
        workflows={entity.portable_ref: entity},
        git=DeploymentGitProvenance(),
    )
    return SimpleNamespace(
        id=deployment_id,
        solution_id=solution_id,
        state="active",
        bundle_hash=manifest.bundle_hash,
        compiled_manifest=manifest.model_dump(mode="json"),
        compiled_manifest_hash=manifest.content_hash(),
        resolution_map=resolution.model_dump(mode="json"),
        resolution_map_hash=resolution_hash,
        runtime_storage_prefix=manifest.source.runtime_prefix,
        git_commit_sha=None,
        dependencies=[],
    )


@pytest.mark.asyncio
async def test_active_pointer_selects_runtime_without_reading_mutable_definition(
    monkeypatch,
):
    solution_id, workflow_id = uuid4(), uuid4()
    old_id, new_id = uuid4(), uuid4()
    workflow = SimpleNamespace(id=workflow_id, solution_id=solution_id)
    solution = SimpleNamespace(
        id=solution_id,
        status="active",
        organization_id=None,
        allow_outbound_access=False,
        active_deployment_id=old_id,
    )
    session = SimpleNamespace(
        execute=AsyncMock(return_value=_Result((workflow, solution)))
    )
    deployments = {
        old_id: _closure(
            deployment_id=old_id,
            solution_id=solution_id,
            workflow_id=workflow_id,
            source_text="old",
        ),
        new_id: _closure(
            deployment_id=new_id,
            solution_id=solution_id,
            workflow_id=workflow_id,
            source_text="new",
        ),
    }

    async def get_closure(_repo, deployment_id, _org_id):
        return deployments[deployment_id]

    monkeypatch.setattr(
        "src.services.solutions.deployment_runtime.SolutionDeploymentRepository.get_runtime_closure",
        get_closure,
    )

    old = await pin_workflow_runtime(session, workflow_id)
    solution.active_deployment_id = new_id
    new = await pin_workflow_runtime(session, workflow_id)

    assert old is not None and new is not None
    assert old.deployment_id == old_id
    assert old.name == "demo-old"
    assert new.deployment_id == new_id
    assert new.name == "demo-new"
    # The already materialized queue evidence remains pinned after promotion.
    assert old.queue_evidence()["solution_deployment_id"] == str(old_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("versioned", [False, True])
async def test_parameter_evidence_contract_preserves_preexisting_accepted_work(monkeypatch, versioned):
    solution_id, workflow_id, deployment_id = uuid4(), uuid4(), uuid4()
    workflow = SimpleNamespace(id=workflow_id, solution_id=solution_id, is_active=False)
    solution = SimpleNamespace(id=solution_id, status="active", organization_id=None,
        allow_outbound_access=False, active_deployment_id=uuid4())
    schema = {"type": "object", "properties": {"user": {"type": "string", "default": "root"}}}
    definition = {"parameters_schema": schema}
    if versioned:
        definition["parameters_schema_contract"] = WORKFLOW_PARAMETERS_SCHEMA_CONTRACT
    deployment = _closure(deployment_id=deployment_id, solution_id=solution_id,
        workflow_id=workflow_id, source_text="accepted", definition_extra=definition)
    deployment.state = "superseded"
    session = SimpleNamespace(execute=AsyncMock(return_value=_Result((workflow, solution))))

    async def get_closure(_repo, requested_id, org, requested_solution):
        assert requested_id == deployment_id
        assert org is None and requested_solution == solution_id
        return deployment

    monkeypatch.setattr(
        "src.services.solutions.deployment_runtime.SolutionDeploymentRepository.get_runtime_closure",
        get_closure,
    )
    pinned = await resolve_pinned_workflow_runtime(session, deployment_id, workflow_id)
    evidence = pinned.queue_evidence()
    if versioned:
        assert evidence["workflow_parameters_schema"] == schema
        assert pinned.parameters_schema == schema
    else:
        assert "workflow_parameters_schema" not in evidence
        assert pinned.parameters_schema is None
    # Existing durable evidence has no schema key even if the old manifest
    # already stored schema metadata. It must still validate after rollout.
    durable = dict(evidence)
    if not versioned:
        durable.pop("workflow_parameters_schema", None)
    verify_runtime_evidence(str(deployment_id), durable, durable,
        sha256_digest(canonical_json(durable)), evidence)


@pytest.mark.asyncio
async def test_bounded_deployment_pin_preserves_live_duration_and_output_limits(monkeypatch):
    solution_id, workflow_id, deployment_id = uuid4(), uuid4(), uuid4()
    workflow = SimpleNamespace(id=workflow_id, solution_id=solution_id)
    solution = SimpleNamespace(
        id=solution_id,
        status="active",
        organization_id=None,
        allow_outbound_access=False,
        active_deployment_id=deployment_id,
    )
    bounds = {
        "max_duration_seconds": 30,
        "max_external_calls": 10,
        "max_records_read": 100,
        "max_output_bytes": 2048,
    }
    deployment = _closure(
        deployment_id=deployment_id,
        solution_id=solution_id,
        workflow_id=workflow_id,
        source_text="bounded",
        runtime_bounds=bounds,
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=_Result((workflow, solution))))

    async def get_closure(_repo, _deployment_id, _org):
        return deployment

    monkeypatch.setattr(
        "src.services.solutions.deployment_runtime.SolutionDeploymentRepository.get_runtime_closure",
        get_closure,
    )
    pinned = await pin_workflow_runtime(session, workflow_id)

    assert pinned is not None
    evidence = pinned.queue_evidence()
    assert evidence["workflow_runtime_bounds"] == bounds
    assert workflow_data_from_evidence(str(deployment_id), evidence)[
        "workflow_runtime_bounds"
    ] == bounds


@pytest.mark.asyncio
async def test_queued_handoff_pin_survives_owner_rollback_to_live(monkeypatch):
    solution_id, workflow_id, deployment_id = uuid4(), uuid4(), uuid4()
    workflow = SimpleNamespace(id=workflow_id, solution_id=None)
    solution = SimpleNamespace(
        id=solution_id,
        status="active",
        organization_id=None,
        allow_outbound_access=False,
        active_deployment_id=None,
    )
    deployment = _closure(
        deployment_id=deployment_id,
        solution_id=solution_id,
        workflow_id=workflow_id,
        source_text="old handoff",
    )
    deployment.state = "superseded"
    deployment.validation_result = {
        "schema_version": "bifrost.workspace-live-handoff/v1",
        "workflow_ids": [str(workflow_id)],
    }
    session = SimpleNamespace(
        execute=AsyncMock(return_value=_Result((workflow, None))),
        get=AsyncMock(return_value=solution),
    )

    async def get_by_id(_repo, requested_id):
        return deployment if requested_id == deployment_id else None

    monkeypatch.setattr(
        "src.services.solutions.deployment_runtime.SolutionDeploymentRepository.get_by_id_for_runtime",
        get_by_id,
    )

    assert await pin_workflow_runtime(session, workflow_id) is None
    pinned = await resolve_pinned_workflow_runtime(session, deployment_id, workflow_id)
    assert pinned.deployment_id == deployment_id
    assert pinned.workflow_id == workflow_id
    assert pinned.source_hash == deployment.resolution_map["sources"]["workflows/demo.py"]["content_hash"]

    deployment.validation_result = None
    with pytest.raises(DeploymentRuntimeError, match="does not belong"):
        await resolve_pinned_workflow_runtime(session, deployment_id, workflow_id)


@pytest.mark.asyncio
async def test_solution_without_active_deployment_never_falls_back_to_mutable_row():
    solution_id, workflow_id = uuid4(), uuid4()
    workflow = SimpleNamespace(id=workflow_id, solution_id=solution_id)
    solution = SimpleNamespace(
        id=solution_id,
        status="active",
        organization_id=None,
        allow_outbound_access=False,
        active_deployment_id=None,
        execution_runtime_mode="deployment-v1",
    )
    session = SimpleNamespace(
        execute=AsyncMock(return_value=_Result((workflow, solution)))
    )

    with pytest.raises(DeploymentRuntimeError, match="no active deployment"):
        await pin_workflow_runtime(session, workflow_id)


@pytest.mark.asyncio
async def test_explicit_legacy_solution_without_deployment_uses_repo_compatibility():
    solution_id, workflow_id = uuid4(), uuid4()
    workflow = SimpleNamespace(id=workflow_id, solution_id=solution_id)
    solution = SimpleNamespace(
        id=solution_id,
        status="active",
        organization_id=None,
        allow_outbound_access=False,
        active_deployment_id=None,
        execution_runtime_mode="repo-v1",
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=_Result((workflow, solution))))

    assert await pin_workflow_runtime(session, workflow_id) is None


def test_tampered_queue_evidence_is_rejected_against_durable_manifest_evidence():
    deployment_id = str(uuid4())
    authoritative = {
        "solution_deployment_id": deployment_id,
        "workflow_path": "workflows/run.py",
    }
    evidence_hash = sha256_digest(canonical_json(authoritative))
    tampered = {**authoritative, "workflow_path": "workflows/other.py"}

    with pytest.raises(DeploymentRuntimeError, match="queue evidence"):
        verify_runtime_evidence(
            deployment_id, tampered, authoritative, evidence_hash, authoritative
        )


@pytest.mark.asyncio
async def test_child_call_inherits_superseded_parent_deployment(monkeypatch):
    solution_id, workflow_id = uuid4(), uuid4()
    old_id, active_id = uuid4(), uuid4()
    workflow = SimpleNamespace(id=workflow_id, solution_id=solution_id)
    solution = SimpleNamespace(
        id=solution_id,
        status="active",
        organization_id=None,
        allow_outbound_access=False,
        active_deployment_id=active_id,
    )
    old = _closure(
        deployment_id=old_id,
        solution_id=solution_id,
        workflow_id=workflow_id,
        source_text="old",
    )
    old.state = "superseded"
    session = SimpleNamespace(execute=AsyncMock(return_value=_Result((workflow, solution))))

    async def get_by_id(_repo, deployment_id):
        return old if deployment_id == old_id else None

    async def get_closure(_repo, deployment_id, _org):
        return old if deployment_id == old_id else None

    monkeypatch.setattr(
        "src.services.solutions.deployment_runtime.SolutionDeploymentRepository.get_by_id_for_runtime",
        get_by_id,
    )
    monkeypatch.setattr(
        "src.services.solutions.deployment_runtime.SolutionDeploymentRepository.get_runtime_closure",
        get_closure,
    )
    pinned = await pin_workflow_runtime(
        session, workflow_id, caller_deployment_id=old_id
    )
    assert pinned is not None and pinned.deployment_id == old_id


@pytest.mark.asyncio
async def test_pinned_runtime_rejects_inactive_solution():
    solution_id, workflow_id, deployment_id = uuid4(), uuid4(), uuid4()
    workflow = SimpleNamespace(id=workflow_id, solution_id=solution_id)
    solution = SimpleNamespace(id=solution_id, status="inactive")
    session = SimpleNamespace(execute=AsyncMock(return_value=_Result((workflow, solution))))

    with pytest.raises(DeploymentRuntimeError, match="Solution is not active"):
        await pin_workflow_runtime(
            session, workflow_id, caller_deployment_id=deployment_id
        )


@pytest.mark.asyncio
async def test_cross_solution_child_uses_exact_dependency_deployment(monkeypatch):
    owner_solution_id, target_solution_id, workflow_id = uuid4(), uuid4(), uuid4()
    caller_id, dependency_id, current_target_id = uuid4(), uuid4(), uuid4()
    workflow = SimpleNamespace(id=workflow_id, solution_id=target_solution_id)
    solution = SimpleNamespace(
        id=target_solution_id,
        status="active",
        organization_id=None,
        allow_outbound_access=False,
        active_deployment_id=current_target_id,
    )
    caller = SimpleNamespace(
        id=caller_id,
        solution_id=owner_solution_id,
        dependencies=[
            SimpleNamespace(
                dependency_solution_id=target_solution_id,
                dependency_deployment_id=dependency_id,
            )
        ],
    )
    dependency = _closure(
        deployment_id=dependency_id,
        solution_id=target_solution_id,
        workflow_id=workflow_id,
        source_text="dependency",
    )
    dependency.state = "superseded"
    session = SimpleNamespace(execute=AsyncMock(return_value=_Result((workflow, solution))))

    async def get_by_id(_repo, deployment_id):
        return caller if deployment_id == caller_id else None

    async def get_closure(_repo, deployment_id, _org):
        return dependency if deployment_id == dependency_id else None

    monkeypatch.setattr(
        "src.services.solutions.deployment_runtime.SolutionDeploymentRepository.get_by_id_for_runtime",
        get_by_id,
    )
    monkeypatch.setattr(
        "src.services.solutions.deployment_runtime.SolutionDeploymentRepository.get_runtime_closure",
        get_closure,
    )
    pinned = await pin_workflow_runtime(
        session, workflow_id, caller_deployment_id=caller_id
    )
    assert pinned is not None and pinned.deployment_id == dependency_id


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["active", "superseded", "ready"])
async def test_accepted_pin_reads_exact_owned_deployment_after_registration_retirement(monkeypatch, state):
    solution_id, workflow_id, deployment_id = uuid4(), uuid4(), uuid4()
    workflow = SimpleNamespace(id=workflow_id, solution_id=solution_id, is_active=False)
    solution = SimpleNamespace(id=solution_id, status="active", organization_id=uuid4(),
        allow_outbound_access=False, active_deployment_id=uuid4())
    deployment = _closure(deployment_id=deployment_id, solution_id=solution_id,
        workflow_id=workflow_id, source_text="accepted")
    deployment.state = state
    session = SimpleNamespace(execute=AsyncMock(return_value=_Result((workflow, solution))))

    async def get_closure(_repo, requested_id, org_id, requested_solution_id):
        assert (requested_id, org_id, requested_solution_id) == (
            deployment_id, solution.organization_id, solution_id)
        return deployment

    monkeypatch.setattr(
        "src.services.solutions.deployment_runtime.SolutionDeploymentRepository.get_runtime_closure",
        get_closure)
    if state == "ready":
        with pytest.raises(DeploymentRuntimeError, match="not executable"):
            await resolve_pinned_workflow_runtime(session, deployment_id, workflow_id)
    else:
        pinned = await resolve_pinned_workflow_runtime(session, deployment_id, workflow_id)
        assert pinned.deployment_id == deployment_id
        assert pinned.name == "demo-accepted"


@pytest.mark.asyncio
async def test_accepted_pin_rejects_workflow_reassigned_to_another_solution(monkeypatch):
    solution_id, original_solution_id, workflow_id, deployment_id = uuid4(), uuid4(), uuid4(), uuid4()
    workflow = SimpleNamespace(id=workflow_id, solution_id=solution_id, is_active=False)
    solution = SimpleNamespace(id=solution_id, status="active", organization_id=None)
    original = _closure(deployment_id=deployment_id, solution_id=original_solution_id,
        workflow_id=workflow_id, source_text="original")
    session = SimpleNamespace(execute=AsyncMock(return_value=_Result((workflow, solution))))
    monkeypatch.setattr(
        "src.services.solutions.deployment_runtime.SolutionDeploymentRepository.get_runtime_closure",
        AsyncMock(return_value=original))
    with pytest.raises(DeploymentRuntimeError, match="does not belong"):
        await resolve_pinned_workflow_runtime(session, deployment_id, workflow_id)


@pytest.mark.parametrize("tampered", [False, True])
def test_runtime_pin_retains_installed_name_and_validates_its_sealed_binding(tampered):
    from src.services.solutions.deployment_runtime import _pin_from_deployment
    from src.services.solutions.source_revision import legacy_registration_name_evidence

    sid, wid, did = uuid4(), uuid4(), uuid4()
    evidence = legacy_registration_name_evidence("demo", "Friendly task")
    if tampered:
        evidence["fields"]["source_name"] = "Forged declaration"
    deployment = _closure(deployment_id=did, solution_id=sid, workflow_id=wid, source_text="old",
        definition_extra={"name": "demo", "legacy_registration_name_evidence": evidence})
    solution = SimpleNamespace(id=sid, allow_outbound_access=False)
    if tampered:
        with pytest.raises(DeploymentRuntimeError, match="name differs from immutable evidence"):
            _pin_from_deployment(wid, solution, deployment, allow_superseded=False)
    else:
        pin = _pin_from_deployment(wid, solution, deployment, allow_superseded=False)
        assert pin.name == "demo" and pin.function_name == "demo"
        assert pin.queue_evidence()["workflow_name"] == "demo"
