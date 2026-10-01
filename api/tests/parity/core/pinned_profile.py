"""Explicit deployment-v1 policy; source and hash relationships stay observable."""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

from bifrost.solution_delivery_review import ReviewedWorkflowRecipe
from bifrost.workflow_parameters import WorkflowParameterCompiler
from src.core.module_cache_contract import (
    WORKSPACE_GENERATION_KEY,
    WORKSPACE_UPDATING_PREFIX,
)
from src.services.solutions.deployment_manifest import validate_runtime_closure
from src.services.solutions.deployment_runtime import _pin_from_deployment
from src.services.solutions.deployment_storage import (
    deployment_runtime_prefix,
    deployment_source_artifact_key,
)
from src.services.solutions.workflow_revision_recipe import (
    compile_workflow_registrations,
)

from tests.parity.compare import assert_parity
from tests.parity.core.environment import SOURCE
from tests.parity.core.pinned import (
    PORTABLE_REF,
    SOURCE_COMMIT,
    SOURCE_HASH,
    SOURCE_PATH,
    assert_source_response,
)
from tests.parity.core.profile import (
    PENDING_PATH,
    CoreReferenceProfile,
    digest,
    fixture_binding,
    instant,
    validate_pending_context,
    validate_transport_owner,
)

PIN_CLOCKS = {
    "solutions": {"created_at", "updated_at"},
    "solution_deployments": {
        "created_at",
        "validated_at",
        "activated_at",
        "superseded_at",
    },
    "workflows": {
        "created_at",
        "updated_at",
        "last_seen_at",
        "api_key_created_at",
        "api_key_last_used_at",
        "api_key_expires_at",
    },
}
PIN_IDENTITIES = {
    "solutions": {"id", "organization_id", "active_deployment_id"},
    "solution_deployments": {
        "id",
        "organization_id",
        "solution_id",
        "parent_deployment_id",
        "base_deployment_id",
        "created_by",
        "source_artifact_key",
        "runtime_storage_prefix",
    },
    "workflows": {"id", "organization_id", "solution_id"},
    "solution_deployment_dependencies": {
        "deployment_id",
        "dependency_solution_id",
        "dependency_deployment_id",
    },
}
RUNTIME_IDENTITIES = {
    "solution_id",
    "solution_deployment_id",
    "workflow_organization_id",
    "runtime_storage_prefix",
}
RUNTIME_HASHES = {"bundle_hash", "compiled_manifest_hash"}


class PinnedProfile(CoreReferenceProfile):
    def __init__(self, recipes, generation_witnesses=None):
        self.recipes = recipes
        self.generation_witnesses = (
            generation_witnesses if generation_witnesses is not None else {}
        )

    def validate(self, observation, bindings):
        deployments = observation.database["solution_deployments"]
        for deployment in deployments:
            recipe = self.recipes[deployment["solution_id"]]
            assert deployment["id"] == fixture_binding(bindings, "deployment"), (
                "Pinned deployment fixture owner differs"
            )
            assert deployment["solution_id"] == fixture_binding(bindings, "solution")
            assert deployment["organization_id"] == fixture_binding(bindings, "org")
            assert deployment["created_by"] == fixture_binding(bindings, "setup_user")
            manifest, resolution = validate_runtime_closure(
                deployment["compiled_manifest"],
                deployment["resolution_map"],
                [],
                expected_manifest_hash=deployment["compiled_manifest_hash"],
                expected_resolution_hash=deployment["resolution_map_hash"],
            )
            assert not observation.database["solution_deployment_dependencies"]
            assert (
                str(manifest.solution_id) == deployment["solution_id"]
                and str(manifest.deployment_id) == deployment["id"]
            )
            assert (
                deployment["git_commit_sha"] == manifest.git.commit_sha == SOURCE_COMMIT
            )
            assert set(resolution.sources) == {SOURCE_PATH} and set(
                resolution.workflows
            ) == {PORTABLE_REF}
            assert resolution.sources[SOURCE_PATH].content_hash == SOURCE_HASH
            prefix = deployment_runtime_prefix(
                deployment["solution_id"], deployment["id"]
            )
            artifact = deployment_source_artifact_key(
                deployment["solution_id"], deployment["id"]
            )
            assert (
                deployment["runtime_storage_prefix"]
                == manifest.source.runtime_prefix
                == prefix
                and deployment["source_artifact_key"]
                == manifest.source.artifact_key
                == artifact
                and resolution.sources[SOURCE_PATH].object_key == prefix + SOURCE_PATH
            ), "Pinned immutable storage owner differs"
            assert not any(
                getattr(manifest, field)
                for field in (
                    "agents",
                    "forms",
                    "events",
                    "applications",
                    "tables",
                    "shared_tables",
                    "resources",
                    "root_file_bindings",
                    "file_locations",
                    "connections",
                    "config_requirements",
                    "dependencies",
                )
            ), "Pinned A acquired an unreviewed closure plane"
            assert deployment["bundle_hash"] == digest(
                {
                    "schema_version": "bifrost.initial-reviewed-workflow-install/v1",
                    "reviewed_recipe": recipe,
                    "source_commit_sha": SOURCE_COMMIT,
                    "source_hashes": {SOURCE_PATH: SOURCE_HASH},
                }
            )
            entity = resolution.workflows[PORTABLE_REF]
            reviewed = compile_workflow_registrations(
                ReviewedWorkflowRecipe.model_validate(recipe),
                {SOURCE_PATH: SOURCE.read_bytes()},
                WorkflowParameterCompiler(),
            )
            assert digest(entity.model_dump(mode="json", exclude_none=True)) == digest(
                reviewed[PORTABLE_REF].model_dump(mode="json", exclude_none=True)
            ), "Pinned immutable workflow differs from reviewed source recipe"
            assert str(entity.resolved_id) == recipe["workflows"][0]["id"]
            assert (
                entity.definition["organization_id"]
                == recipe["workflows"][0]["organization_id"]
            )
            assert (
                list(entity.definition["role_ids"])
                == recipe["workflows"][0]["controls"]["role_ids"]
            )
            evidence_id = digest(
                {
                    "schema_version": "bifrost.initial-reviewed-workflow-install/v1",
                    "solution_id": deployment["solution_id"],
                    "deployment_id": deployment["id"],
                    "organization_id": deployment["organization_id"],
                    "pointer": None,
                    "runtime_mode": "repo-v1",
                    "manifest_hash": deployment["compiled_manifest_hash"],
                    "resolution_hash": deployment["resolution_map_hash"],
                    "reviewed_recipe": recipe,
                    "workflow_ids": [str(entity.resolved_id)],
                    "source_hashes": {SOURCE_PATH: SOURCE_HASH},
                }
            )
            if isinstance(observation.body, dict) and observation.body.get("state") in {
                "ready",
                "active",
            }:
                assert observation.body["evidence_id"] == evidence_id, (
                    "Pinned HTTP evidence hash differs"
                )
            marker = deployment["validation_result"]
            assert deployment["state"] != "active" or marker is not None, (
                "Pinned active deployment validation receipt absent"
            )
            if marker is not None:
                assert marker == {
                    "schema_version": "bifrost.initial-reviewed-workflow-install/v1",
                    "preflight_evidence_id": evidence_id,
                    "workflow_ids": [str(entity.resolved_id)],
                    "source_hashes": {SOURCE_PATH: SOURCE_HASH},
                }, "Pinned validation receipt differs"

    def owned_deployment(self, observation, owner, bindings):
        deployments = [
            row
            for row in observation.database["solution_deployments"]
            if row["id"] == owner["solution_deployment_id"]
        ]
        assert len(deployments) == 1, "Pinned execution has no unique deployment owner"
        deployment = deployments[0]
        solutions = [
            row
            for row in observation.database["solutions"]
            if row["id"] == deployment["solution_id"]
        ]
        assert len(solutions) == 1, "Pinned deployment has no unique Solution owner"
        solution = solutions[0]
        assert (
            deployment["id"] == fixture_binding(bindings, "deployment")
            and solution["id"] == fixture_binding(bindings, "solution")
            and owner["workflow_id"] == fixture_binding(bindings, "workflow")
            and deployment["organization_id"]
            == solution["organization_id"]
            == owner["organization_id"]
            == fixture_binding(bindings, "org")
            and solution["status"] == "active"
            and solution["active_deployment_id"] == deployment["id"]
            and solution["execution_runtime_mode"] == "deployment-v1"
            and solution["allow_outbound_access"] is False
            and solution["git_connected"] is False
            and deployment["state"] == "active"
        ), "Pinned execution installation owner differs"
        return deployment, solution

    def validate_runtime(self, observation, owner, dispatch, pending, bindings):
        assert owner["runtime_mode"] == "deployment-v1", (
            "Pending profile requires R1 deployment runtime"
        )
        deployment, solution = self.owned_deployment(observation, owner, bindings)
        runtime = _pin_from_deployment(
            UUID(owner["workflow_id"]),
            SimpleNamespace(**{**solution, "id": UUID(solution["id"])}),
            SimpleNamespace(
                **{**deployment, "id": UUID(deployment["id"]), "dependencies": []}
            ),
            allow_superseded=False,
        )
        expected = runtime.queue_evidence()
        assert (
            len(expected) == 22
            and {"workflow_runtime_bounds", "workflow_parameters_schema"}
            <= expected.keys()
        ), "Pinned A immutable producer contract differs"
        evidence = owner["runtime_evidence"]
        assert type(evidence) is dict and evidence.keys() == expected.keys(), (
            "Pinned runtime evidence schema differs"
        )
        assert digest(evidence) == digest(expected), (
            "Pinned runtime evidence differs from immutable producer"
        )
        assert owner["runtime_evidence_hash"] == digest(evidence), (
            "Pinned runtime evidence hash differs"
        )
        assert (
            dispatch["request"]["caller_solution_deployment_id"] is None
            and dispatch["publish"]["file_path"] is None
        ), "Pinned A incoming dispatch contract differs"

    def expected_source_path(self, observation, owner, bindings):
        return SOURCE_PATH

    def validate_successful_generation(self, observation, owner, context, bindings):
        witness = self.generation_witnesses.get(owner["id"])
        assert type(witness) is dict and witness.keys() == {
            "source",
            "execution_id",
            "before",
            "after",
        }, "Pinned successful generation witness absent"
        assert (
            witness["source"] == WORKSPACE_GENERATION_KEY
            and witness["execution_id"] == owner["id"]
        ), "Pinned generation witness provenance differs"
        reads = [witness["before"], witness["after"]]
        for read in reads:
            assert type(read) is dict and read.keys() == {
                "value",
                "started_at",
                "completed_at",
            }, "Pinned generation read absent"
            assert (
                type(read["value"]) is str
                and read["value"]
                and not read["value"].startswith(WORKSPACE_UPDATING_PREFIX)
            ), "Pinned generation read invalid"
            start, end = instant(read["started_at"]), instant(read["completed_at"])
            assert (
                start.utcoffset() == end.utcoffset() == timedelta(0)
                and start <= end <= observation.after
            ), "Pinned generation read clock differs"
        assert instant(reads[0]["completed_at"]) <= instant(reads[1]["started_at"]), (
            "Pinned generation witness order differs"
        )
        assert (
            reads[0]["value"]
            == reads[1]["value"]
            == fixture_binding(bindings, "observed-workspace-generation")
        ), "Pinned generation changed during admission"
        assert (
            type(context.get("workspace_generation")) is str
            and context["workspace_generation"] == reads[0]["value"]
        ), "Pinned successful source generation differs"

    def validate_pending_context(self, observation, bindings):
        self.validate(observation, bindings)
        for owner in observation.database["executions"]:
            if owner["status"] == "Success":
                assert (
                    len(
                        [
                            delivery
                            for delivery in observation.database["work_deliveries"]
                            if delivery["message_id"] == owner["id"]
                        ]
                    )
                    == 1
                ), "Pinned successful pending delivery owner absent"
        validate_pending_context(observation, bindings, policy=self)

    def canonicalize(self, trace, bindings):
        for observation in trace:
            self.validate(observation, bindings)
        ordinary = super().canonicalize(trace, bindings)
        clocks = sorted(
            {
                instant(row[field])
                for observation in trace
                for table, fields in PIN_CLOCKS.items()
                for row in observation.database[table]
                for field in fields
                if row.get(field) is not None
            }
        )
        clock_names = {
            clock: f"pinned-clock:{index}" for index, clock in enumerate(clocks)
        }
        seen_clocks = set()
        for observation in trace:
            for table, fields in PIN_CLOCKS.items():
                for row in observation.database[table]:
                    for field in fields:
                        if row.get(field) is not None:
                            clock = instant(row[field])
                            if clock not in seen_clocks:
                                assert (
                                    observation.before <= clock <= observation.after
                                ), "Pinned server clock outside observation window"
                            seen_clocks.add(clock)

        def walk(value, path):
            if isinstance(value, dict):
                return {key: walk(item, (*path, key)) for key, item in value.items()}
            if isinstance(value, list):
                return [walk(item, path) for item in value]
            table = path[1] if len(path) >= 3 and path[0] == "database" else None
            leaf = next(reversed(path), None)
            relative = path[2:]
            identity = (
                table in PIN_IDENTITIES
                and len(path) == 3
                and leaf in PIN_IDENTITIES[table]
            )
            clock = table in PIN_CLOCKS and len(path) == 3 and leaf in PIN_CLOCKS[table]
            hash_field = table == "solution_deployments" and relative in {
                ("bundle_hash",),
                ("compiled_manifest_hash",),
                ("resolution_map_hash",),
                ("validation_result", "preflight_evidence_id"),
            }
            identity |= table == "solution_deployments" and relative == (
                "validation_result",
                "workflow_ids",
            )
            if table == "solution_deployments" and relative[:1] in {
                ("compiled_manifest",),
                ("resolution_map",),
            }:
                identity |= relative in {
                    ("compiled_manifest", "solution_id"),
                    ("compiled_manifest", "deployment_id"),
                    ("compiled_manifest", "source", "artifact_key"),
                    ("compiled_manifest", "source", "runtime_prefix"),
                    ("resolution_map", "sources", SOURCE_PATH, "object_key"),
                }
                identity |= relative[1:] in {
                    ("workflows", PORTABLE_REF, "resolved_id"),
                    ("workflows", PORTABLE_REF, "definition", "organization_id"),
                    ("workflows", PORTABLE_REF, "definition", "role_ids"),
                }
                hash_field |= relative in {
                    ("compiled_manifest", "bundle_hash"),
                    ("compiled_manifest", "resolution_map_hash"),
                }
            runtime = path[:-1] in {
                ("database", "executions", "runtime_evidence"),
                (
                    "database",
                    "executions",
                    "dispatch_evidence",
                    "publish",
                    "runtime_evidence",
                ),
                (*PENDING_PATH, "runtime_evidence"),
            }
            if runtime:
                identity |= leaf in RUNTIME_IDENTITIES
                hash_field |= leaf in RUNTIME_HASHES
            identity |= path in {
                (*PENDING_PATH, "solution_deployment_id"),
                (
                    "database",
                    "executions",
                    "dispatch_evidence",
                    "publish",
                    "solution_deployment_id",
                ),
                (
                    "database",
                    "work_deliveries",
                    "envelope",
                    "body",
                    "solution_deployment_id",
                ),
                ("body", "id"),
                ("body", "organization_id"),
                ("body", "solution_id"),
                ("body", "deployment_id"),
                ("body", "workflow_ids"),
                ("body", "active_deployment_id"),
            }
            hash_field |= path in {
                ("database", "executions", "runtime_evidence_hash"),
                ("database", "workflow_execution_attempts", "runtime_evidence_hash"),
                ("body", "evidence_id"),
            }
            clock |= path in {("body", "created_at"), ("body", "updated_at")}
            if clock and value is not None:
                return clock_names[instant(value)]
            if identity and value is not None:
                assert str(value) in bindings, f"Unowned pinned identity at {path}"
                return {
                    "identity": bindings[str(value)],
                    "wire_type": type(value).__name__,
                }
            if hash_field and value is not None:
                return "verified:identity-derived-sha256"
            return value

        return [walk(observation, ()) for observation in ordinary]

    def transport(self, captured, bindings):
        self.validate_pending_context(captured.observation, bindings)
        sdk_solutions = []
        for request in captured.transport.sdk_requests:
            claims = request["authorization"]["claims"]
            owner, pending, _ = validate_transport_owner(
                captured.observation, claims, bindings
            )
            assert pending is not None, "Pinned SDK pending owner absent"
            _, solution = self.owned_deployment(captured.observation, owner, bindings)
            assert (
                claims["engine_solution_id"] == solution["id"]
                and claims["engine_global_repo_access"] is False
            ), "Pinned SDK Solution authority differs"
            if request["path"] == "/api/sdk/integrations/get":
                assert request["request"].get("solution") == solution["id"], (
                    "Pinned SDK request Solution differs"
                )
            elif request["path"] == "/api/sdk/integrations/get_mapping":
                assert "solution" not in request["request"], (
                    "Pinned mapping acquired Solution selector"
                )
            sdk_solutions.append(solution["id"])
        result = super().transport(captured, bindings)
        if (
            not result["sdk_requests"]
            and not asdict(captured.transport)["source_requests"]
        ):
            return result
        for request, solution in zip(
            result["sdk_requests"], sdk_solutions, strict=True
        ):
            request["authorization"]["claims"]["engine_solution_id"] = (
                f"identity:{bindings[solution]}"
            )
            if request["path"] == "/api/sdk/integrations/get":
                assert request["request"]["solution"] == solution
                request["request"]["solution"] = f"identity:{bindings[solution]}"
        result["source_requests"] = asdict(captured.transport)["source_requests"]
        for request in result["source_requests"]:
            claims = request["authorization"]["claims"]
            execution = claims["engine_execution_id"]
            owner, pending, attempt = validate_transport_owner(
                captured.observation, claims, bindings
            )
            assert pending is not None, "Pinned source pending owner absent"
            deployment, solution_row = self.owned_deployment(
                captured.observation, owner, bindings
            )
            solution = solution_row["id"]
            assert (
                claims["engine_solution_id"] == solution
                and claims["engine_global_repo_access"] is False
            ), "Pinned source Solution authority differs"
            assert (
                captured.observation.before
                <= instant(request["before"])
                <= instant(request["after"])
                <= captured.observation.after
            )
            request.pop("before")
            request.pop("after")
            registry = {
                "source": {"path": deployment["runtime_storage_prefix"] + SOURCE_PATH},
                "solution_id": solution,
                "deployment_id": deployment["id"],
            }
            assert_source_response(request["status"], request["response"], registry)
            assert (
                request["method"] == "GET"
                and request["path"] == "/api/sdk/modules/" + registry["source"]["path"]
                and request["query"] == []
                and request["request"] is None
            )
            claims["engine_attempt_token_digest"] = (
                f"execution:{bindings[execution]}:attempt:{attempt['attempt_number']}"
            )
            for field in (
                "engine_execution_id",
                "engine_solution_id",
                "org_id",
                "delegated_user_id",
                "delegated_email",
            ):
                assert claims[field] in bindings
                claims[field] = f"identity:{bindings[claims[field]]}"
            request["path"] = "/api/sdk/modules/identity:source-storage-path"
            request["response"]["path"] = {
                "identity": "source-storage-path",
                "wire_type": "str",
            }
            if "storage_path" in request["response"]:
                request["response"]["storage_path"] = {
                    "identity": "source-storage-path",
                    "wire_type": "str",
                }
        return result


def assert_pinned_parity(left, right, left_env, right_env):
    profile = PinnedProfile(
        {str(env.ids["solution"]): env.recipe for env in (left_env, right_env)},
        {
            key: value
            for env in (left_env, right_env)
            for key, value in env.generation_witnesses.items()
        },
    )
    assert_parity(
        [step.observation for step in left],
        [step.observation for step in right],
        left_env.bindings,
        right_env.bindings,
        profile=profile,
    )
    for expected, actual in zip(left, right, strict=True):
        assert profile.transport(expected, left_env.bindings) == profile.transport(
            actual, right_env.bindings
        ), f"{expected.observation.step}: pinned transport differs"
