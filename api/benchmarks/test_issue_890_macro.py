"""Bounded execution-response serialization shapes for CodSpeed walltime/memory."""

import json

from src.models.contracts.executions import ExecutionsListResponse, WorkflowExecutionResponse


def _round_trip(model, payload: dict) -> str:
    return model.model_validate(payload).model_dump_json()


def test_bench_execution_list_page(benchmark) -> None:
    payload = {
        "executions": [
            {
                "execution_id": f"00000000-0000-0000-0000-{index:012d}",
                "workflow_name": "Synthetic workflow",
                "executed_by": "00000000-0000-0000-0000-000000000000",
                "executed_by_name": "Synthetic operator",
                "status": "Success",
                "duration_ms": (index % 40) * 1000,
            }
            for index in range(1000)
        ],
        "continuation_token": None,
    }

    encoded = benchmark(_round_trip, ExecutionsListResponse, payload)
    assert len(json.loads(encoded)["executions"]) == 1000


def test_bench_nested_execution_result(benchmark) -> None:
    payload = {
        "execution_id": "00000000-0000-0000-0000-000000000001",
        "status": "Success",
        "result": {
            "records": [
                {"index": index, "payload": "x" * 1024, "ok": True}
                for index in range(1400)
            ]
        },
        "duration_ms": 3000,
    }

    encoded = benchmark(_round_trip, WorkflowExecutionResponse, payload)
    assert len(json.loads(encoded)["result"]["records"]) == 1400
