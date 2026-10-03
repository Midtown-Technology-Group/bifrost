"""Real SQL characterization before claiming Rust persistence parity."""

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from src.repositories.executions import ExecutionRepository

from tests.parity.workflow_domain_harness import WorkflowCohort, load_cases
from tests.parity.workflow_sql_harness import load_fixture, source_session

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="session", autouse=True)
def result_source_session(setup_test_environment, request):
    """Install before the genuine non-autouse synchronous engine fixture."""
    observer = source_session(request.session)
    original = None
    try:
        yield observer
    except BaseException as error:
        original = error
        raise
    finally:
        if observer is not None:
            observer.close(original)


@pytest_asyncio.fixture
async def result_source_function(result_source_session, isolate_global_db_engine, request):
    """Owned listeners leave before the unchanged source post-close finalizer."""
    if result_source_session is None:
        yield None
        return
    owner = result_source_session.begin_function(request.node)
    original = None
    try:
        yield owner
    except BaseException as error:
        original = error
        raise
    finally:
        owner.finish(original)


async def test_queued_cancel_emitted_update_order(async_engine, record_property, result_source_function):
    """Record actual ORM flush order; assignment order is not SQL evidence."""
    if result_source_function is not None:
        result_source_function.admit_fixture_engine(async_engine)
    case = next(case for case in load_cases() if case["case_id"] == "cancel-Pending-claimed")
    cohort = WorkflowCohort(async_engine, case)
    cohort.sessions = async_sessionmaker(async_engine, autoflush=False, expire_on_commit=False)
    labels = []

    def observe(_connection, _cursor, _statement, _parameters, context, _many):
        # Inspect compiled statement metadata only. Never retain SQL or binds.
        compiled = context.compiled
        if compiled is None:
            return
        statement = compiled.statement
        if not getattr(statement, "is_update", False):
            return
        table = getattr(getattr(statement, "table", None), "name", None)
        if table in {"executions", "workflow_execution_attempts"}:
            labels.append(table)

    try:
        await cohort.seed()
        assert cohort.principal is not None
        event.listen(async_engine.sync_engine, "after_cursor_execute", observe)
        try:
            async with cohort.sessions() as db:
                _, error = await ExecutionRepository(db).cancel_execution(cohort.ids["execution"], cohort.principal)
                assert error is None
        finally:
            event.remove(async_engine.sync_engine, "after_cursor_execute", observe)
        # Freeze the actual order observed in supported run37099087578. A
        # reference-order change must stop SQL parity rather than silently pass.
        assert labels == ["executions", "workflow_execution_attempts"]
        record_property("queued_cancel_update_order", ",".join(labels))
        async with cohort.sessions() as db:
            execution = await db.get(Execution, cohort.ids["execution"])
            attempt = await db.scalar(
                select(WorkflowExecutionAttempt).where(WorkflowExecutionAttempt.execution_id == cohort.ids["execution"])
            )
            assert execution is not None and attempt is not None
            assert execution.status == ExecutionStatus.CANCELLED
            assert attempt.status == "cancelled"
            assert execution.completed_at is not None
            assert execution.completed_at == attempt.completed_at == attempt.heartbeat_at
    finally:
        await cohort.close()


@pytest.mark.parametrize(
    "result_case",
    load_fixture()["cases"],
    ids=lambda case: case["case_id"],
)
async def test_result_nonfault_paired(async_engine, request, record_property, result_case, result_source_function):
    from tests.parity.workflow_sql_harness import lifetime, paired_result

    async with lifetime(request, result_source_function, async_engine) as owned:
        observation = await paired_result(async_engine, result_case, owned)
        record_property("result_case_id", observation["case_id"])
        record_property("result_reference", observation["reference"])
        record_property("result_native_kind", observation["native_kind"])
        record_property("rust_event_adapter", "absent-held")


@pytest.mark.parametrize(
    "category,control_id",
    [
        (category, case_id)
        for category, ids in load_fixture()["controls"].items()
        if category != "Feature-preservation"
        for case_id in ids
    ],
    ids=lambda value: value,
)
async def test_result_nonfault_control(
    async_engine, request, record_property, category, control_id, result_source_function
):
    from tests.parity.workflow_sql_harness import (
        codec_control,
        decode_control,
        lifetime,
        load_fixture,
        schema_control,
        source_admission,
        source_control,
    )

    async with lifetime(request, result_source_function, async_engine) as owned:
        source_admission(result_source_function)
        base = next(case for case in load_fixture()["cases"] if case["case_id"] == "s-result-absent")
        if category == "Codec pre-admission negatives":
            await codec_control(async_engine, owned, control_id, base)
        elif category == "Actual schema INSERT":
            await schema_control(async_engine, owned, control_id, base)
        elif category == "Source-only/out-of-tracked":
            await source_control(async_engine, owned, control_id, base)
        else:
            assert category == "Decode-parity gate"
            await decode_control(owned, control_id)
        record_property("result_control_id", control_id)
        record_property("result_control_category", category)
