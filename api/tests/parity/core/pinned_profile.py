"""Explicit deployment-v1 policy; source and hash relationships stay observable."""

from __future__ import annotations

import hashlib
from dataclasses import asdict

from src.services.solutions.deployment_manifest import validate_runtime_closure

from tests.parity.compare import assert_parity
from tests.parity.core.pinned import (
    PORTABLE_REF,
    SOURCE_COMMIT,
    SOURCE_HASH,
    SOURCE_PATH,
    assert_source_response,
)
from tests.parity.core.profile import CoreReferenceProfile, digest, instant

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
    def __init__(self, recipes):
        self.recipes = recipes

    def validate(self, observation):
        deployments = observation.database["solution_deployments"]
        for deployment in deployments:
            recipe = self.recipes[deployment["solution_id"]]
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
            assert deployment["bundle_hash"] == digest(
                {
                    "schema_version": "bifrost.initial-reviewed-workflow-install/v1",
                    "reviewed_recipe": recipe,
                    "source_commit_sha": SOURCE_COMMIT,
                    "source_hashes": {SOURCE_PATH: SOURCE_HASH},
                }
            )
            entity = resolution.workflows[PORTABLE_REF]
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
            if marker is not None:
                assert marker == {
                    "schema_version": "bifrost.initial-reviewed-workflow-install/v1",
                    "preflight_evidence_id": evidence_id,
                    "workflow_ids": [str(entity.resolved_id)],
                    "source_hashes": {SOURCE_PATH: SOURCE_HASH},
                }, "Pinned validation receipt differs"
            for execution in observation.database["executions"]:
                evidence = execution["runtime_evidence"]
                assert (
                    execution["runtime_mode"] == "deployment-v1"
                    and execution["solution_deployment_id"] == deployment["id"]
                )
                assert (
                    evidence["solution_id"] == deployment["solution_id"]
                    and evidence["solution_deployment_id"] == deployment["id"]
                )
                assert (
                    evidence["bundle_hash"] == deployment["bundle_hash"]
                    and evidence["compiled_manifest_hash"]
                    == deployment["compiled_manifest_hash"]
                )
                assert (
                    evidence["git_commit_sha"] == SOURCE_COMMIT
                    and evidence["workflow_source_hash"] == SOURCE_HASH
                )
                assert evidence["deployment_source_hashes"] == {
                    SOURCE_PATH: SOURCE_HASH
                }
                assert (
                    evidence["runtime_storage_prefix"]
                    == deployment["runtime_storage_prefix"]
                )
                assert evidence["solution_global_repo_access"] is False
                assert (
                    execution["dispatch_evidence"]["publish"]["runtime_evidence"]
                    == evidence
                )
                for delivery in observation.database["work_deliveries"]:
                    if delivery["message_id"] == execution["id"]:
                        assert (
                            delivery["envelope"]["body"]["runtime_evidence"] == evidence
                        )
                        assert (
                            delivery["envelope"]["body"]["solution_deployment_id"]
                            == deployment["id"]
                        )

    def canonicalize(self, trace, bindings):
        for observation in trace:
            self.validate(observation)
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
        for observation in trace:
            for table, fields in PIN_CLOCKS.items():
                for row in observation.database[table]:
                    for field in fields:
                        if row.get(field) is not None:
                            assert (
                                observation.before
                                <= instant(row[field])
                                <= observation.after
                            ), "Pinned server clock outside observation window"

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
            runtime = (
                path[:3] == ("database", "executions", "runtime_evidence")
                or path[:5]
                == (
                    "database",
                    "executions",
                    "dispatch_evidence",
                    "publish",
                    "runtime_evidence",
                )
                or path[:5]
                == (
                    "database",
                    "work_deliveries",
                    "envelope",
                    "body",
                    "runtime_evidence",
                )
            )
            if runtime:
                identity |= leaf in RUNTIME_IDENTITIES and len(path) in {4, 6}
                hash_field |= leaf in RUNTIME_HASHES and len(path) in {4, 6}
            identity |= path in {
                ("database", "executions", "execution_context", "solution_id"),
                (
                    "database",
                    "executions",
                    "execution_context",
                    "solution_deployment_id",
                ),
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
        result = super().transport(captured, bindings)
        if (
            not result["sdk_requests"]
            and not asdict(captured.transport)["source_requests"]
        ):
            return result
        solution = captured.observation.database["solution_deployments"][0][
            "solution_id"
        ]
        for request in result["sdk_requests"]:
            assert request["authorization"]["claims"]["engine_solution_id"] == solution
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
            assert (
                captured.observation.before
                <= instant(request["before"])
                <= instant(request["after"])
                <= captured.observation.after
            )
            request.pop("before")
            request.pop("after")
            deployment = captured.observation.database["solution_deployments"][0]
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
            attempts = [
                row
                for row in captured.observation.database["workflow_execution_attempts"]
                if row["execution_id"] == execution
                and row["claim_token"] is not None
                and hashlib.sha256(row["claim_token"].encode()).hexdigest()
                == claims["engine_attempt_token_digest"]
            ]
            assert len(attempts) == 1 and claims["engine_attempt_token_present"] is True
            assert claims["engine_solution_id"] == solution
            claims["engine_attempt_token_digest"] = (
                f"execution:{bindings[execution]}:attempt:{attempts[0]['attempt_number']}"
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
        {str(env.ids["solution"]): env.recipe for env in (left_env, right_env)}
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
