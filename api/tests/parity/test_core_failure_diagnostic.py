"""Unit coverage for the public-CI failure summary's disclosure boundary."""

from __future__ import annotations

import json
from typing import cast

import pytest

from tests.parity.core.capture import CapturedStep, TransportEvidence, now
from tests.parity.core.environment import ReferenceEnvironment
from tests.parity.core.scenarios import assert_readiness, readiness_failure_summary
from tests.parity.harness import Observation


def captured_failure() -> CapturedStep:
    timestamp = now()
    return CapturedStep(
        Observation(
            "readiness", 200,
            {"status": "Failed", "error_type": "WorkflowLoadError"},
            {
                "executions": [{"status": "Failed", "runtime_mode": "deployment-v1"}],
                "workflow_execution_attempts": [{
                    "status": "failed", "phase": "terminal",
                    "failure_phase": "execution", "failure_code": "tenant_code_error",
                }],
                "execution_attempts": [{
                    "status": "failed", "failure_code": "WorkflowLoadError",
                }],
                "work_deliveries": [{"status": "completed"}],
            },
            [{"payload": {
                "type": "execution_update", "errorType": "WorkflowLoadError",
            }}],
            timestamp, timestamp,
        ),
        TransportEvidence(sdk_requests=[{}]),
    )


def summary_payload(step: CapturedStep):
    return json.loads(readiness_failure_summary(step).removeprefix("Core readiness failure: "))


def test_failure_summary_preserves_decisive_class_and_committed_phase():
    step = captured_failure()
    summary = summary_payload(step)
    assert summary["response_error_class"] == "WorkflowLoadError"
    assert summary["event_error_classes"] == ["WorkflowLoadError"]
    assert summary["committed"]["workflow_execution_attempts"] == [{
        "status": "failed", "phase": "terminal",
        "failure_phase": "execution", "failure_code": "tenant_code_error",
    }]
    assert summary["transport_counts"] == {"source": 0, "sdk": 1, "model": 0, "vendor": 0}
    # The durable-result response fallback omits the class; terminal publication
    # still supplies it without exposing the event's error message.
    step.observation.body["error_type"] = None
    summary = summary_payload(step)
    assert summary["response_error_class"] is None
    assert summary["event_error_classes"] == ["WorkflowLoadError"]


def test_failure_summary_never_serializes_unapproved_values_or_evidence():
    step = captured_failure()
    private = "unapproved-private-material"
    step.observation.body.update(error=private, result=private, execution_id=private)
    step.observation.body["error_type"] = "PrivateMaterialClass"
    step.observation.body["status"] = private
    for rows in step.observation.database.values():
        for row in rows:
            for key in row:
                row[key] = {"private": private}
            row.update(
                error_message=private, claim_token=private, lease_token=private,
                parameters=private, variables=private, id=private,
            )
    step.observation.database["execution_logs"] = [{"message": private}]
    step.observation.events[0].update(channel=private)
    step.observation.events[0]["payload"].update(
        error=private, errorType=private, execution_id=private,
    )
    step.transport.sdk_requests = [{"authorization": private, "response": private}]
    setattr(step.transport, "source_requests", [{"content": private}])
    step.transport.model_requests = [{"response": private}]
    step.transport.vendor_requests = [{"response": private}]
    setattr(step.transport, "observer_failures", [{
        "stage": private, "reason": private, "request_kind": private,
        "exception_class": "PrivateMaterialClass", "upstream_status": private,
        "body": private, "path": private, "claims": private,
    }])
    result = readiness_failure_summary(step)
    assert private not in result and "PrivateMaterialClass" not in result
    summary = summary_payload(step)
    assert summary["response_status"] == "unclassified"
    assert summary["response_error_class"] == "unclassified"
    assert summary["event_error_classes"] == ["unclassified"]
    assert summary["transport_counts"] == {"source": 1, "sdk": 1, "model": 1, "vendor": 1}
    assert summary["observer_failures"] == [{
        "stage": "unclassified", "reason": "unclassified", "request_kind": "unclassified",
        "exception_class": "unclassified", "upstream_status": None,
    }]
    assert summary["committed"]["workflow_execution_attempts"] == [{
        key: "unclassified" for key in ("status", "phase", "failure_phase", "failure_code")
    }]


def test_failed_readiness_still_fails_with_only_safe_summary():
    step = captured_failure()
    step.observation.body["error"] = "private-exception-text"
    with pytest.raises(AssertionError, match="Core readiness failure:") as caught:
        assert_readiness(step, cast(ReferenceEnvironment, None), {})
    assert "WorkflowLoadError" in str(caught.value)
    assert "private-exception-text" not in str(caught.value)
