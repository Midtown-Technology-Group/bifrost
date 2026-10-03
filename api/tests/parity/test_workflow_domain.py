"""Actual domain differential evidence; events/wire/SQL ownership remain open."""

import asyncio
from copy import deepcopy
import pytest
import pytest_asyncio

from tests.parity.workflow_domain_harness import (
    WorkflowCohort,
    check,
    compare,
    dormant,
    load_cases,
    read_bounded,
    real_consumer,
    verify_receipt,
)

pytestmark = pytest.mark.e2e
CASES = load_cases()


@pytest_asyncio.fixture
async def cohort_factory(async_engine):
    """Own committed cohorts without changing any global test fixture."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def create(case):
        verify_receipt()
        cohort = WorkflowCohort(async_engine, case)
        try:
            await cohort.seed()
            yield cohort
        finally:
            try:
                cohort.retain()
            finally:
                await cohort.close()

    return create


@pytest.mark.parametrize("case", CASES, ids=[case["case_id"] for case in CASES])
async def test_real_workflow_domain_differential(case, cohort_factory):
    async with cohort_factory(case) as cohort:
        observations = await cohort.run()
        check(
            [item.case_id for item in observations]
            == [step["case_id"] for step in case["steps"]],
            "declared operation omitted",
        )
        for item in observations:
            outcome = item.response["outcome"]
            if outcome["kind"] == "rejected" and outcome["reason"] not in {
                "RequiresCoordinatorPolicy",
                "LegacyUnfencedOutsideTrackedPath",
            }:
                continue  # compare() already proves no projection or fan-out.
            if item.request["operation"]["kind"] == "running":
                check(not item.events, "running helper unexpectedly published")
                continue
            # Real Python publications are retained reference observations.
            # The Rust plan has no emitter, so this is never event-plane parity.
            kinds = {event["payload"].get("type") for event in item.events}
            check(
                "execution_update" in kinds, "reference execution publication missing"
            )
            if item.request["operation"]["kind"] == "result":
                check(
                    "history_update" in kinds, "reference history publication missing"
                )


@pytest.mark.parametrize(
    "plane", ["status", "nullable_input", "projection", "clock", "unselected_row"]
)
async def test_real_rust_plan_comparator_detects_drift(plane, cohort_factory):
    case = next(case for case in CASES if case["case_id"] == "success-Success")
    async with cohort_factory(case) as cohort:
        observed = (await cohort.run())[0]
        drift = deepcopy(observed)
        plan = drift.response["outcome"]["plan"]
        if plane == "status":
            plan["execution"]["status"] = "Failed"
        elif plane == "nullable_input":
            plan["attempt"]["duration_ms"] = "Keep"
        elif plane == "projection":
            plan["execution"]["result"] = "Keep"
        elif plane == "clock":
            plan["attempt"]["completed_at"] = "Keep"
        else:
            foreign = next(
                row
                for row in drift.after["attempts"]
                if row["execution_id"] != cohort.ids["execution"]
            )
            foreign["process_id"] = "synthetic-detector-drift"
        # This mutation is a detector control on genuine retained observations,
        # never a replacement reference operation or candidate backend result.
        with pytest.raises(AssertionError, match="field differs"):
            compare(drift)


async def test_null_clear_and_logical_keep_drift_are_distinct(cohort_factory):
    case = next(case for case in CASES if case["case_id"] == "duration-null")
    async with cohort_factory(case) as cohort:
        observed = (await cohort.run())[0]
        attempt_drift = deepcopy(observed)
        attempt_drift.response["outcome"]["plan"]["attempt"]["duration_ms"] = "Keep"
        with pytest.raises(AssertionError, match="field differs"):
            compare(attempt_drift)
        logical_drift = deepcopy(observed)
        logical_drift.response["outcome"]["plan"]["execution"]["duration_ms"] = (
            "SetSupplied"
        )
        with pytest.raises(AssertionError, match="field differs"):
            compare(logical_drift)


async def test_capture_bound_is_enforced_during_reads():
    """Pure capture-unit control, not Rust/DB differential authorization proof."""
    stream = asyncio.StreamReader(limit=4096)
    stream.feed_data(b"x" * 4097)
    stream.feed_eof()
    with pytest.raises(AssertionError, match="driver output exceeded limit"):
        await read_bounded(stream, 4096)
    exact = asyncio.StreamReader(limit=4096)
    exact.feed_data(b"x" * 4096)
    exact.feed_eof()
    check(len(await read_bounded(exact, 4096)) == 4096, "exact capture boundary failed")


async def test_real_constructor_restores_owned_callback_after_failure():
    """Genuine dormant pool/resource control, not execution ownership proof."""
    from src.services.execution import process_pool

    previous_pool = process_pool._pool
    pool = process_pool.get_process_pool()
    callback = pool.on_result

    async def owned_callback(_result):
        raise AssertionError("dormant fixture callback must not run")

    try:
        dormant(pool)
        pool.on_result = owned_callback

        async def fail_inside_owned_consumer():
            async with real_consumer() as consumer:
                check(consumer._pool is pool, "constructor selected a different pool")
                raise RuntimeError("synthetic-constructor-control")

        with pytest.raises(RuntimeError, match="synthetic-constructor-control"):
            await fail_inside_owned_consumer()
        check(process_pool._pool is pool, "pre-existing dormant pool was replaced")
        check(
            pool.on_result is owned_callback, "exact previous callback was not restored"
        )
        dormant(pool)
    finally:
        pool.on_result = callback
        if previous_pool is None:
            check(process_pool._pool is pool, "owned control pool identity changed")
            dormant(pool)
            process_pool._pool = None
        # No stop, start or fork of either an owned or borrowed pool.
