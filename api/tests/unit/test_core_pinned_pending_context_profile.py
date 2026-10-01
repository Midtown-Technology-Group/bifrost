"""Pure source-produced deployment vectors; runtime belongs to the supported lane."""

from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from types import SimpleNamespace
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from bifrost.solution_delivery_review import (
    WORKFLOW_RECIPE_SCHEMA,
    ReviewedWorkflowRecipe,
)
from bifrost.workflow_parameters import WorkflowParameterCompiler
from src.core import redis_client as redis_module
from src.core.module_cache_contract import WORKSPACE_GENERATION_KEY
from src.jobs.rabbitmq import _message_headers, infer_idempotency_key
from src.models.orm.solution_deployments import SolutionDeployment
from src.models.orm.solutions import Solution
from src.services.execution import async_executor
from src.services.execution.async_executor import _pending_dispatch_envelope
from src.services.solutions.deployment_manifest import (
    CompiledDeploymentManifest,
    DeploymentGitProvenance,
    DeploymentResolutionMap,
    DeploymentSource,
    RuntimeSourceResolution,
    canonical_json,
    sha256_digest,
)
from src.services.solutions.deployment_runtime import _pin_from_deployment
from src.services.solutions.deployment_storage import (
    deployment_runtime_prefix,
    deployment_source_artifact_key,
)
from src.services.solutions.workflow_revision_recipe import (
    compile_workflow_registrations,
)

from tests.parity.core.capture import CapturedStep
from tests.parity.core.environment import SOURCE
from tests.parity.core.pinned import (
    FUNCTION,
    PORTABLE_REF,
    SOURCE_COMMIT,
    SOURCE_HASH,
    SOURCE_PATH,
    SOURCE_SHA256,
    PinnedEnvironment,
    PinnedTransportEvidence,
    capture_pinned_row,
    generation_read,
)
from tests.parity.core.pinned_profile import PinnedProfile
from tests.parity.core.profile import PENDING_TYPES, PROFILE, digest
from tests.unit.test_core_pending_context_profile import (
    pending_of,
    pending_vector,
    rehash,
)


def pinned_vector(label="left"):
    observation, original_bindings = pending_vector(label)
    ids = {
        role: str(uuid5(NAMESPACE_URL, f"pinned:{label}:{role}"))
        for role in (
            "readiness",
            "workflow",
            "user",
            "org",
            "setup_user",
            "foreign_org",
            "solution",
            "deployment",
            "role",
        )
    }
    replacements = {
        raw: ids[role] for raw, role in original_bindings.items() if role in ids
    }

    def replace(value):
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        return replacements.get(value, value) if isinstance(value, str) else value

    observation.database = replace(observation.database)
    bindings = {
        replacements.get(raw, raw): role for raw, role in original_bindings.items()
    }
    bindings.update({raw: role for role, raw in ids.items()})
    generation = f"{label}-observed-generation"
    bindings[generation] = "observed-workspace-generation"
    recipe = ReviewedWorkflowRecipe.model_validate(
        {
            "schema_version": WORKFLOW_RECIPE_SCHEMA,
            "solution_id": ids["solution"],
            "files": {SOURCE_PATH: SOURCE_PATH},
            "workflows": [
                {
                    "id": ids["workflow"],
                    "path": SOURCE_PATH,
                    "function_name": FUNCTION,
                    "organization_id": ids["org"],
                    "runtime_bounds": {
                        "max_duration_seconds": 60,
                        "max_external_calls": 10,
                        "max_records_read": 100,
                        "max_output_bytes": 4096,
                    },
                    "controls": {
                        "timeout_seconds": 60,
                        "role_ids": [ids["role"]],
                        "endpoint_enabled": False,
                        "public_endpoint": False,
                    },
                }
            ],
        }
    ).model_dump(mode="json")
    entities = compile_workflow_registrations(
        ReviewedWorkflowRecipe.model_validate(recipe),
        {SOURCE_PATH: SOURCE.read_bytes()},
        WorkflowParameterCompiler(),
    )
    prefix = deployment_runtime_prefix(ids["solution"], ids["deployment"])
    artifact = deployment_source_artifact_key(ids["solution"], ids["deployment"])
    bindings.update(
        {
            prefix: "runtime-prefix",
            artifact: "source-artifact-key",
            prefix + SOURCE_PATH: "source-storage-path",
        }
    )
    resolution = DeploymentResolutionMap(
        workflows=entities,
        sources={
            SOURCE_PATH: RuntimeSourceResolution(
                object_key=prefix + SOURCE_PATH, content_hash=SOURCE_HASH
            )
        },
    )
    bundle = digest(
        {
            "schema_version": "bifrost.initial-reviewed-workflow-install/v1",
            "reviewed_recipe": recipe,
            "source_commit_sha": SOURCE_COMMIT,
            "source_hashes": {SOURCE_PATH: SOURCE_HASH},
        }
    )
    manifest = CompiledDeploymentManifest(
        solution_id=UUID(ids["solution"]),
        deployment_id=UUID(ids["deployment"]),
        bundle_hash=bundle,
        resolution_map_hash=sha256_digest(canonical_json(resolution)),
        source=DeploymentSource(artifact_key=artifact, runtime_prefix=prefix),
        workflows=entities,
        git=DeploymentGitProvenance(commit_sha=SOURCE_COMMIT),
    )
    solution = {
        "id": ids["solution"],
        "organization_id": ids["org"],
        "status": "active",
        "active_deployment_id": ids["deployment"],
        "execution_runtime_mode": "deployment-v1",
        "allow_outbound_access": False,
        "git_connected": False,
    }
    deployment = {
        "id": ids["deployment"],
        "solution_id": ids["solution"],
        "organization_id": ids["org"],
        "created_by": ids["setup_user"],
        "state": "active",
        "bundle_hash": bundle,
        "compiled_manifest_hash": manifest.content_hash(),
        "resolution_map_hash": manifest.resolution_map_hash,
        "compiled_manifest": manifest.model_dump(mode="json", exclude_none=True),
        "resolution_map": resolution.model_dump(mode="json", exclude_none=True),
        "git_commit_sha": SOURCE_COMMIT,
        "runtime_storage_prefix": prefix,
        "source_artifact_key": artifact,
        "validation_result": validation_receipt(
            deployment_id=ids["deployment"],
            solution_id=ids["solution"],
            organization_id=ids["org"],
            manifest_hash=manifest.content_hash(),
            resolution_hash=manifest.resolution_map_hash,
            recipe=recipe,
            workflow_id=ids["workflow"],
        ),
    }
    runtime = _pin_from_deployment(
        UUID(ids["workflow"]),
        SimpleNamespace(**{**solution, "id": UUID(ids["solution"])}),
        SimpleNamespace(
            **{**deployment, "id": UUID(ids["deployment"]), "dependencies": []}
        ),
        allow_superseded=False,
    )
    evidence = runtime.queue_evidence()
    row = observation.database["executions"][0]
    request = deepcopy(row["dispatch_evidence"]["request"])
    request["dispatch_metadata"]["path"] = SOURCE_PATH
    dispatch = _pending_dispatch_envelope(
        request,
        solution_deployment_id=ids["deployment"],
        runtime_evidence=deepcopy(evidence),
        runtime_mode="deployment-v1",
    )
    row.update(
        runtime_mode="deployment-v1",
        solution_deployment_id=ids["deployment"],
        runtime_evidence=deepcopy(evidence),
        runtime_evidence_hash=digest(evidence),
        dispatch_evidence=dispatch,
        dispatch_evidence_hash=digest(dispatch),
    )
    row["execution_context"]["workspace_generation"] = generation
    pending = pending_of(observation)
    pending.update(
        runtime_mode="deployment-v1",
        solution_deployment_id=ids["deployment"],
        runtime_evidence=deepcopy(evidence),
    )
    body = observation.database["work_deliveries"][0]["envelope"]["body"]
    body["dispatch_metadata"] = deepcopy(request["dispatch_metadata"])
    body["solution_deployment_id"] = ids["deployment"]
    observation.database.update(
        solutions=[solution],
        solution_deployments=[deployment],
        solution_deployment_dependencies=[],
        workflows=[],
    )
    witness = {
        "source": WORKSPACE_GENERATION_KEY,
        "execution_id": row["id"],
        "before": generation_read(
            generation,
            observation.before,
            observation.before + timedelta(milliseconds=1),
        ),
        "after": generation_read(
            generation, observation.after - timedelta(milliseconds=1), observation.after
        ),
    }
    profile = PinnedProfile({ids["solution"]: recipe}, {row["id"]: witness})
    return observation, bindings, profile


def install_evidence(observation, profile):
    row = observation.database["executions"][0]
    deployment = observation.database["solution_deployments"][0]
    deployment["compiled_manifest"]["resolution_map_hash"] = deployment[
        "resolution_map_hash"
    ] = digest(deployment["resolution_map"])
    deployment["compiled_manifest_hash"] = digest(deployment["compiled_manifest"])
    deployment["validation_result"] = validation_receipt(
        deployment_id=deployment["id"],
        solution_id=deployment["solution_id"],
        organization_id=deployment["organization_id"],
        manifest_hash=deployment["compiled_manifest_hash"],
        resolution_hash=deployment["resolution_map_hash"],
        recipe=profile.recipes[deployment["solution_id"]],
        workflow_id=row["workflow_id"],
    )
    for key in ("bundle_hash", "compiled_manifest_hash"):
        row["runtime_evidence"][key] = deployment[key]
    synchronize_evidence(observation)


def validation_receipt(
    *,
    deployment_id,
    solution_id,
    organization_id,
    manifest_hash,
    resolution_hash,
    recipe,
    workflow_id,
):
    evidence = digest(
        {
            "schema_version": "bifrost.initial-reviewed-workflow-install/v1",
            "solution_id": solution_id,
            "deployment_id": deployment_id,
            "organization_id": organization_id,
            "pointer": None,
            "runtime_mode": "repo-v1",
            "manifest_hash": manifest_hash,
            "resolution_hash": resolution_hash,
            "reviewed_recipe": recipe,
            "workflow_ids": [workflow_id],
            "source_hashes": {SOURCE_PATH: SOURCE_HASH},
        }
    )
    return {
        "schema_version": "bifrost.initial-reviewed-workflow-install/v1",
        "preflight_evidence_id": evidence,
        "workflow_ids": [workflow_id],
        "source_hashes": {SOURCE_PATH: SOURCE_HASH},
    }


def synchronize_evidence(observation):
    row = observation.database["executions"][0]
    row["runtime_evidence_hash"] = digest(row["runtime_evidence"])
    row["dispatch_evidence"]["publish"]["runtime_evidence"] = deepcopy(
        row["runtime_evidence"]
    )
    pending_of(observation)["runtime_evidence"] = deepcopy(row["runtime_evidence"])
    rehash(row)
    for attempt in observation.database["workflow_execution_attempts"]:
        attempt.update(
            runtime_evidence_hash=row["runtime_evidence_hash"],
            dispatch_evidence_hash=row["dispatch_evidence_hash"],
        )


def source_capture(observation):
    row = observation.database["executions"][0]
    pending = pending_of(observation)
    evidence = row["runtime_evidence"]
    token = "synthetic-owned-attempt-fence"
    observation.database["workflow_execution_attempts"] = [
        {
            "id": str(uuid5(NAMESPACE_URL, row["id"] + ":attempt")),
            "execution_id": row["id"],
            "claim_token": token,
            "attempt_number": 1,
            "status": "succeeded",
            "runtime_evidence_hash": row["runtime_evidence_hash"],
            "dispatch_evidence_hash": row["dispatch_evidence_hash"],
            "duration_ms": None,
            "peak_memory_bytes": None,
            "cpu_total_seconds": None,
        }
    ]
    claims = {
        "engine_execution_id": row["id"],
        "engine_solution_id": evidence["solution_id"],
        "engine_global_repo_access": False,
        "engine_attempt_token_digest": sha256(token.encode()).hexdigest(),
        "engine_attempt_token_present": True,
        "org_id": pending["org_id"],
        "delegated_user_id": pending["user_id"],
        "delegated_email": pending["user_email"],
        "delegated_name": pending["user_name"],
        "delegated_is_superuser": False,
        "delegated_is_provider_org": False,
        "delegated_is_external": False,
    }
    path = evidence["runtime_storage_prefix"] + SOURCE_PATH
    request = {
        "before": observation.before.isoformat(),
        "after": observation.after.isoformat(),
        "authorization": {"claims": claims},
        "method": "GET",
        "path": "/api/sdk/modules/" + path,
        "query": [],
        "request": None,
        "status": 200,
        "upstream_status": 200,
        "response": {
            "content": SOURCE.read_text(),
            "hash": SOURCE_SHA256,
            "path": path,
        },
    }
    return CapturedStep(observation, PinnedTransportEvidence(source_requests=[request]))


def test_pinned_independent_projection_is_complete_and_preserves_raw():
    left, left_bindings, profile = pinned_vector()
    right, right_bindings, other = pinned_vector("right")
    profile.recipes.update(other.recipes)
    profile.generation_witnesses.update(other.generation_witnesses)
    original = deepcopy(left)
    projected = profile.canonicalize([left], left_bindings)
    assert projected == profile.canonicalize([right], right_bindings)
    pending = projected[0]["database"]["work_deliveries"][0]["envelope"]["body"][
        "pending_context"
    ]
    assert pending["solution_deployment_id"] == {
        "identity": "deployment",
        "wire_type": "str",
    }
    assert pending["runtime_evidence"]["runtime_storage_prefix"] == {
        "identity": "runtime-prefix",
        "wire_type": "str",
    }
    assert pending["runtime_evidence"]["workflow_path"] == SOURCE_PATH
    assert (
        pending["runtime_evidence"]["workflow_parameters_schema"]
        == pending_of(left)["runtime_evidence"]["workflow_parameters_schema"]
    )
    assert left == original
    assert "solution_id" not in left.database["executions"][0]["execution_context"]


def test_pinned_real_producer_uses_nested_evidence_and_metadata_path(monkeypatch):
    observation, bindings, profile = pinned_vector()
    row = observation.database["executions"][0]
    stored, published = [], []
    created = observation.before + timedelta(seconds=1)

    class ImmediateRedis:
        async def setex(self, key, ttl, value):
            stored.append(value)

    client = redis_module.RedisClient()

    async def connection():
        return ImmediateRedis()

    async def publish(queue, message, **kwargs):
        published.append(
            {
                "body": deepcopy(message),
                "headers": _message_headers(
                    message,
                    queue,
                    message_id=infer_idempotency_key(queue, message),
                    headers={
                        "x-enqueued-at": (
                            created + timedelta(milliseconds=1)
                        ).isoformat()
                    },
                ),
            }
        )

    monkeypatch.setattr(client, "_get_redis", connection)
    monkeypatch.setattr(
        redis_module, "datetime", SimpleNamespace(now=lambda _: created)
    )
    monkeypatch.setattr(async_executor, "get_redis_client", lambda: client)
    monkeypatch.setattr(
        async_executor,
        "get_settings",
        lambda: SimpleNamespace(work_delivery_backend="postgres"),
    )
    monkeypatch.setattr(async_executor, "publish_message", publish)
    coroutine = async_executor._publish_pending(**row["dispatch_evidence"]["publish"])
    with pytest.raises(StopIteration):
        coroutine.send(None)
    assert len(stored) == len(published) == 1
    body = published[0]["body"]
    assert "runtime_evidence" not in body and "file_path" not in body
    assert body["dispatch_metadata"]["path"] == SOURCE_PATH
    assert body["solution_deployment_id"] == row["solution_deployment_id"]
    assert len(body["pending_context"]) == 25
    assert len(body["pending_context"]["runtime_evidence"]) == 22
    assert published[0] == observation.database["work_deliveries"][0]["envelope"]
    profile.validate_pending_context(observation, bindings)


@pytest.mark.parametrize("field", list(PENDING_TYPES))
def test_pinned_whole_pending_schema_requires_every_produced_field(field):
    observation, bindings, profile = pinned_vector()
    del pending_of(observation)[field]
    with pytest.raises(AssertionError, match="Pending context schema differs"):
        profile.validate_pending_context(observation, bindings)


@pytest.mark.parametrize(
    "field,value",
    [
        ("sync", 1),
        ("cancelled", 0),
        ("org_id_overridden", 0),
        ("parameters", []),
        ("embed", None),
        ("user_id", 7),
    ],
)
def test_pinned_common_wire_type_gate_is_not_bypassed(field, value):
    observation, bindings, profile = pinned_vector()
    pending_of(observation)[field] = value
    with pytest.raises(AssertionError, match="wire type differs"):
        profile.validate_pending_context(observation, bindings)


@pytest.mark.parametrize(
    "field",
    [
        "workflow_runtime_bounds",
        "workflow_parameters_schema",
        "workflow_timeout_seconds",
    ],
)
def test_pinned_runtime_cannot_omit_accepted_producer_fields(field):
    observation, bindings, profile = pinned_vector()
    del observation.database["executions"][0]["runtime_evidence"][field]
    synchronize_evidence(observation)
    with pytest.raises(AssertionError, match="runtime evidence schema differs"):
        profile.validate_pending_context(observation, bindings)


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_name", "foreign-name"),
        ("workflow_function_name", "foreign_function"),
        ("workflow_path", SOURCE_PATH + "/foreign"),
        ("workflow_timeout_seconds", 61),
        ("workflow_time_saved", True),
        ("workflow_value", 0),
        ("workflow_cache_ttl_seconds", False),
        ("solution_global_repo_access", True),
        ("deployment_source_hashes", {}),
        ("workflow_runtime_bounds", {"max_duration_seconds": 60}),
        ("workflow_parameters_schema", {}),
    ],
)
def test_pinned_coherent_runtime_hashes_do_not_authorize_drift(field, value):
    observation, bindings, profile = pinned_vector()
    observation.database["executions"][0]["runtime_evidence"][field] = value
    synchronize_evidence(observation)
    with pytest.raises(AssertionError, match="differs from immutable producer"):
        profile.validate_pending_context(observation, bindings)


def test_pinned_coherent_manifest_definition_must_still_match_reviewed_recipe():
    observation, bindings, profile = pinned_vector()
    deployment = observation.database["solution_deployments"][0]
    for document in ("compiled_manifest", "resolution_map"):
        deployment[document]["workflows"][PORTABLE_REF]["definition"][
            "timeout_seconds"
        ] = 59
    install_evidence(observation, profile)
    with pytest.raises(AssertionError, match="differs from reviewed source recipe"):
        profile.validate_pending_context(observation, bindings)


@pytest.mark.parametrize(
    "plane",
    [
        "state",
        "pointer",
        "scope",
        "caller-pin",
        "extra-evidence",
        "extra-pending",
        "top-level-evidence",
        "encryption",
        "header",
        "source-path",
    ],
)
def test_pinned_raw_state_body_and_owner_gates(plane):
    observation, bindings, profile = pinned_vector()
    row = observation.database["executions"][0]
    delivery = observation.database["work_deliveries"][0]
    if plane == "state":
        observation.database["solution_deployments"][0]["state"] = "ready"
    elif plane == "pointer":
        observation.database["solutions"][0]["active_deployment_id"] = None
    elif plane == "scope":
        observation.database["solutions"][0]["organization_id"] = next(
            raw for raw, role in bindings.items() if role == "foreign_org"
        )
    elif plane == "caller-pin":
        row["dispatch_evidence"]["request"]["caller_solution_deployment_id"] = row[
            "solution_deployment_id"
        ]
        rehash(row)
    elif plane == "extra-evidence":
        row["runtime_evidence"]["extra"] = "opaque"
        synchronize_evidence(observation)
    elif plane == "extra-pending":
        pending_of(observation)["execution_attempt_id"] = "unproduced"
    elif plane == "top-level-evidence":
        delivery["envelope"]["body"]["runtime_evidence"] = deepcopy(
            row["runtime_evidence"]
        )
    elif plane == "encryption":
        delivery["encrypted_envelope_present"] = False
    elif plane == "header":
        delivery["envelope"]["headers"]["x-original-message-id"] = row["workflow_id"]
    else:
        for section in ("request", "publish"):
            row["dispatch_evidence"][section]["dispatch_metadata"]["path"] = (
                row["runtime_evidence"]["runtime_storage_prefix"] + SOURCE_PATH
            )
        delivery["envelope"]["body"]["dispatch_metadata"]["path"] = row[
            "dispatch_evidence"
        ]["publish"]["dispatch_metadata"]["path"]
        rehash(row)
    with pytest.raises(AssertionError):
        profile.validate_pending_context(observation, bindings)


@pytest.mark.parametrize(
    "field",
    [
        "solution_id",
        "solution_deployment_id",
        "workflow_organization_id",
        "runtime_storage_prefix",
    ],
)
def test_pinned_known_foreign_identity_cannot_be_laundered(field):
    observation, bindings, profile = pinned_vector()
    foreign, foreign_bindings, _ = pinned_vector("foreign")
    bindings.update({raw: "other-" + role for raw, role in foreign_bindings.items()})
    observation.database["executions"][0]["runtime_evidence"][field] = foreign.database[
        "executions"
    ][0]["runtime_evidence"][field]
    synchronize_evidence(observation)
    with pytest.raises(AssertionError, match="differs from immutable producer"):
        profile.validate_pending_context(observation, bindings)


@pytest.mark.parametrize(
    "plane",
    [
        "missing",
        "null",
        "type",
        "foreign",
        "changed",
        "updating",
        "source",
        "execution",
        "clock",
    ],
)
def test_pinned_success_requires_independent_stable_generation_witness(plane):
    observation, bindings, profile = pinned_vector()
    row = observation.database["executions"][0]
    witness = profile.generation_witnesses[row["id"]]
    if plane == "missing":
        del row["execution_context"]["workspace_generation"]
    elif plane in {"null", "type", "foreign"}:
        row["execution_context"]["workspace_generation"] = {
            "null": None,
            "type": 1,
            "foreign": row["solution_deployment_id"],
        }[plane]
    elif plane == "changed":
        witness["after"]["value"] += "changed"
    elif plane == "updating":
        witness["before"]["value"] = "updating:unready"
    elif plane == "source":
        witness["source"] = "unapproved-generation-source"
    elif plane == "execution":
        witness["execution_id"] = row["workflow_id"]
    else:
        witness["after"]["completed_at"] = (
            observation.after + timedelta(seconds=1)
        ).isoformat()
    with pytest.raises(AssertionError):
        profile.validate_pending_context(observation, bindings)


@pytest.mark.parametrize("value", [None, "", "updating:unready", 1])
def test_pinned_generation_reader_never_accepts_cold_or_unstable_key(value):
    observation, _, _ = pinned_vector()
    with pytest.raises(AssertionError, match="cold or updating"):
        generation_read(value, observation.before, observation.after)


def test_pinned_failed_execution_does_not_require_success_generation():
    observation, bindings, profile = pinned_vector()
    row = observation.database["executions"][0]
    row["status"] = "Failed"
    row["execution_context"] = None
    profile.generation_witnesses.clear()
    profile.validate_pending_context(observation, bindings)


def test_pinned_success_cannot_erase_the_owned_committed_delivery():
    observation, bindings, profile = pinned_vector()
    observation.database["work_deliveries"].clear()
    with pytest.raises(
        AssertionError, match="successful pending delivery owner absent"
    ):
        profile.validate_pending_context(observation, bindings)


def test_pinned_source_caller_and_fence_are_joined_before_aliasing():
    observation, bindings, profile = pinned_vector()
    captured = source_capture(observation)
    original = deepcopy(captured)
    result = profile.transport(captured, bindings)
    assert (
        result["source_requests"][0]["authorization"]["claims"]["engine_solution_id"]
        == "identity:solution"
    )
    assert captured == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("delegated_user_id", "setup_user"),
        ("org_id", "foreign_org"),
        ("delegated_email", "foreign-email"),
        ("delegated_name", "Setup"),
        ("delegated_is_superuser", 0),
        ("delegated_is_provider_org", True),
        ("delegated_is_external", True),
        ("engine_solution_id", "foreign-solution"),
        ("engine_global_repo_access", 0),
        ("engine_attempt_token_digest", "foreign-digest"),
        ("engine_attempt_token_present", 1),
    ],
)
def test_pinned_source_rejects_raw_caller_solution_and_fence_drift(field, value):
    observation, bindings, profile = pinned_vector()
    captured = source_capture(observation)
    captured.transport.source_requests[0]["authorization"]["claims"][field] = next(
        (raw for raw, role in bindings.items() if role == value), value
    )
    with pytest.raises(AssertionError):
        profile.transport(captured, bindings)


def test_pinned_fence_cannot_belong_to_another_committed_execution():
    observation, bindings, profile = pinned_vector()
    captured = source_capture(observation)
    attempt = observation.database["workflow_execution_attempts"][0]
    foreign = str(uuid5(NAMESPACE_URL, "foreign-fence-execution"))
    bindings[foreign] = "other-execution"
    attempt["execution_id"] = foreign
    with pytest.raises(AssertionError, match="fence has no committed owner"):
        profile.transport(captured, bindings)


def test_pinned_opaque_private_identity_and_clock_strings_are_preserved():
    observation, bindings, profile = pinned_vector()
    row = observation.database["executions"][0]
    private = {
        "solution_id": row["runtime_evidence"]["solution_id"],
        "created_at": "2020-01-01T00:00:00+00:00",
        "runtime_storage_prefix": row["runtime_evidence"]["runtime_storage_prefix"],
    }
    for field in ("startup", "form_inputs", "embed", "parameters"):
        value = (
            {**row["parameters"], "private": deepcopy(private)}
            if field == "parameters"
            else {"private": deepcopy(private)}
        )
        pending_of(observation)[field] = deepcopy(value)
        row["execution_context"][field] = deepcopy(value)
        for section in ("request", "publish"):
            row["dispatch_evidence"][section][field] = deepcopy(value)
        if field == "parameters":
            row["parameters"] = deepcopy(value)
    rehash(row)
    projected = profile.canonicalize([observation], bindings)[0]
    pending = projected["database"]["work_deliveries"][0]["envelope"]["body"][
        "pending_context"
    ]
    for field in ("startup", "form_inputs", "embed", "parameters"):
        assert pending[field]["private"] == private


def test_pinned_prior_admission_clocks_and_generation_witness_survive_reread():
    observation, bindings, profile = pinned_vector()
    observation.database["solution_deployments"][0]["created_at"] = (
        observation.before + timedelta(milliseconds=1)
    ).isoformat()
    later = deepcopy(observation)
    later.before = observation.after + timedelta(seconds=1)
    later.after = later.before + timedelta(seconds=1)
    result = profile.canonicalize([observation, later], bindings)
    assert result[0] == result[1]
    with pytest.raises(AssertionError, match="clock outside measured operation"):
        profile.canonicalize([later], bindings)


def test_profile_policies_remain_fail_closed_across_runtime_modes():
    observation, bindings, _ = pinned_vector()
    with pytest.raises(AssertionError, match="requires R0 repo runtime"):
        PROFILE.validate_pending_context(observation, bindings)
    repo, repo_bindings = pending_vector()
    policy = PinnedProfile({})
    with pytest.raises(AssertionError, match="requires R1 deployment runtime"):
        policy.validate_runtime(
            repo, repo.database["executions"][0], {}, {}, repo_bindings
        )


def test_generation_after_read_is_independent_of_returned_context():
    observation, bindings, profile = pinned_vector()
    row = observation.database["executions"][0]
    witness = profile.generation_witnesses[row["id"]]
    fixture = object.__new__(PinnedEnvironment)
    fixture.bindings = deepcopy(bindings)
    fixture.generation_witnesses = {row["id"]: {**deepcopy(witness), "after": None}}
    reads = []

    async def independent_read():
        reads.append(True)
        return deepcopy(witness["after"])

    fixture.read_generation = independent_read
    row["execution_context"]["workspace_generation"] = "untrusted-returned-value"
    coroutine = fixture.witness_successful_generations([row])
    with pytest.raises(StopIteration):
        coroutine.send(None)
    assert reads == [True]
    assert fixture.generation_witnesses[row["id"]]["after"] == witness["after"]


def test_generation_source_read_uses_only_existing_redis_key(monkeypatch):
    from tests.parity.core import pinned as pinned_module

    observation, _, _ = pinned_vector()
    calls = []

    class ReadOnlyRedis:
        async def get(self, key):
            calls.append(key)
            return "existing-shared-generation"

    fixture = object.__new__(PinnedEnvironment)
    fixture.redis = ReadOnlyRedis()
    monkeypatch.setattr(pinned_module, "now", lambda: observation.before)
    coroutine = fixture.read_generation()
    with pytest.raises(StopIteration) as result:
        coroutine.send(None)
    assert calls == [WORKSPACE_GENERATION_KEY]
    assert result.value.value == generation_read(
        "existing-shared-generation", observation.before, observation.before
    )


def test_generation_after_read_rejects_actual_changed_source_value():
    observation, bindings, profile = pinned_vector()
    row = observation.database["executions"][0]
    fixture = object.__new__(PinnedEnvironment)
    fixture.bindings = deepcopy(bindings)
    fixture.generation_witnesses = deepcopy(profile.generation_witnesses)
    fixture.generation_witnesses[row["id"]]["after"] = None

    async def changed_source_read():
        return generation_read(
            "different-shared-generation", observation.before, observation.after
        )

    fixture.read_generation = changed_source_read
    coroutine = fixture.witness_successful_generations([row])
    with pytest.raises(AssertionError, match="changed during admission"):
        coroutine.send(None)
    assert fixture.generation_witnesses[row["id"]]["after"] is None


@pytest.mark.parametrize("winner", ["creator", "other-worker"])
def test_real_cold_producer_and_independent_after_read_bind_success(
    monkeypatch, winner
):
    from src.core import module_cache_sync

    from tests.parity.core import pinned as pinned_module

    observation, bindings, profile = pinned_vector()
    row = observation.database["executions"][0]
    candidate = UUID("11111111-1111-4111-8111-111111111111")
    calls, values = [], {}

    class ProducerRedis:
        def get(self, key):
            calls.append(("get", key))
            return values.get(key)

        def set(self, key, value, *, nx):
            calls.append(("set", key, value, nx))
            assert nx is True
            values[key] = value if winner == "creator" else "opaque-other-worker-winner"
            return winner == "creator"

    source = ProducerRedis()

    class ReadOnlyRedis:
        async def get(self, key):
            return source.get(key)

    fixture = object.__new__(PinnedEnvironment)
    fixture.redis = ReadOnlyRedis()
    fixture.bindings = {
        raw: role
        for raw, role in bindings.items()
        if role != "observed-workspace-generation"
    }
    fixture.generation = None
    clock = iter(
        [
            observation.before,
            observation.before + timedelta(milliseconds=1),
            observation.after - timedelta(milliseconds=1),
            observation.after,
        ]
    )
    monkeypatch.setattr(pinned_module, "now", lambda: next(clock))
    before_read = fixture.read_generation(allow_absent=True)
    with pytest.raises(StopIteration) as before_result:
        before_read.send(None)
    assert before_result.value.value["value"] is None
    assert calls == [("get", WORKSPACE_GENERATION_KEY)]
    monkeypatch.setattr(module_cache_sync, "_get_sync_redis", lambda: source)
    monkeypatch.setattr(
        module_cache_sync, "get_workspace_release_context", lambda: None
    )
    monkeypatch.setattr(module_cache_sync, "uuid4", lambda: candidate)
    # Only the actual isolated producer helper performs the simulated NX write.
    actual_generation = module_cache_sync.get_workspace_generation_sync()
    assert actual_generation == (
        candidate.hex if winner == "creator" else "opaque-other-worker-winner"
    )
    assert [call for call in calls if call[0] == "set"] == [
        ("set", WORKSPACE_GENERATION_KEY, candidate.hex, True)
    ]
    row["execution_context"]["workspace_generation"] = actual_generation
    fixture.generation_witnesses = {
        row["id"]: {
            "source": WORKSPACE_GENERATION_KEY,
            "execution_id": row["id"],
            "before": before_result.value.value,
            "after": None,
        }
    }
    after_read = fixture.witness_successful_generations([row])
    with pytest.raises(StopIteration):
        after_read.send(None)
    assert fixture.generation_witnesses[row["id"]]["before"]["value"] is None
    assert fixture.generation == values[WORKSPACE_GENERATION_KEY]
    assert fixture.bindings[fixture.generation] == "observed-workspace-generation"
    assert len([call for call in calls if call[0] == "set"]) == 1
    profile.generation_witnesses = fixture.generation_witnesses
    profile.validate_pending_context(observation, fixture.bindings)


@pytest.mark.parametrize("after", [None, "", "updating:unready", 1])
def test_cold_before_does_not_allow_absent_or_unready_success_after(after):
    observation, bindings, profile = pinned_vector()
    row = observation.database["executions"][0]
    witness = profile.generation_witnesses[row["id"]]
    witness["before"]["value"] = None
    witness["after"]["value"] = after
    with pytest.raises(AssertionError, match="generation read invalid"):
        profile.validate_pending_context(observation, bindings)


def test_cold_before_still_rejects_public_context_as_its_own_expectation():
    observation, bindings, profile = pinned_vector()
    row = observation.database["executions"][0]
    profile.generation_witnesses[row["id"]]["before"]["value"] = None
    row["execution_context"]["workspace_generation"] = "untrusted-returned-generation"
    with pytest.raises(AssertionError, match="successful source generation differs"):
        profile.validate_pending_context(observation, bindings)


@pytest.mark.parametrize("missing", [False, True])
def test_cold_success_requires_the_independent_after_record(missing):
    observation, bindings, profile = pinned_vector()
    row = observation.database["executions"][0]
    witness = profile.generation_witnesses[row["id"]]
    witness["before"]["value"] = None
    if missing:
        del witness["after"]
    else:
        witness["after"] = None
    with pytest.raises(AssertionError, match="generation (read|witness) absent"):
        profile.validate_pending_context(observation, bindings)


def test_unsubmitted_cold_execution_does_not_gain_after_witness_or_binding():
    observation, bindings, profile = pinned_vector()
    row = observation.database["executions"][0]
    row["status"] = "Failed"
    row["execution_context"] = None
    fixture = object.__new__(PinnedEnvironment)
    fixture.bindings = {
        raw: role
        for raw, role in bindings.items()
        if role != "observed-workspace-generation"
    }
    fixture.generation_witnesses = deepcopy(profile.generation_witnesses)
    fixture.generation_witnesses[row["id"]]["before"]["value"] = None
    fixture.generation_witnesses[row["id"]]["after"] = None

    async def forbidden_read():
        raise AssertionError("Unsuccessful execution must not gain an after witness")

    fixture.read_generation = forbidden_read
    coroutine = fixture.witness_successful_generations([row])
    with pytest.raises(StopIteration):
        coroutine.send(None)
    assert fixture.generation_witnesses[row["id"]]["after"] is None
    assert "observed-workspace-generation" not in fixture.bindings.values()
    profile.generation_witnesses = fixture.generation_witnesses
    profile.validate_pending_context(observation, fixture.bindings)


SOLUTION_OWNER_FIELDS = (
    "id",
    "organization_id",
    "status",
    "active_deployment_id",
    "execution_runtime_mode",
    "allow_outbound_access",
    "git_connected",
)
DEPLOYMENT_SOURCE_FIELDS = (
    "id",
    "solution_id",
    "organization_id",
    "created_by",
    "compiled_manifest",
    "resolution_map",
    "compiled_manifest_hash",
    "resolution_map_hash",
    "git_commit_sha",
    "runtime_storage_prefix",
    "source_artifact_key",
    "bundle_hash",
    "validation_result",
    "state",
)


@pytest.mark.parametrize("authority", [False, True, None])
def test_pinned_capture_retains_genuine_orm_outbound_authority(authority):
    observation, bindings, profile = pinned_vector()
    solution = observation.database["solutions"][0]
    original = deepcopy(solution)
    row = Solution(**{**solution, "allow_outbound_access": authority})
    column = Solution.__table__.columns["global_repo_access"]
    assert Solution.__mapper__.get_property_by_column(column).key == (
        "allow_outbound_access"
    )
    captured = capture_pinned_row(Solution, row)
    assert captured["global_repo_access"] is authority
    assert captured["allow_outbound_access"] is authority
    assert solution == original and row.allow_outbound_access is authority
    observation.database["solutions"] = [captured]
    if authority is False:
        profile.validate_pending_context(observation, bindings)
    else:
        with pytest.raises(AssertionError, match="installation owner differs"):
            profile.validate_pending_context(observation, bindings)


@pytest.mark.parametrize(
    ("model", "required"),
    [(Solution, SOLUTION_OWNER_FIELDS), (SolutionDeployment, DEPLOYMENT_SOURCE_FIELDS)],
)
def test_pinned_capture_retains_complete_required_source_fields(model, required):
    observation, bindings, profile = pinned_vector()
    original = observation.database[model.__tablename__][0]
    captured = capture_pinned_row(model, model(**original))
    assert set(required) <= captured.keys()
    assert all(captured[field] == original[field] for field in required)
    observation.database[model.__tablename__] = [captured]
    profile.validate_pending_context(observation, bindings)


def test_pinned_missing_authority_is_not_replaced_by_historical_column():
    observation, bindings, profile = pinned_vector()
    row = Solution(**observation.database["solutions"][0])
    captured = capture_pinned_row(Solution, row)
    del captured["allow_outbound_access"]
    assert captured["global_repo_access"] is False
    observation.database["solutions"] = [captured]
    with pytest.raises(KeyError, match="allow_outbound_access"):
        profile.validate_pending_context(observation, bindings)


@pytest.mark.parametrize(
    ("table", "field"),
    [("solutions", field) for field in SOLUTION_OWNER_FIELDS]
    + [("solution_deployments", field) for field in DEPLOYMENT_SOURCE_FIELDS],
)
def test_pinned_missing_required_source_field_still_fails(table, field):
    observation, bindings, profile = pinned_vector()
    del observation.database[table][0][field]
    with pytest.raises(KeyError, match=field):
        profile.validate_pending_context(observation, bindings)
