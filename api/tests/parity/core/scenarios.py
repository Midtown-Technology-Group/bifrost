"""Pinned authored Scenario A; nominal execution success is insufficient."""

from __future__ import annotations

import json

from tests.parity.core.adapter import ReferenceAdapter
from tests.parity.core.capture import CapturedStep, now
from tests.parity.core.environment import SYNTHETIC_VALUE, ReferenceEnvironment


async def execute_readiness(
    adapter: ReferenceAdapter,
    environment: ReferenceEnvironment,
    *,
    parameters=None,
    role="readiness",
    before=None,
) -> CapturedStep:
    execution_parameters = parameters or environment.parameters()
    execution = await environment.allocate_execution(
        role, request_name=execution_parameters["integration_name"]
    )
    step = await adapter.request(
        role,
        "POST",
        "/api/workflows/execute",
        json={
            "workflow_id": str(environment.ids["workflow"]),
            "input_data": execution_parameters,
            "sync": True,
        },
        headers={"X-Bifrost-Execution-ID": str(execution)},
        settled=True,
        before=before or now(),
    )
    assert step.observation.status == 200, "Real public workflow admission failed"
    assert step.observation.body["execution_id"] == str(execution)
    return step


def expected_result(
    environment: ReferenceEnvironment,
    *,
    required=None,
    configured=True,
    found=True,
    mapping=True,
    lookup_error=None,
    name=None,
) -> dict:
    required = required if required is not None else ["fixture_value"]
    keys = ["fixture_value"] if configured and found else []
    missing = sorted(set(required) - set(keys))
    blockers = []
    if lookup_error:
        blockers.append("integration_lookup_failed")
    elif not found:
        blockers.append("integration_not_found")
    if found and not keys:
        blockers.append("no_usable_configuration")
    if missing:
        blockers.append("required_config_keys_missing")
    if not mapping:
        blockers.append("mapping_not_found")
    return {
        "integration_name": name or environment.name,
        "scope_mode": "organization",
        "found": found,
        "lookup_error_type": lookup_error,
        "configured_keys": keys,
        "required_config_keys": required,
        "missing_required_config_keys": missing,
        "oauth_fields_present": {
            field: False
            for field in ("access_token", "refresh_token", "client_id", "client_secret")
        },
        "oauth_ready": False,
        "credential_material_present": bool(keys),
        "mapping_checked": True,
        "mapping_present": mapping,
        "mapping_lookup_error_type": None,
        "ready": not blockers,
        "blockers": blockers,
        "guarantees": {
            "read_only": True,
            "vendor_connectivity_tested": False,
            "credential_values_returned": False,
            "raw_scope_identifier_returned": False,
        },
    }


def assert_readiness(
    step: CapturedStep, environment: ReferenceEnvironment, expected: dict
) -> None:
    observation = step.observation
    assert observation.body["status"] == "Success"
    assert observation.body["result"] == expected
    serialized = json.dumps(observation.body["result"])
    assert SYNTHETIC_VALUE not in serialized
    assert (
        str(environment.ids["org"]) not in serialized
        and "synthetic-entity" not in serialized
    )
    execution = observation.database["executions"][0]
    principals = {row["id"]: row for row in observation.database["users"]}
    assert principals[str(environment.ids["user"])]["is_superuser"] is False
    assert principals[str(environment.ids["setup_user"])]["is_superuser"] is True
    tenant = next(
        row
        for row in observation.database["organizations"]
        if row["id"] == str(environment.ids["org"])
    )
    assert tenant["is_provider"] is False
    assert observation.database["user_roles"][0]["role_id"] == str(
        environment.ids["role"]
    )
    assert observation.database["workflow_roles"][0]["role_id"] == str(
        environment.ids["role"]
    )
    assert execution["result"] == expected and execution["status"] == "Success"
    assert execution["executed_by"] == str(environment.ids["user"])
    assert execution["organization_id"] == str(environment.ids["org"])
    assert execution["workflow_id"] == str(environment.ids["workflow"])
    assert execution["parameters"]["integration_name"] == expected["integration_name"]
    assert execution["parameters"]["organization_id"] == str(environment.ids["org"])
    context = execution["execution_context"]
    assert context["user_id"] == str(environment.ids["user"]) and context[
        "scope"
    ] == str(environment.ids["org"])
    if "workspace_generation" in context:
        assert context["workspace_generation"] == environment.generation
    attempts = observation.database["workflow_execution_attempts"]
    assert (
        len(attempts) == 1
        and attempts[0]["status"] == "succeeded"
        and attempts[0]["phase"] == "terminal"
    )
    deliveries = observation.database["work_deliveries"]
    assert len(deliveries) == 1 and deliveries[0]["status"] == "completed"
    assert (
        deliveries[0]["queue_name"] == "workflow-executions"
        and deliveries[0]["claim_count"] == 1
    )
    assert deliveries[0]["lease_owner"] is None and deliveries[0]["lease_token"] is None
    assert (
        not observation.database["agent_runs"]
        and not observation.database["agent_run_steps"]
    )
    assert (
        not observation.database["ai_usage"]
        and not step.transport.model_requests
        and not step.transport.vendor_requests
    )
    requests = step.transport.sdk_requests
    assert [request["path"] for request in requests] == [
        "/api/sdk/integrations/get",
        "/api/sdk/integrations/get_mapping",
    ]
    for request in requests:
        assert (
            request["method"] == "POST"
            and request["request"]["name"] == expected["integration_name"]
        )
        assert request["request"]["scope"] == str(environment.ids["org"])
        assert (
            request["authorization"]["scheme"] == "Bearer"
            and request["authorization"]["signature_valid"] is True
        )
        assert request["authorization"]["owner_verified"] is True
        claims = request["authorization"]["claims"]
        assert claims["delegated_user_id"] == str(environment.ids["user"])
        assert claims["org_id"] == str(environment.ids["org"])
        assert (
            claims["delegated_is_superuser"] is False
            and claims["delegated_is_provider_org"] is False
        )
        assert claims["delegated_is_external"] is False
    terminal = [
        event
        for event in observation.events
        if isinstance(event["payload"], dict)
        and event["payload"].get("type") == "execution_update"
        and event["payload"].get("status") == "Success"
    ]
    assert (
        len(terminal) == 1
        and terminal[0]["channel"] == f"bifrost:execution:{execution['id']}"
    )
