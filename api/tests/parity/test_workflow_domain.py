"""Actual domain differential evidence; events/wire/SQL ownership remain open."""

import asyncio
from copy import deepcopy

import pytest
import pytest_asyncio

from tests.parity.workflow_domain_harness import (
    ClaimCohort,
    ClaimFrontendBroker,
    WorkflowCohort,
    check,
    claim_control,
    compare,
    compare_claim,
    dormant,
    invoke_driver,
    load_cases,
    load_claim_cases,
    native_claim_request,
    read_bounded,
    real_consumer,
    validate_claim_response,
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
            [item.case_id for item in observations] == [step["case_id"] for step in case["steps"]],
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
            check("execution_update" in kinds, "reference execution publication missing")
            if item.request["operation"]["kind"] == "result":
                check("history_update" in kinds, "reference history publication missing")


@pytest.mark.parametrize("plane", ["status", "nullable_input", "projection", "clock", "unselected_row"])
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
            foreign = next(row for row in drift.after["attempts"] if row["execution_id"] != cohort.ids["execution"])
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
        logical_drift.response["outcome"]["plan"]["execution"]["duration_ms"] = "SetSupplied"
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
        check(pool.on_result is owned_callback, "exact previous callback was not restored")
        dormant(pool)
    finally:
        pool.on_result = callback
        if previous_pool is None:
            check(process_pool._pool is pool, "owned control pool identity changed")
            dormant(pool)
            process_pool._pool = None
        # No stop, start or fork of either an owned or borrowed pool.


CLAIM_CASES = load_claim_cases()


@pytest.fixture(scope="module")
def claim_frontend_module():
    """Synchronous broker outlives both original function DB finalizers."""
    broker = ClaimFrontendBroker()
    first: BaseException | None = None
    try:
        broker.install()
        yield broker
    except BaseException as error:
        first = error
        broker.poison(error)
    finally:
        try:
            broker.finish()
        except BaseException as error:
            if first is None:
                first = error
    if first is not None:
        raise first


@pytest_asyncio.fixture
async def claim_cohort_factory(async_engine, claim_frontend_module):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def create(scenario):
        verify_receipt()
        label = scenario["case_id"]
        broker = claim_frontend_module
        check(label not in broker.pending and label not in broker.finished, "claim duplicate genuine scenario")
        broker.pending.add(label)
        cohort = None
        first: BaseException | None = None
        try:
            cohort = ClaimCohort(async_engine, scenario, broker)
            await cohort.seed()
            yield cohort
        except BaseException as error:
            first = error
            broker.poison(error)
        finally:
            if cohort is not None:
                try:
                    await cohort.close()
                except BaseException as error:
                    broker.poison(error)
                    if first is None:
                        first = error
            if cohort is not None and cohort.closed:
                broker.pending.remove(label)
                if first is None:
                    broker.finished.add(label)
        if first is not None:
            raise first

    return create


@pytest.mark.parametrize("scenario", CLAIM_CASES, ids=[item["case_id"] for item in CLAIM_CASES])
async def test_original_claim_characterization(scenario, claim_cohort_factory, claim_frontend_module):
    if scenario["execution_path"] == "none_native_boundary_only":
        # Genuine fixture dependencies still resolve; these cases create no
        # ClaimCohort, SQL seed or original consumer and grant no authority.
        verify_receipt()
        response = await invoke_driver(native_claim_request(scenario))
        validate_claim_response(response["outcome"])
        if scenario["behavior_category"] == "structural_invalid_active_guard":
            check(
                response["outcome"] == {"kind": "rejected", "reason": "InvalidAttemptState"},
                "claim native structural guard differs",
            )
        else:
            check(response["outcome"]["kind"] == "accepted", "claim native odd-start guard differs")
        return
    factory = claim_cohort_factory
    async with factory(scenario) as cohort:
        observed = await cohort.run_claim()
        # Detector controls consume genuine measured response and rows. They
        # share the actual comparator, never substitute a reference operation.
        if scenario["case_id"] == "claim-logical-pending":
            original = deepcopy(observed["response"])
            with pytest.raises(AssertionError, match="claim native venue response presence"):
                compare_claim(cohort, observed["request"], None)
            for field, changed, diagnostic in [
                ("worker_incarnation_id", "Keep", "field differs"),
                ("heartbeat_at", "Keep", "field differs"),
                ("attempt_status", "running", "field differs"),
            ]:
                drift = deepcopy(original)
                drift["outcome"]["plan"][field] = changed
                with pytest.raises(AssertionError, match=diagnostic):
                    compare_claim(cohort, observed["request"], drift)
            saved = cohort.after
            try:
                cohort.after = deepcopy(saved)
                selected = next(item for item in cohort.after["attempts"] if item["id"] == cohort.ids["attempt"])
                selected["policy_digest"] = None
                with pytest.raises(AssertionError, match="field differs"):
                    compare_claim(cohort, observed["request"], original)
            finally:
                cohort.after = saved
            try:
                cohort.after = deepcopy(saved)
                foreign = next(
                    item for item in cohort.after["attempts"] if item["execution_id"] == cohort.ids["foreign"]
                )
                foreign["process_id"] = "synthetic-claim-drift"
                with pytest.raises(AssertionError, match="field differs"):
                    compare_claim(cohort, observed["request"], original)
            finally:
                cohort.after = saved
        if scenario["case_id"] == "claim-logical-success":
            drift = deepcopy(observed["response"])
            drift["outcome"] = {"kind": "deferred", "reason": "DeferLegacyInline"}
            with pytest.raises(AssertionError, match="claim legacy return differs"):
                compare_claim(cohort, observed["request"], drift)


async def test_claim_control_grammar_rejects_replay_unknown_and_boolean_sequences():
    original = {"schema": "bifrost.test.claim-frontend-control/v1", "phase": "before", "seq": 2}
    check(claim_control(original, "before", 2) is original, "claim control identity changed")
    for value, diagnostic in [
        ({**original, "seq": 4}, "claim control sequence"),
        ({**original, "seq": True}, "claim control sequence"),
        ({**original, "phase": "after"}, "claim control phase"),
        ({**original, "schema": "unknown"}, "claim control schema"),
        ({**original, "unknown": None}, "closed schema mismatch"),
    ]:
        with pytest.raises(AssertionError, match=diagnostic):
            claim_control(value, "before", 2)


async def test_claim_same_observer_forwarding_preserves_first_close_exception():
    from types import SimpleNamespace

    broker = ClaimFrontendBroker()
    before_engine, before_factory = object(), object()
    broker.database = SimpleNamespace(_engine=before_engine, _async_session_factory=before_factory)
    calls = []

    async def original(*args, **kwargs):
        calls.append((args, kwargs))
        broker.database._engine = None
        broker.database._async_session_factory = None

    broker.original_close = original
    await broker.close_db("synthetic-argument", selected=True)
    check(calls == [(("synthetic-argument",), {"selected": True})], "claim original close forwarding count")
    observed = broker.close_calls[0]
    check(
        observed["returned"]
        and observed["engine_before"] is before_engine
        and observed["factory_before"] is before_factory
        and observed["engine_after"] is None,
        "claim actual close return observation",
    )
    # Distinct original lifetimes retain distinct actual engine identities.
    second_engine, second_factory = object(), object()
    broker.database._engine = second_engine
    broker.database._async_session_factory = second_factory
    await broker.close_db()
    check(
        broker.close_calls[-1]["engine_before"] is second_engine
        and broker.close_calls[-1]["factory_before"] is second_factory
        and broker.close_calls[-1]["returned"],
        "claim second original lifetime missing",
    )

    async def expect_close_fault(selected_broker, expected, selected_calls):
        try:
            await selected_broker.close_db()
        except BaseException as actual:
            if actual is not expected:
                raise
            check(
                selected_broker.failure is expected and selected_calls == ["called"],
                "claim original close error identity",
            )
            check(
                selected_broker.close_calls[-1]["error"] is expected
                and not selected_broker.close_calls[-1]["returned"],
                "claim close failure observation erased",
            )
        else:
            raise AssertionError("claim original close failure swallowed")

    for failure in [RuntimeError("synthetic-close-after-effect"), SystemExit(7), KeyboardInterrupt()]:
        broker = ClaimFrontendBroker()
        broker.database = SimpleNamespace(_engine=object(), _async_session_factory=object())
        calls.clear()

        async def failing_original(selected_failure=failure, selected_broker=broker):
            calls.append("called")
            selected_broker.database._engine = None
            raise selected_failure

        broker.original_close = failing_original
        await expect_close_fault(broker, failure, calls)

    # The SAME expected-fault handler must propagate a different real object;
    # only these explicitly injected controls may be consumed by this caller.
    for injected in [SystemExit(23), KeyboardInterrupt(), RuntimeError("synthetic-unexpected-close")]:
        broker = ClaimFrontendBroker()
        broker.database = SimpleNamespace(_engine=object(), _async_session_factory=object())
        calls.clear()

        async def unexpected_original(selected_error=injected):
            calls.append("called")
            raise selected_error

        broker.original_close = unexpected_original
        expected = RuntimeError("synthetic-different-expected-close")
        try:
            await expect_close_fault(broker, expected, calls)
        except BaseException as actual:
            if actual is not injected:
                raise
            check(broker.failure is injected and calls == ["called"], "claim unexpected original close control lost")
        else:
            raise AssertionError("claim unexpected close control swallowed")


async def test_claim_same_frame_decoder_rejects_partial_nonfinite_and_duplicate_data():
    from tests.parity.workflow_domain_harness import decode_claim_frame

    prefix = b'{"schema":"bifrost.test.claim-frontend-control/v1","phase":"before","seq":'
    for data, diagnostic in [
        (prefix + b"2}", "claim frontend frame grammar"),
        (prefix + b"NaN}\n", "claim frontend nonfinite constant"),
        (prefix + b"Infinity}\n", "claim frontend nonfinite constant"),
        (prefix + b'2,"seq":2}\n', "claim frontend duplicate field"),
        (prefix + b"2}\nextra", "claim frontend frame grammar"),
        (b" " + prefix + b"2}\n", "claim frontend noncompact frame"),
    ]:
        with pytest.raises(AssertionError, match=diagnostic):
            decode_claim_frame(data)


async def test_claim_same_settlement_gate_rejects_missing_alias_driver_and_cleanup_facts():
    from types import SimpleNamespace

    from tests.parity.workflow_domain_harness import validate_claim_settlement

    class Driver:
        def __init__(self):
            self.closed = True
            self.calls = 0

        def is_closed(self):
            self.calls += 1
            return self.closed

    engine, constructor, closer = object(), object(), object()
    driver = Driver()
    namespace = SimpleNamespace(
        failure=None,
        pending=set(),
        finished={item["case_id"] for item in CLAIM_CASES if item["execution_path"] != "none_native_boundary_only"},
        drivers=[driver],
        contexts={"synthetic": {"connections": [driver]}},
        database=SimpleNamespace(
            close_db=closer, create_async_engine=constructor, _engine=None, _async_session_factory=None
        ),
        wrapper_close=closer,
        wrapper_constructor=constructor,
        listeners=[],
        event=SimpleNamespace(contains=lambda *_args: True),
        engines=[engine],
        close_calls=[{"engine_before": engine, "returned": True, "engine_after": None, "factory_after": None}],
    )
    validate_claim_settlement(namespace)
    check(driver.calls == 1, "claim getter must run once per settlement")
    for field, changed, diagnostic in [
        ("failure", RuntimeError("synthetic-latched-close"), "claim observer unsettled or failed"),
        ("pending", {"pending"}, "claim observer unsettled or failed"),
        ("finished", set(), "claim observer coverage missing"),
        ("drivers", [], "claim observer driver coverage"),
        ("close_calls", [], "claim original disposal return missing"),
    ]:
        original = getattr(namespace, field)
        try:
            setattr(namespace, field, changed)
            with pytest.raises(AssertionError, match=diagnostic):
                validate_claim_settlement(namespace)
        finally:
            setattr(namespace, field, original)
    namespace.database.close_db = object()
    with pytest.raises(AssertionError, match="claim foreign alias before finish"):
        validate_claim_settlement(namespace)
    namespace.database.close_db = closer
    driver.closed = False
    with pytest.raises(AssertionError, match="claim actual driver still open"):
        validate_claim_settlement(namespace)
    from tests.parity.workflow_domain_harness import retain_claim_driver

    capture = SimpleNamespace(drivers=[])
    retain_claim_driver(capture, driver)
    retain_claim_driver(capture, driver)
    check(capture.drivers == [driver], "claim same physical driver identity duplicated")
    other = Driver()
    retain_claim_driver(capture, other)
    check(len(capture.drivers) == 2 and capture.drivers[1] is other, "claim distinct physical driver identity omitted")
    getter_failure = SystemExit(19)

    class FailedGetter:
        def is_closed(self):
            raise getter_failure

    namespace.drivers = [FailedGetter()]
    try:
        validate_claim_settlement(namespace)
    except BaseException as actual:
        if actual is not getter_failure:
            raise
        check(actual is getter_failure, "claim public getter error identity")
    else:
        raise AssertionError("claim public getter error swallowed")


async def test_claim_actual_finish_restores_independently_and_preserves_body_control():
    from types import SimpleNamespace

    def expect_finish_fault(broker, expected, removed, original_constructor, original_close):
        try:
            broker.finish()
        except BaseException as actual:
            if actual is not expected:
                raise
            check(broker.failure is expected, "claim finish primary control identity")
            check(len(removed) == 1, "claim actual listener removal omitted")
            check(
                broker.database.create_async_engine is original_constructor
                and broker.database.close_db is original_close,
                "claim independent alias restoration omitted",
            )
        else:
            raise AssertionError("claim finish swallowed original control")

    expected_scenarios = [(body, body) for body in [RuntimeError("synthetic-body"), SystemExit(8), KeyboardInterrupt()]]
    unexpected_scenarios = [
        (RuntimeError("synthetic-different-expected-finish"), actual)
        for actual in [SystemExit(24), KeyboardInterrupt(), RuntimeError("synthetic-unexpected-finish")]
    ]
    for expected, body in expected_scenarios + unexpected_scenarios:
        broker = ClaimFrontendBroker()
        original_constructor, original_close = object(), object()
        broker.original_constructor, broker.original_close = original_constructor, original_close
        broker.database = SimpleNamespace(create_async_engine=broker.wrapper_constructor, close_db=broker.wrapper_close)
        removed = []

        def remove(target, name, callback, selected_removed=removed):
            selected_removed.append((target, name, callback))
            raise RuntimeError("synthetic-secondary-restoration")

        broker.event = SimpleNamespace(remove=remove)
        broker.listeners = [(object(), "synthetic-event", object())]
        broker.poison(body)
        if expected is body:
            expect_finish_fault(broker, expected, removed, original_constructor, original_close)
        else:
            try:
                expect_finish_fault(broker, expected, removed, original_constructor, original_close)
            except BaseException as actual:
                if actual is not body:
                    raise
                check(
                    broker.failure is body and len(removed) == 1,
                    "claim unexpected finish control or independent removal lost",
                )
                check(
                    broker.database.create_async_engine is original_constructor
                    and broker.database.close_db is original_close,
                    "claim unexpected control prevented alias restoration",
                )
            else:
                raise AssertionError("claim unexpected finish control swallowed")


async def test_claim_actual_constructor_rejects_foreign_callsite_without_forwarding():
    from tests.parity.workflow_domain_harness import CLAIM_ACTOR

    broker = ClaimFrontendBroker()
    calls = []

    def constructor(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("unqualified constructor must not forward")

    broker.original_constructor = constructor
    context = CLAIM_ACTOR.set("synthetic-foreign-frame")
    try:
        with pytest.raises(AssertionError, match="claim constructor caller") as caught:
            broker.constructor("synthetic-nonsecret-url")
        check(not calls and broker.failure is caught.value, "claim constructor callsite refusal failed")
    finally:
        CLAIM_ACTOR.reset(context)


async def test_claim_same_endpoint_validator_excludes_boolean_port_and_payload_hosts():
    from tests.parity.workflow_domain_harness import claim_endpoint

    check(claim_endpoint("pgbouncer", 5432) == ("pgbouncer", 5432), "claim endpoint changed")
    for host, port, diagnostic in [
        ("pgbouncer", True, "claim constructor port grammar"),
        ("pgbouncer", 0, "claim constructor port grammar"),
        ("pgbouncer", 65536, "claim constructor port grammar"),
        ("pgbouncer\n", 5432, "claim constructor host grammar"),
        ("user@pgbouncer", 5432, "claim constructor host grammar"),
        ("é", 5432, "claim constructor host grammar"),
    ]:
        with pytest.raises(AssertionError, match=diagnostic):
            claim_endpoint(host, port)


async def test_claim_actual_exchange_latches_partial_write_and_deadline_errors():
    from tests.parity import workflow_domain_harness as harness

    class Stream:
        def __init__(self, overshoot=False):
            self.overshoot = overshoot

        def send(self, data):
            return len(data) + 1 if self.overshoot else len(data)

        def recv(self, _size):
            return b""

    original_select = harness.io_select.select
    for mode, diagnostic in [
        ("deadline", "claim frontend write deadline"),
        ("overshoot", "claim frontend write progress"),
        ("partial", "claim frontend partial EOF"),
    ]:
        broker = ClaimFrontendBroker()
        broker.socket = Stream(mode == "overshoot")

        def selected(readable, writable, exceptional, _timeout, selected_mode=mode):
            if selected_mode == "deadline":
                return [], [], []
            return readable, writable, exceptional

        primary: BaseException | None = None
        try:
            harness.io_select.select = selected
            with pytest.raises(AssertionError, match=diagnostic) as caught:
                broker.exchange({"schema": harness.CLAIM_CONTROL_SCHEMA, "phase": "after", "seq": 3}, "after", 4)
            check(broker.failure is caught.value, "claim exchange first failure not retained")
            try:
                broker.exchange({"schema": harness.CLAIM_CONTROL_SCHEMA, "phase": "after", "seq": 3}, "after", 4)
            except BaseException as actual:
                if actual is not caught.value:
                    raise
                check(actual is caught.value, "claim failed exchange incorrectly replayed")
            else:
                raise AssertionError("claim poisoned exchange admitted replay")
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                harness.io_select.select = original_select
            except BaseException:
                if primary is None:
                    raise
                # The pending original error still fails this control; a
                # secondary restoration fault cannot replace its traceback.
        check(harness.io_select.select is original_select, "claim control select binding not restored")
