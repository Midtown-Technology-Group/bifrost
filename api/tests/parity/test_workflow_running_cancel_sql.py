"""39 scenarios / 89 callers plus one zero-actor actual expiry control."""

import pytest

from tests.parity.workflow_running_cancel_sql_harness import (
    CASES,
    run_case,
    run_expiry_control,
)

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
async def test_actual_running_cancel_sql_differential(case, async_engine):
    await run_case(case, async_engine)


async def test_actual_case_work_expiry_disposes_owned_cohorts(async_engine):
    await run_expiry_control(async_engine)
