"""One held F4 business experiment; actual observer retains the original endpoint.

The separately admitted host supervisor owns Docker and authoritative clocks.
This pytest process receives no daemon socket and does not launch actors.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from tests.parity.workflow_commit_fault import (
    FIXED_SQL,
    FaultAdmissionError,
    Restoration,
    bootstrap_channel,
    close_unadmitted_writer,
    closed,
    cycle,
    decode,
    driver_snapshot,
    finish_permission,
    query_witness,
    readback_connection,
    require,
    shorten,
)
from tests.parity.workflow_commit_fault_child import actor_environment, connect_control, qualify_sources

pytestmark = pytest.mark.e2e


class FixtureConstructors:
    """Forward real fixture/helper constructor calls without substituting makers."""

    def __init__(self):
        self.restoration = Restoration()
        self.engine = self.factory = None
        self.calls = {}
        self.helpers = []
        self.physical = None
        self.retained = False
        self.retained_cohorts = []
        self.environment = None

    def install(self, request):
        # This requests the already loaded genuine fixture module, not another
        # imported copy. Constructor observation precedes requesting its engine.
        module = sys.modules.get("tests.conftest")
        require(module is not None, "fixture_module")
        require(module.__file__ == "/app/tests/conftest.py", "fixture_origin")
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        require(
            module.create_async_engine is create_async_engine and module.async_sessionmaker is async_sessionmaker,
            "fixture_original_aliases",
        )
        self.environment = actor_environment()
        original_engine = module.create_async_engine
        original_factory = module.async_sessionmaker

        def engine(*args, **kwargs):
            require("engine" not in self.calls, "fixture_engine_repeated")
            result = original_engine(*args, **kwargs)
            self.calls["engine"] = (args, kwargs, result)
            self.engine = result
            return result

        def factory(*args, **kwargs):
            require("factory" not in self.calls, "fixture_factory_repeated")
            result = original_factory(*args, **kwargs)
            self.calls["factory"] = (args, kwargs, result)
            self.factory = result
            return result

        self.restoration.install(module, "create_async_engine", engine)
        self.restoration.install(module, "async_sessionmaker", factory)
        require(os.environ["BIFROST_DATABASE_URL"] == module.TEST_DATABASE_URL, "fixture_url")
        actual_engine = request.getfixturevalue("async_engine")
        actual_factory = request.getfixturevalue("async_session_factory")
        require(actual_engine is self.engine and actual_factory is self.factory, "fixture_return")
        args, kwargs, result = self.calls["engine"]
        require(
            args == (module.TEST_DATABASE_URL,)
            and kwargs == {"echo": False, "poolclass": NullPool}
            and result is actual_engine
            and isinstance(result.sync_engine.pool, NullPool),
            "fixture_engine_arguments",
        )
        args, kwargs, result = self.calls["factory"]
        require(
            len(args) == 1
            and args[0] is actual_engine
            and kwargs == {"class_": AsyncSession, "expire_on_commit": False}
            and result is actual_factory
            and result.kw["autoflush"] is True,
            "fixture_factory_arguments",
        )
        from tests.parity import workflow_domain_harness, workflow_sql_harness

        for helper, label in ((workflow_domain_harness, "super"), (workflow_sql_harness, "result")):
            previous = helper.async_sessionmaker

            def observe(*args, _previous=previous, _label=label, **kwargs):
                result = _previous(*args, **kwargs)
                require(len(args) == 1 and args[0] is actual_engine, "cohort_engine")
                require(
                    kwargs
                    == (
                        {"expire_on_commit": False}
                        if _label == "super"
                        else {"autoflush": False, "expire_on_commit": False}
                    ),
                    "cohort_factory_arguments",
                )
                self.helpers.append((_label, result))
                require(len(self.helpers) <= 4, "cohort_factory_bound")
                return result

            self.restoration.install(helper, "async_sessionmaker", observe)

    def ready(self):
        endpoint = make_url(os.environ["BIFROST_DATABASE_URL"])
        require(
            endpoint.drivername == "postgresql+asyncpg"
            and not endpoint.query
            and endpoint.host is not None
            and type(endpoint.port) is int,
            "fixture_endpoint",
        )
        return {
            "schema": "bifrost.test.f4-constructor-observation/v1",
            "role": "observer",
            "env_count": 28,
            "env_source_equal": actor_environment() == self.environment,
            "source_calls": {"settings": 0, "prepare": 0, "engine": 1, "factory": 1, "guard": 0},
            "endpoint": {"hostname": endpoint.host, "port": endpoint.port, "drivername": endpoint.drivername},
            "engine": {
                "origin_matches": True,
                "input_matches": True,
                "kwargs_matches": True,
                "returned_matches": True,
                "pool": "null_pool",
            },
            "factory": {
                "origin_matches": True,
                "engine_matches": True,
                "returned_matches": True,
                "expire_on_commit": False,
                "autoflush": True,
            },
            "connection": {
                "record_join": False,
                "selected_connection_join": False,
                "frontend_join": False,
                "driver_args_observed": False,
                "negotiated_transport_observed": False,
            },
            "restoration": {"pending": True, "failed": False},
        }


@pytest.fixture(scope="session")
def f4_constructed(setup_test_environment, request):
    actor_environment()
    qualify_sources()
    owner = FixtureConstructors()
    original = None
    try:
        owner.install(request)
        yield owner
    except BaseException as error:
        original = error
        raise
    finally:
        try:
            if not owner.retained:
                owner.restoration.restore()
        except BaseException:
            if original is None:
                raise


class ObserverPhysical:
    """All 18 genuine NullPool lifetimes, including both cleanup transactions."""

    def __init__(self, engine, channel):
        self.engine, self.channel = engine, channel
        self.pending = {}
        self.records = []
        self.sessions = []
        self.listeners = []
        self.accepted = False
        self.failed = False
        self.readbacks = []
        self.purpose = None
        self.maker = None

    def install(self):
        endpoint = make_url(os.environ["BIFROST_DATABASE_URL"])
        expected = {
            "host": endpoint.host,
            "port": endpoint.port,
            "user": endpoint.username,
            "password": endpoint.password,
            "database": endpoint.database,
        }

        def preconnect(dialect, record, cargs, cparams):
            require(
                dialect is self.engine.sync_engine.dialect and not cargs and cparams == expected, "observer_preconnect"
            )
            require(record not in self.pending and len(self.records) < 18, "observer_pair_bound")
            self.pending[record] = None

        def connected(adapter, record):
            require(record in self.pending and self.pending[record] is None, "observer_record")
            require(self.purpose is not None, "observer_purpose")
            entry = {
                "record": record,
                "adapter": adapter,
                "purpose": self.purpose,
                "connection": None,
                "checked_out": False,
            }
            self.pending[record] = entry
            self.records.append(entry)

        def checkout(adapter, record, _proxy):
            entry = self.pending.get(record)
            require(entry is not None and entry["adapter"] is adapter and not entry["checked_out"], "observer_checkout")
            entry["checked_out"] = True
            if not self.accepted:

                async def accept(_driver):
                    await self.channel.send(
                        "connection_ready",
                        {
                            "schema": "bifrost.test.f4-connection-observation/v1",
                            "role": "observer",
                            "engine_join": True,
                            "preconnect_match": True,
                            "record_join": True,
                            "checkout_join": True,
                            "selected_connection_join": False,
                            "frontend_join": False,
                            "negotiated_tls": None,
                        },
                    )
                    body = await self.channel.receive("connection_accept", ("role",))
                    require(body["role"] == "observer", "observer_connection_role")
                    self.accepted = True

                adapter.run_async(accept)

        def before(connection, _cursor, _statement, _parameters, _context, _many):
            adapter = connection.connection.dbapi_connection
            matches = [entry for entry in self.records if entry["adapter"] is adapter]
            require(len(matches) == 1 and matches[0]["checked_out"], "observer_sql_pair")
            entry = matches[0]
            if entry["connection"] is None:
                entry["connection"] = connection
            require(
                entry["connection"] is connection and connection.engine is self.engine.sync_engine,
                "observer_sql_connection",
            )

        def after_begin(session, transaction, connection):
            if connection.engine is self.engine.sync_engine:
                require(
                    self.maker is not None
                    and self.maker.kw["bind"] is self.engine
                    and self.maker.kw["autoflush"] is False
                    and self.maker.kw["expire_on_commit"] is False
                    and session.autoflush is False
                    and session.expire_on_commit is False,
                    "observer_used_session_profile",
                )
                self.sessions.append((session, transaction, connection, self.maker))
                require(len(self.sessions) <= 12, "observer_session_bound")

        for target, name, callback in (
            (self.engine.sync_engine, "do_connect", preconnect),
            (self.engine.sync_engine.pool, "connect", connected),
            (self.engine.sync_engine.pool, "checkout", checkout),
            (self.engine.sync_engine, "before_cursor_execute", before),
            (Session, "after_begin", after_begin),
        ):
            require(not event.contains(target, name, callback), "observer_listener_preexisting")
            self.listeners.append((target, name, callback))
            event.listen(target, name, callback)

    async def operation(self, purpose, operation, count, maker=None):
        require(self.purpose is None, "observer_operation_overlap")
        start = len(self.records)
        self.purpose = purpose
        self.maker = maker
        try:
            async with asyncio.timeout(self.channel.deadline.remaining(purpose == "cleanup")):
                result = await operation()
            require(
                len(self.records) - start == count
                and all(entry["connection"] is not None and entry["checked_out"] for entry in self.records[start:]),
                "observer_operation_pairs",
            )
            if maker is not None:
                for entry in self.records[start:]:
                    require(
                        len(
                            [
                                binding
                                for binding in self.sessions
                                if binding[2] is entry["connection"] and binding[3] is maker
                            ]
                        )
                        == 1,
                        "observer_used_maker_session_join",
                    )
            return result
        finally:
            self.purpose = None
            self.maker = None

    def restore(self):
        original = None
        for target, name, callback in reversed(self.listeners):
            try:
                require(event.contains(target, name, callback), "observer_listener_missing")
                event.remove(target, name, callback)
                self.listeners.remove((target, name, callback))
            except BaseException as error:
                if original is None:
                    original = error
        if original is not None:
            raise original


async def fresh(physical, owned, foreign, witness=None):
    async def read(connection):
        async def driver_read(driver):
            if witness is None:
                return await driver_snapshot(driver, owned, foreign)
            rows = await driver.fetch(FIXED_SQL["fate"], witness["xid"])
            require(len(rows) == 1, "fate_count")
            row = dict(rows[0])
            closed(row, ("fate", "database_name", "server_address", "server_port", "server_version_num"))
            require(all(row[key] == witness[key] for key in row if key != "fate"), "fate_cluster_query_join")
            require(row["fate"] in ("committed", "aborted", "in progress") or row["fate"] is None, "fate_value")
            return row["fate"]

        return await connection.run_sync(lambda actual: actual.connection.dbapi_connection.run_async(driver_read))

    return await readback_connection(physical.engine, read, physical.channel.deadline, physical)


@pytest_asyncio.fixture
async def f4_observer(f4_constructed):
    reader = writer = channel = physical = None
    original = None
    try:
        reader, writer = await connect_control("/run/f4-observer.sock", 0o666)
        channel, _body = await bootstrap_channel(reader, writer, relay=False)
        await channel.send("hello", {})
        body = await channel.receive("admit", ("remaining_ms",))
        shorten(channel.deadline, body["remaining_ms"], asyncio.get_running_loop().time())
        physical = ObserverPhysical(f4_constructed.engine, channel)
        physical.install()
        await channel.send("constructor_ready", f4_constructed.ready())
        accept = await channel.receive("constructor_accept", ("role",))
        require(accept["role"] == "observer", "observer_constructor_role")
        yield f4_constructed, physical, channel
    except BaseException as error:
        original = error
        raise
    finally:
        cleanup_error = None
        if physical is not None and not f4_constructed.retained:
            try:
                physical.restore()
            except BaseException as error:
                cleanup_error = error
        if channel is not None and not f4_constructed.retained:
            try:
                await channel.close()
            except BaseException as error:
                cleanup_error = cleanup_error or error
        elif writer is not None and not f4_constructed.retained:
            cleanup_error = close_unadmitted_writer(writer, {"failed": False}, cleanup_error)
        if original is None and cleanup_error is not None:
            raise cleanup_error


async def test_workflow_commit_fault(f4_observer, record_property):
    """Exactly two real lanes of one held transaction-response requirement."""
    from tests.parity.workflow_sql_harness import ResultCohort, load_fixture, request_bytes

    constructed, physical, channel = f4_observer
    channel.deadline.remaining()
    controls = await source_controls()
    record_property("f4_source_controls", len(controls))
    fixture = next(case for case in load_fixture()["cases"] if case["case_id"] == "r-attempt-claimed-unstarted")
    cohorts = []
    original = None
    cleanup_permitted = False
    try:
        for number, actor in ((1, "python"), (2, "rust")):
            cohort = ResultCohort(constructed.engine, fixture)
            cohorts.append(cohort)
            require(
                any(label == "result" and maker is cohort.sessions for label, maker in constructed.helpers),
                "used_cohort_maker",
            )
            await physical.operation("seed", cohort.seed, 5, cohort.sessions)
            owned, foreign = str(cohort.ids["execution"]), str(cohort.ids["foreign"])
            before = await physical.operation(
                "before", lambda owned=owned, foreign=foreign: fresh(physical, owned, foreign), 1
            )
            request = decode(request_bytes(cohort, fixture["raw_fields_json"]))
            scope = {
                "foreign_execution_id": foreign,
                "attempt_id": str(cohort.ids["attempt"]),
                "foreign_attempt_id": str(cohort.ids["foreign_attempt"]),
            }
            await channel.send(
                "seeded", {"cycle": number, "actor": actor, "scope": scope, "request": request}, data=True
            )
            settled = await channel.receive(
                "actor_settled", ("cycle", "actor", "execution_id", "scope", "query_witness")
            )
            cycle(settled, (number, actor))
            require(settled["execution_id"] == owned and settled["scope"] == scope, "settled_scope")
            witness = query_witness(settled["query_witness"])
            fate = await physical.operation(
                "fate",
                lambda owned=owned, foreign=foreign, witness=witness: fresh(physical, owned, foreign, witness),
                1,
            )
            rows = await physical.operation(
                "final", lambda owned=owned, foreign=foreign: fresh(physical, owned, foreign), 1
            )
            require(fate == "committed", "fate_not_committed")
            require(len(rows["executions"]) == 2 and len(rows["attempts"]) == 2, "observed_row_count")

            def foreign_rows(snapshot, foreign=foreign):
                return {
                    "executions": [row for row in snapshot["executions"] if row["id"]["value"] == foreign],
                    "attempts": [row for row in snapshot["attempts"] if row["execution_id"]["value"] == foreign],
                }

            require(foreign_rows(rows) == foreign_rows(before) and rows["logs"] == before["logs"], "foreign_keep")
            await channel.send(
                "observed",
                {"cycle": number, "actor": actor, "query_witness": witness, "fate": fate, "rows": rows},
                data=True,
            )
            if number == 1:
                advance = await channel.receive("advance", ("cycle", "actor"))
                cycle(advance, (1, "python"))
        permit = await channel.receive("finish_permit", ("cycle", "actor"))
        finish_permission(permit)
        cleanup_permitted = True
    except BaseException as error:
        original = error
    finally:
        if physical.failed or any(not record["closed"] for record in physical.readbacks):
            cleanup_permitted = False
            constructed.retained = True
            original = original or FaultAdmissionError("retained_readback_custody")
        if cleanup_permitted:
            for cohort in reversed(cohorts):
                try:
                    await physical.operation(
                        "cleanup", lambda cohort=cohort: cohort.close(channel.deadline.total_end), 1, cohort.sessions
                    )
                except BaseException as error:
                    if original is None:
                        original = error
        else:
            # No unknown-effect reseed/delete or dependent teardown permission.
            # Host must settle every actor and dispose its exact owned stack;
            # current pytest fixture finalizers remain outside this guarantee.
            constructed.retained = bool(cohorts)
            constructed.retained_cohorts = cohorts
            original = original or FaultAdmissionError("retained_cohort_custody")
        if original is None:
            require(len(physical.records) == 18 and len(constructed.helpers) == 4, "finished_lifetimes")
            require(
                all(entry["adapter"].driver_connection.is_closed() for entry in physical.records),
                "observer_driver_live",
            )
            physical.restore()
            constructed.restoration.restore()
            physical.readbacks.clear()
            physical.records.clear()
            physical.sessions.clear()
            physical.pending.clear()
            constructed.helpers.clear()
            await channel.send("finished", {"source_closed": True}, cleanup=True)
            record_property("f4_lanes", 2)
    if original is not None:
        raise original


async def source_controls():
    """Inert local objects exercise the real helpers; never business/clock authority."""
    from types import SimpleNamespace

    from tests.parity import workflow_commit_fault_child as child
    from tests.parity.workflow_commit_fault import (
        CONTROL_LIMIT,
        OBSERVER_SCHEMA,
        Channel,
        Deadline,
        RelayPair,
        SdkTrace,
        SessionJoins,
        acquire_task,
        bootstrap_value,
        cleanup_failure,
        encode,
        independent_cleanup,
        source_witness,
    )

    completed = []
    actual_environment = actor_environment()
    original_environment = dict(os.environ)
    shared = sys.modules.get("tests.parity.workflow_commit_fault")
    require(shared is not None and shared.closed is closed and child.os is os, "environment_control_origins")

    def environment_control(candidate, expected_label=None):
        temporary = Restoration()
        original_error = None
        try:
            temporary.install(child, "os", SimpleNamespace(environ=candidate))
            if expected_label is None:
                require(actor_environment() == actual_environment, "environment_control_positive")
            else:
                try:
                    actor_environment()
                except FaultAdmissionError as error:
                    require(error.label == expected_label, "environment_control_label")
                else:
                    raise AssertionError("F4 environment negative admitted")
        except BaseException as error:
            original_error = error
        finally:
            try:
                temporary.restore()
            except BaseException as error:
                if original_error is None:
                    original_error = error
        if original_error is not None:
            raise original_error
        require(
            child.os is os and sys.modules.get("tests.parity.workflow_commit_fault") is shared and shared.closed is closed,
            "environment_control_restoration",
        )
        require(dict(os.environ) == original_environment, "environment_control_real_environment")

    environment_control(original_environment.copy())
    missing_version = original_environment.copy()
    del missing_version["BIFROST_VERSION"]
    environment_control(missing_version, "environment_missing")
    environment_control({**original_environment, "BIFROST_VERSION": "not-qualified"}, "environment_profile")
    environment_control({**original_environment, "BIFROST_F4_UNREVIEWED": "1"}, "environment_extra")
    environment_control({**original_environment, "ANTHROPIC_API_TEST_KEY": "not-empty"}, "vendor_environment")
    require(actor_environment() == actual_environment, "environment_control_actual_restored")
    completed.append("environment")
    invocation = "abcdef00-0000-4000-8000-000000000001"

    def rejected(operation):
        try:
            operation()
        except FaultAdmissionError:
            return
        raise AssertionError("F4 inert negative admitted")

    consumer_code, sdk_code, session = object(), object(), object()

    def trace():
        owner = SdkTrace(consumer_code, invocation, lambda _module, _code: False)
        owner.sdk_code, owner.session = sdk_code, session
        return owner

    def frame(code=sdk_code, *, line=0, values=None):
        return SimpleNamespace(
            f_code=code,
            f_lineno=line,
            f_locals=values if values is not None else {"execution_id": invocation, "session": session},
        )

    owner = trace()
    actual = frame()
    owner.observe(actual, "call")
    owner.observe(actual, "return")
    owner.observe(actual, "call")
    require(owner.call_count == 1 and owner.sdk_frame is actual, "same_frame_resumption")
    rejected(lambda: owner.observe(frame(), "call"))

    def witness_control(candidate):
        # Only the public getter is temporarily redirected; no real trace is
        # installed, no await/import/product call occurs while the seam lives.
        temporary = Restoration()
        candidate.installed = True
        try:
            temporary.install(sys, "gettrace", lambda: candidate.callback)
            return candidate.witness(True)
        finally:
            temporary.restore()
            candidate.installed = False

    missing = trace()
    missing.function_source = missing.shared_source = missing.result_zero = missing.arguments_match = True
    rejected(lambda: witness_control(missing))
    caught = trace()
    caught._observe(frame(consumer_code, line=523, values={}), "line", None)
    require(caught.failed, "caught_sdk_error_control")
    completed.append("sdk_call")

    for value in (False, True, None, 1, -1):
        owner = trace()
        owner.observe(frame(), "call")
        rejected(
            lambda owner=owner, value=value: owner.observe(
                frame(consumer_code, line=521, values={"changes_count": value}), "line"
            )
        )
    owner = trace()
    owner.observe(frame(), "call")
    owner.observe(frame(consumer_code, line=521, values={"changes_count": 0}), "line")
    require(owner.result_zero, "builtin_zero_control")
    owner.function_source = owner.shared_source = True
    require(witness_control(owner)["sdk_call_count"] == 1, "complete_inert_witness")
    owner.failed = True
    rejected(lambda: witness_control(owner))
    owner = trace()
    owner.observe(frame(), "return")
    require(not owner.result_zero, "return_event_not_success")
    completed.append("sdk_return")

    # Temporary public-access seams have no await/product call and restore
    # independently. The actual process trace is never installed or replaced.
    restoration = Restoration()

    def foreign(*_args):
        return None

    original_gettrace = sys.gettrace
    try:
        restoration.install(sys, "gettrace", lambda: foreign)
        owner = trace()
        rejected(owner.install)
        owner.installed = True
        rejected(owner.restore)
        require(sys.gettrace() is foreign, "foreign_trace_preserved")
        broken = frame(values={"execution_id": invocation, "session": object()})
        owner._observe(broken, "call", None)
        require(owner.failed, "observation_failure_latch")
    finally:
        restoration.restore()
    require(sys.gettrace is original_gettrace, "control_trace_seam_restored")
    completed.append("trace_custody")

    class Events:
        def __init__(self):
            self.callbacks = []

        def contains(self, _target, _name, callback):
            return any(value is callback for value in self.callbacks)

        def listen(self, _target, _name, callback):
            self.callbacks.append(callback)

        def remove(self, _target, _name, callback):
            self.callbacks = [value for value in self.callbacks if value is not callback]

    engine, transaction, sync_session = object(), object(), object()
    connection = SimpleNamespace(engine=engine)
    api = Events()
    joins = SessionJoins(engine, api, object())
    joins.install()
    joins.callback(sync_session, transaction, SimpleNamespace(engine=object()))
    require(not joins.records, "wrong_engine_ignored")
    joins.callback(sync_session, transaction, connection)
    require(joins.join(SimpleNamespace(sync_session=sync_session), connection), "actual_pair_control")
    rejected(lambda: joins.join(SimpleNamespace(sync_session=object()), connection))
    rejected(lambda: joins.join(SimpleNamespace(sync_session=sync_session), SimpleNamespace(engine=engine)))
    joins.callback(sync_session, object(), connection)
    rejected(lambda: joins.join(SimpleNamespace(sync_session=sync_session), connection))
    completed.append("session_connection")

    api.callbacks.append(foreign)
    joins.restore()
    require(api.callbacks == [foreign], "foreign_listener_preserved")
    joins.install()
    api.remove(None, None, joins.callback)
    rejected(joins.restore)
    primary = KeyboardInterrupt()
    attempts = []

    def secondary():
        attempts.append("first")
        raise FaultAdmissionError("secondary")

    try:
        independent_cleanup((secondary, lambda: attempts.append("second")), primary)
    except BaseException as error:
        require(error is primary and attempts == ["first", "second"], "original_priority_control")
    else:
        raise AssertionError("F4 primary exception missing")
    custody = {"failed": False}
    secondary_error = FaultAdmissionError("secondary")
    require(cleanup_failure(custody, primary, secondary_error) is primary, "cleanup_original_identity")
    require(custody["failed"] is True, "cleanup_failure_latched")
    custody = {"failed": False}
    require(cleanup_failure(custody, None, secondary_error) is secondary_error, "cleanup_first_identity")
    require(custody["failed"] is True, "cleanup_first_latched")

    class Coroutine:
        def __init__(self, close_error=None):
            self.closed = False
            self.close_error = close_error

        def close(self):
            self.closed = True
            if self.close_error is not None:
                raise self.close_error

    def refusing(_coroutine):
        raise primary

    for close_error in (None, secondary_error):
        coroutine = Coroutine(close_error)
        custody = {"failed": False}
        try:
            acquire_task(coroutine, custody, refusing)
        except BaseException as error:
            require(error is primary and coroutine.closed, "task_acquisition_first_error")
            require(custody["failed"] is (close_error is not None), "task_acquisition_close_custody")
        else:
            raise AssertionError("F4 task constructor negative admitted")

    returned = object()
    coroutine = Coroutine()
    require(acquire_task(coroutine, {"failed": False}, lambda _coroutine: returned) is returned, "task_return_retained")
    require(not coroutine.closed, "submitted_coroutine_not_closed")
    coroutine.close()

    class LocalDeadline:
        def remaining(self, _cleanup=False):
            return 1

    class Connection:
        def __init__(self, begin_error=None, rollback_error=None, close_error=None, unresolved=False, start_error=None):
            self.start_error = start_error
            self.begin_error, self.rollback_error, self.close_error = begin_error, rollback_error, close_error
            self.unresolved = unresolved
            self.closed = False
            self.attempts = []

        def __await__(self):
            async def started():
                if self.start_error is not None:
                    raise self.start_error
                return self

            return started().__await__()

        async def begin(self):
            if self.begin_error is not None:
                raise self.begin_error
            return self

        async def exec_driver_sql(self, _sql):
            return None

        async def rollback(self):
            self.attempts.append("rollback")
            if self.rollback_error is not None:
                raise self.rollback_error

        async def close(self):
            self.attempts.append("close")
            if self.close_error is not None:
                raise self.close_error
            self.closed = not self.unresolved

    class Engine:
        def __init__(self, connection):
            self.connection = connection

        def connect(self):
            return self.connection

    async def failing_read(_connection):
        raise primary

    for begin_error, rollback_error, close_error, unresolved in (
        (primary, None, secondary_error, False),
        (None, secondary_error, secondary_error, False),
        (None, None, None, True),
        (None, None, None, False),
    ):
        connection = Connection(begin_error, rollback_error, close_error, unresolved)
        custody = SimpleNamespace(failed=False, readbacks=[])
        try:
            await readback_connection(Engine(connection), failing_read, LocalDeadline(), custody)
        except BaseException as error:
            require(error is primary, "readback_first_error")
        else:
            raise AssertionError("F4 readback primary missing")
        require(connection.attempts == (["close"] if begin_error else ["rollback", "close"]), "readback_attempts")
        require(custody.failed is bool(rollback_error or close_error or unresolved), "readback_failed_custody")
        require(custody.readbacks[0]["closed"] is (close_error is None and not unresolved), "readback_close_fact")
    connection = Connection(close_error=secondary_error, start_error=primary)
    custody = SimpleNamespace(failed=False, readbacks=[])
    try:
        await readback_connection(Engine(connection), failing_read, LocalDeadline(), custody)
    except BaseException as error:
        require(error is primary and connection.attempts == ["close"], "readback_start_first_error")
    else:
        raise AssertionError("F4 readback start negative admitted")
    require(custody.failed and custody.readbacks[0]["connection"] is connection, "readback_partial_owned")

    async def successful_read(_connection):
        return 7

    connection = Connection(close_error=secondary_error)
    custody = SimpleNamespace(failed=False, readbacks=[])
    try:
        await readback_connection(Engine(connection), successful_read, LocalDeadline(), custody)
    except BaseException as error:
        require(error is secondary_error and custody.failed, "readback_close_only_error")
    else:
        raise AssertionError("F4 readback close failure admitted")
    completed.append("cleanup")

    class Selector:
        def __init__(self):
            self.entries = {}

        def register(self, endpoint, mask, data):
            self.entries[endpoint] = (mask, data)

        def modify(self, endpoint, mask, data):
            self.entries[endpoint] = (mask, data)

        def unregister(self, endpoint):
            del self.entries[endpoint]

    class Endpoint:
        def __init__(self):
            self.fd = 1
            self.data = b"response"
            self.sent = bytearray()

        def setblocking(self, _value):
            return None

        def fileno(self):
            return self.fd

        def recv(self, maximum):
            data, self.data = self.data[:maximum], self.data[maximum:]
            return data

        def send(self, data):
            sent = min(2, len(data))
            self.sent.extend(data[:sent])
            return sent

        def close(self):
            self.fd = -1

    import selectors

    selector, frontend, upstream = Selector(), Endpoint(), Endpoint()
    pair = RelayPair(selector, frontend, upstream)
    pair.to_frontend.extend(b"x" * 65536)
    pair.interests()
    require(not selector.entries[upstream][0] & selectors.EVENT_READ, "fullqueue_read_disabled")
    pair.arm()
    require(
        not pair.to_frontend
        and pair.suppressed_bytes == 65536
        and selector.entries[upstream][0] & selectors.EVENT_READ,
        "arm_reenabled_actual_read",
    )
    pair.dispatch("upstream", selectors.EVENT_READ)
    require(pair.closed and pair.settled()["transport_closed"] and not selector.entries, "closed_pair_control")
    rejected(lambda: pair.dispatch("frontend", selectors.EVENT_WRITE))
    pair = RelayPair(Selector(), Endpoint(), Endpoint())
    pair.to_upstream.extend(b"abcdef")
    pair.dispatch("upstream", selectors.EVENT_WRITE)
    require(
        pair.to_upstream == b"cdef" and pair.upstream.sent == b"ab" and pair.upstream_bytes == 2, "partial_send_prefix"
    )
    pair.close()
    rejected(lambda: decode(b'{"a":1,"a":2}'))
    rejected(lambda: decode(b"[" * 65 + b"0" + b"]" * 65))
    finish_permission({"cycle": 2, "actor": "rust"})
    for body in ({"cycle": 2}, {"cycle": 2, "actor": "python"}, {"cycle": 1, "actor": "rust"}):
        rejected(lambda body=body: finish_permission(body))

    class Reader:
        def __init__(self, raw):
            self.raw = raw

        async def readexactly(self, count):
            chunk, self.raw = self.raw[:count], self.raw[count:]
            if len(chunk) != count:
                raise asyncio.IncompleteReadError(chunk, count)
            return chunk

    class Writer:
        def __init__(self, close_error=None):
            self.closed = False
            self.close_error = close_error

        def close(self):
            self.closed = True
            if self.close_error is not None:
                raise self.close_error

        async def wait_closed(self):
            return None

    def framed(value):
        raw = encode(value, CONTROL_LIMIT)
        return len(raw).to_bytes(4, "big") + raw

    async def async_rejected(operation, label=None):
        try:
            await operation()
        except FaultAdmissionError as error:
            if label is not None:
                require(error.label == label, "framing_rejection_label")
            return
        raise AssertionError("F4 framing negative admitted")

    valid = {
        "schema": OBSERVER_SCHEMA,
        "lane": "supervisor",
        "invocation": invocation,
        "seq": 1,
        "kind": "finish_permit",
        "body": {"cycle": 2, "actor": "rust"},
    }
    channel = Channel(
        Reader(framed(valid)), Writer(), invocation, "observer", "supervisor", OBSERVER_SCHEMA, LocalDeadline()
    )
    finish_permission(await channel.receive("finish_permit", ("cycle", "actor")))
    await channel.close()
    require(channel.closed and channel.writer.closed, "channel_close_control")
    channel = Channel(
        Reader(framed(valid) + framed(valid)),
        Writer(),
        invocation,
        "observer",
        "supervisor",
        OBSERVER_SCHEMA,
        LocalDeadline(),
    )
    await channel.receive("finish_permit", ("cycle", "actor"))
    await async_rejected(lambda: channel.receive("finish_permit", ("cycle", "actor")), "integer")
    require(channel.received == 1, "repeat_sequence_not_advanced")
    for raw, label in (
        (b"\0\0", "partial_frame"),
        ((5).to_bytes(4, "big") + b"x", "partial_frame"),
        ((0).to_bytes(4, "big"), "frame_bound"),
        ((CONTROL_LIMIT + 1).to_bytes(4, "big"), "frame_bound"),
        (framed({**valid, "seq": 2}), "integer"),
        (framed({**valid, "lane": "observer"}), "frame_identity"),
        (framed({**valid, "invocation": "abcdef00-0000-4000-8000-000000000002"}), "frame_identity"),
        (framed({**valid, "kind": "bootstrap"}), "frame_identity"),
    ):
        channel = Channel(Reader(raw), Writer(), invocation, "observer", "supervisor", OBSERVER_SCHEMA, LocalDeadline())
        await async_rejected(lambda channel=channel: channel.receive("finish_permit", ("cycle", "actor")), label)
        require(channel.received == 0, "rejected_sequence_not_advanced")
    completed.append("protocol_relay")

    envelope = {
        "schema": "bifrost.test.workflow-commit-observer/v1",
        "lane": "supervisor",
        "invocation": invocation,
        "seq": 1,
        "kind": "bootstrap",
        "body": {"remaining_ms": 75000},
    }
    require(bootstrap_value(envelope, relay=False) is envelope, "bootstrap_valid")
    for key, value in (
        ("schema", None),
        ("lane", "observer"),
        ("invocation", None),
        ("seq", True),
        ("kind", "hello"),
        ("invocation", invocation.upper()),
    ):
        invalid = {**envelope, key: value}
        rejected(lambda invalid=invalid: bootstrap_value(invalid, relay=False))
    rejected(lambda: bootstrap_value({**envelope, "unknown": 0}, relay=False))
    for value in (None, False, 0, 75001):
        rejected(lambda value=value: bootstrap_value({**envelope, "body": {"remaining_ms": value}}, relay=False))
    deadline = Deadline(50, 65)
    shorten(deadline, 75000, 1)
    require((deadline.work_end, deadline.total_end) == (50, 65), "admit_never_extends")
    shorten(deadline, 1000, 2)
    require((deadline.work_end, deadline.total_end) == (3, 18), "admit_shortens_both")
    rejected(lambda: source_witness({}))
    boot = framed(envelope)
    writer = Writer()
    channel, body = await bootstrap_channel(Reader(boot), writer, relay=False)
    require(channel.invocation == invocation and channel.received == 1 and body == envelope["body"], "real_bootstrap")
    channel.reader = Reader(framed({**valid, "seq": 1}))
    await async_rejected(lambda: channel.receive("finish_permit", ("cycle", "actor")), "integer")
    channel.reader = Reader(framed({**valid, "seq": 2, "invocation": "abcdef00-0000-4000-8000-000000000002"}))
    await async_rejected(lambda: channel.receive("finish_permit", ("cycle", "actor")), "frame_identity")
    channel.reader = Reader(framed({**valid, "seq": 2}))
    finish_permission(await channel.receive("finish_permit", ("cycle", "actor")))
    await channel.close()
    require(channel.closed and writer.closed, "bootstrap_channel_close_control")
    for raw, label in (
        (b"\0", "bootstrap_partial"),
        ((4).to_bytes(4, "big") + b"x", "bootstrap_partial"),
        ((0).to_bytes(4, "big"), "bootstrap_bound"),
        ((CONTROL_LIMIT + 1).to_bytes(4, "big"), "bootstrap_bound"),
        (framed({**envelope, "seq": 2}), "integer"),
        (framed({**envelope, "lane": "observer"}), "bootstrap_lane"),
        (framed({**envelope, "invocation": None}), "uuid"),
    ):
        for close_error in (None, secondary_error):
            writer = Writer(close_error)
            original = None
            try:
                await bootstrap_channel(Reader(raw), writer, relay=False)
            except FaultAdmissionError as error:
                require(error.label == label, "bootstrap_rejection_label")
                original = error
            else:
                raise AssertionError("F4 bootstrap negative admitted")
            custody = {"failed": False}
            require(close_unadmitted_writer(writer, custody, original) is original, "bootstrap_close_first_error")
            require(writer.closed and custody["failed"] is (close_error is not None), "bootstrap_partial_custody")
    completed.append("bootstrap")
    require(len(completed) == 8 and len(set(completed)) == 8, "control_family_roster")
    return tuple(completed)
