"""Fixed F4 first-party child; no Docker access and no public execution authority."""

from __future__ import annotations

import asyncio
import os
import selectors
import socket
import stat
import sys
from contextlib import suppress

from tests.parity.workflow_commit_fault import (
    IPC_SCHEMA,
    Channel,
    Deadline,
    FaultAdmissionError,
    RelayPair,
    acquire_task,
    bootstrap_channel,
    close_unadmitted_writer,
    cycle,
    decode,
    fault_input,
    require,
)


async def connect_control(path, mode):
    identity = os.lstat(path)
    require(stat.S_ISSOCK(identity.st_mode) and stat.S_IMODE(identity.st_mode) == mode, "control_inode")
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        current = os.lstat(path)
        require(
            (identity.st_dev, identity.st_ino, identity.st_mode, identity.st_uid, identity.st_gid)
            == (current.st_dev, current.st_ino, current.st_mode, current.st_uid, current.st_gid),
            "control_replaced",
        )
    except BaseException as error:
        with suppress(BaseException):
            writer.close()
        raise error
    return reader, writer


async def settle_task(task, deadline, original):
    if task is not None:
        task.cancel()
        try:
            async with asyncio.timeout(deadline.remaining(True)):
                await task
        except asyncio.CancelledError:
            if not task.done():
                return original or FaultAdmissionError("live_task")
        except BaseException as error:
            if original is None:
                original = error
    return original


async def relay():
    reader = writer = channel = listener = selector = pair = control_task = None
    original = None
    custody = {"failed": False}
    try:
        reader, writer = await connect_control("/run/f4-control.sock", 0o600)
        channel, body = await bootstrap_channel(reader, writer, relay=True)
        await channel.send("hello", {})
        await channel.receive("hello_accept", ())
        selector = selectors.DefaultSelector()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setblocking(False)
        listener.bind(("0.0.0.0", body["port"]))
        listener.listen(1)
        loop = asyncio.get_running_loop()
        for number, actor in ((1, "python"), (2, "rust")):
            async with asyncio.timeout(channel.deadline.remaining()):
                frontend, _address = await loop.sock_accept(listener)
            upstream = None
            try:
                async with asyncio.timeout(channel.deadline.remaining()):
                    endpoints = await loop.getaddrinfo(
                        body["hostname"], body["port"], family=socket.AF_INET, type=socket.SOCK_STREAM
                    )
                addresses = {endpoint[4] for endpoint in endpoints}
                require(len(addresses) == 1, "upstream_resolution")
                upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                upstream.setblocking(False)
                async with asyncio.timeout(channel.deadline.remaining()):
                    await loop.sock_connect(upstream, next(iter(addresses)))
                pair = RelayPair(selector, frontend, upstream)
                selector.register(listener, selectors.EVENT_READ, "listener")
            except BaseException as error:
                for endpoint in (frontend, upstream):
                    if endpoint is not None:
                        # Both closes are attempted; the acquisition error wins.
                        with suppress(BaseException):
                            endpoint.close()
                raise error
            peer = frontend.getpeername()
            local = frontend.getsockname()
            remote = upstream.getpeername()
            binding = {"cycle": number, "actor": actor, "connection": number}
            await channel.send(
                "frontend_ready",
                {
                    **binding,
                    "peer_address": peer[0],
                    "peer_port": peer[1],
                    "local_address": local[0],
                    "local_port": local[1],
                    "upstream_address": remote[0],
                    "upstream_port": remote[1],
                },
            )
            receiving = channel.receive("arm", ("cycle", "actor", "connection"))
            control_task = acquire_task(receiving, custody)
            while not pair.closed:
                channel.deadline.remaining()
                # One read/send per role per dispatch; control gets service
                # every iteration even under full forwarding queues.
                if control_task is not None and control_task.done():
                    command = control_task.result()
                    control_task = None
                    cycle(command, (number, actor))
                    require(command["connection"] == number and type(command["connection"]) is int, "connection")
                    pair.arm()
                    await channel.send("armed", binding)
                for key, mask in selector.select(0):
                    require(key.data != "listener", "extra_frontend")
                    pair.dispatch(key.data, mask)
                    if pair.closed:
                        break
                await asyncio.sleep(min(0.01, channel.deadline.remaining()))
            require(control_task is None, "unarmed_settlement")
            await channel.send("settled", {**binding, **pair.settled()}, cleanup=True)
            pair = None
            selector.unregister(listener)
            if number == 1:
                command = await channel.receive("advance", ("cycle", "actor", "connection"))
                cycle(command, (1, "python"))
                require(type(command["connection"]) is int and command["connection"] == 1, "advance_connection")
                await channel.send("advanced", {"cycle": 1, "actor": "python", "connection": 1})
    except BaseException as error:
        original = error
    finally:
        if channel is not None:
            original = await settle_task(control_task, channel.deadline, original)
        for close in (
            (pair.close if pair is not None else None),
            (listener.close if listener is not None else None),
            (selector.close if selector is not None else None),
        ):
            if close is not None:
                try:
                    close()
                except BaseException as error:
                    if original is None:
                        original = error
        if channel is not None:
            try:
                await channel.close()
            except BaseException as error:
                if original is None:
                    original = error
        elif writer is not None:
            original = close_unadmitted_writer(writer, custody, original)
    if original is not None:
        raise original


class ForwardedConstructors:
    """Actual source calls, unchanged arguments/return; no reconstructed constructors."""

    def __init__(self, database, guard, channel):
        self.database = database
        self.guard = guard
        self.channel = channel
        self.installed = []
        self.calls = {}
        self.values = {}
        self.physical = None
        self.environment = None
        self.origins = {}

    def install(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from src.config import get_settings

        self.environment = actor_environment()
        require(
            self.database.get_settings is get_settings
            and self.database.create_async_engine is create_async_engine
            and self.database.async_sessionmaker is async_sessionmaker,
            "constructor_original_alias",
        )
        require(self.database._engine is None and self.database._async_session_factory is None, "preexisting_database")
        for module, name, label in (
            (self.database, "get_settings", "settings"),
            (self.database, "_prepare_asyncpg_url", "prepare"),
            (self.database, "create_async_engine", "engine"),
            (self.database, "async_sessionmaker", "factory"),
            (self.guard, "install_solution_write_guard", "guard"),
        ):
            original = getattr(module, name)
            self.origins[label] = original

            def observe(*args, _original=original, _label=label, **kwargs):
                require(_label not in self.calls, "constructor_repeated")
                value = _original(*args, **kwargs)
                self.calls[_label] = (args, kwargs, value)
                self.values[_label] = value
                if _label == "engine":
                    self.physical = ActorPhysical(value, self.channel)
                    self.physical.install()
                return value

            self.installed.append((module, name, original, observe))
            setattr(module, name, observe)

    def ready(self):
        from sqlalchemy.engine import make_url
        from sqlalchemy.ext.asyncio import AsyncSession

        require(set(self.calls) == {"settings", "prepare", "engine", "factory", "guard"}, "constructor_incomplete")
        settings = self.values["settings"]
        require(
            self.calls["settings"][:2] == ((), {}) and settings.database_url == os.environ["BIFROST_DATABASE_URL"],
            "settings_input",
        )
        require(
            type(settings.debug) is bool
            and settings.debug is False
            and type(settings.database_pool_size) is int
            and settings.database_pool_size == 5
            and type(settings.database_max_overflow) is int
            and settings.database_max_overflow == 10,
            "settings_profile",
        )
        args, kwargs, prepared = self.calls["prepare"]
        require(
            args == (settings.database_url,) and not kwargs and prepared == (settings.database_url, {}), "prepared_url"
        )
        args, kwargs, engine = self.calls["engine"]
        require(
            args == (prepared[0],)
            and kwargs
            == {
                "echo": False,
                "pool_size": 5,
                "max_overflow": 10,
                "pool_pre_ping": True,
                "pool_timeout": 30,
                "pool_recycle": 1800,
                "connect_args": {},
            },
            "engine_arguments",
        )
        require(engine is self.database._engine, "engine_return")
        args, kwargs, factory = self.calls["factory"]
        require(
            len(args) == 1
            and args[0] is engine
            and kwargs == {"class_": AsyncSession, "expire_on_commit": False, "autoflush": False}
            and factory is self.database._async_session_factory,
            "factory_arguments",
        )
        require(self.calls["guard"][:2] == ((), {}), "guard_arguments")
        endpoint = make_url(settings.database_url)
        require(
            endpoint.drivername == "postgresql+asyncpg" and not endpoint.query and endpoint.host is not None,
            "constructor_endpoint",
        )
        require(self.physical is not None and self.physical.connected and self.physical.checked_out, "physical_init")
        return {
            "schema": "bifrost.test.f4-constructor-observation/v1",
            "role": "python",
            "env_count": 28,
            "env_source_equal": actor_environment() == self.environment,
            "source_calls": {name: 1 for name in self.calls},
            "endpoint": {"hostname": endpoint.host, "port": endpoint.port, "drivername": endpoint.drivername},
            "engine": {
                "origin_matches": True,
                "input_matches": True,
                "kwargs_matches": True,
                "returned_matches": True,
                "pool": "production_pool",
            },
            "factory": {
                "origin_matches": True,
                "engine_matches": True,
                "returned_matches": True,
                "expire_on_commit": False,
                "autoflush": False,
            },
            "connection": {
                "record_join": True,
                "selected_connection_join": False,
                "frontend_join": self.physical.parent_connection_accepted,
                "driver_args_observed": True,
                "negotiated_transport_observed": False,
            },
            "restoration": {"pending": True, "failed": False},
        }

    def restore(self):
        original = None
        for module, name, previous, observer in reversed(self.installed):
            try:
                require(getattr(module, name) is observer, "constructor_replaced")
                setattr(module, name, previous)
            except BaseException as error:
                if original is None:
                    original = error
        if original is not None:
            raise original


class ActorPhysical:
    def __init__(self, engine, channel):
        self.engine = engine
        self.channel = channel
        self.preconnect = None
        self.adapter = self.record = None
        self.connected = self.checked_out = self.parent_connection_accepted = False
        self.connection = None
        self.listeners = []
        self.update_acks = []

    def install(self):
        from sqlalchemy import event
        from sqlalchemy.engine import make_url

        url = make_url(os.environ["BIFROST_DATABASE_URL"])
        expected = {
            "host": url.host,
            "port": url.port,
            "user": url.username,
            "password": url.password,
            "database": url.database,
        }

        def preconnect(dialect, record, cargs, cparams):
            require(
                dialect is self.engine.sync_engine.dialect and not cargs and cparams == expected, "physical_preconnect"
            )
            require(self.preconnect is None, "physical_reconnect")
            self.preconnect = record

        def connected(adapter, record):
            require(record is self.preconnect and self.adapter is None, "physical_connect")
            self.adapter, self.record = adapter, record
            self.connected = True

        def checkout(adapter, record, _proxy):
            require(adapter is self.adapter and record is self.record, "physical_checkout")
            if not self.checked_out:
                self.checked_out = True

                async def accept(_driver):
                    await self.channel.send(
                        "connection_ready",
                        {
                            "schema": "bifrost.test.f4-connection-observation/v1",
                            "role": "python",
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
                    require(body["role"] == "python", "connection_role")
                    self.parent_connection_accepted = True

                adapter.run_async(accept)

        def before(connection, _cursor, _statement, _parameters, _context, _many):
            require(connection.connection.dbapi_connection is self.adapter, "physical_sql_connection")
            if self.connection is None:
                self.connection = connection
            else:
                # init's Connection wrapper is legitimately replaced by the
                # consumer, while the actual source physical pair is reused.
                require(connection.engine is self.engine.sync_engine, "physical_engine")

        def after(connection, _cursor, _statement, _parameters, context, _many):
            require(connection.connection.dbapi_connection is self.adapter, "physical_sql_ack")
            compiled = getattr(context, "compiled", None)
            statement = getattr(compiled, "statement", None)
            if getattr(statement, "is_update", False):
                name = getattr(getattr(statement, "table", None), "name", None)
                require(name in ("executions", "workflow_execution_attempts"), "unexpected_update")
                self.update_acks.append(name)

        for target, name, callback in (
            (self.engine.sync_engine, "do_connect", preconnect),
            (self.engine.sync_engine.pool, "connect", connected),
            (self.engine.sync_engine.pool, "checkout", checkout),
            (self.engine.sync_engine, "before_cursor_execute", before),
            (self.engine.sync_engine, "after_cursor_execute", after),
        ):
            require(not event.contains(target, name, callback), "prior_physical_listener")
            self.listeners.append((target, name, callback))
            event.listen(target, name, callback)

    def restore(self):
        from sqlalchemy import event

        original = None
        for target, name, callback in reversed(self.listeners):
            try:
                require(event.contains(target, name, callback), "physical_listener_lost")
                event.remove(target, name, callback)
            except BaseException as error:
                if original is None:
                    original = error
        if original is not None:
            raise original


def actor_environment():
    from tests.parity.workflow_commit_fault import closed

    keys = (
        "BIFROST_DATABASE_URL",
        "BIFROST_DATABASE_URL_SYNC",
        "BIFROST_RABBITMQ_URL",
        "BIFROST_WORK_DELIVERY_BACKEND",
        "BIFROST_REDIS_URL",
        "BIFROST_SECRET_KEY",
        "BIFROST_ENVIRONMENT",
        "BIFROST_ALLOW_REGISTRATION",
        "BIFROST_S3_BUCKET",
        "BIFROST_S3_ENDPOINT_URL",
        "BIFROST_S3_ACCESS_KEY",
        "BIFROST_S3_SECRET_KEY",
        "BIFROST_S3_REGION",
        "TEST_API_URL",
        "TEST_API_REPLICA_URL",
        "PYTHONPATH",
        "BIFROST_RUNTIME_VECTORS",
        "COVERAGE_FILE",
        "GITHUB_TEST_PAT",
        "GITHUB_TEST_REPO",
        "ANTHROPIC_API_TEST_KEY",
        "OPENAPI_API_TEST_KEY",
        "GENERIC_AI_TEST_KEY",
        "GENERIC_AI_BASE_URL",
        "EMBEDDINGS_AI_TEST_KEY",
        "BIFROST_POSTURE_HARDENED",
        "BIFROST_TEMP_LOCATION",
        "BIFROST_VERSION",
    )
    require(all(key in os.environ for key in keys), "environment_missing")
    selected = {key: os.environ[key] for key in keys}
    closed(selected, keys)
    require(
        not any(
            (key.startswith(("PG", "BIFROST_")) and key not in keys) or key in ("SSL_CERT_FILE", "SSL_CERT_DIR")
            for key in os.environ
        ),
        "environment_extra",
    )
    for key in (
        "GITHUB_TEST_PAT",
        "ANTHROPIC_API_TEST_KEY",
        "OPENAPI_API_TEST_KEY",
        "GENERIC_AI_TEST_KEY",
        "GENERIC_AI_BASE_URL",
        "EMBEDDINGS_AI_TEST_KEY",
    ):
        require(selected[key] == "", "vendor_environment")
    require(
        selected["BIFROST_ENVIRONMENT"] == "testing"
        and selected["BIFROST_TEMP_LOCATION"] == "/tmp/bifrost/temp"
        and selected["BIFROST_VERSION"] == "unknown"
        and selected["PYTHONPATH"] == "/app"
        and selected["BIFROST_WORK_DELIVERY_BACKEND"] in ("rabbitmq", "postgres"),
        "environment_profile",
    )
    require(
        all("\0" not in value and "\r" not in value and "\n" not in value for value in selected.values()),
        "environment_encoding",
    )
    return selected


def qualify_sources():
    from importlib import metadata

    from tests.parity.workflow_commit_fault import SOURCE_FILES, source_file

    require(metadata.version("SQLAlchemy") == "2.0.49" and metadata.version("asyncpg") == "0.31.0", "package_version")
    for path, digest in SOURCE_FILES.items():
        source_file(path, digest)
    source_file(
        "/app/src/services/solutions/guard.py", "622668d183e804cef5f435ca46567d4e12cf16b82893996a4dccc670adef4f85"
    )
    package_files = {
        "sqlalchemy": {
            "dialects/postgresql/asyncpg.py": "bcd9da5dd314d4e6e43ea16cb566544d5ebf58e5a9f38593a6924546e9e4c5c5",
            "engine/base.py": "6eaf5c64fb87d30670ff9a08e1a1c04afc632f20cf9d95bac89b223750cd5310",
            "exc.py": "d828a98df9474ddb668ba1be4db6c0e23f78b095f7f51f84826ceb407c96e5bb",
            "ext/asyncio/session.py": "da7665e41e6687a302c72375b8258683ff8677d8f8318e6e876b738812b4ec73",
        },
        "asyncpg": {
            "transaction.py": "b8026893a4a1c7bf8a76de65e0d5fe189b4bc552523d74ce254af219d6c8546f",
            "exceptions/__init__.py": "1575180c5430f60c44de6573f7d166b25758c62bcb50cb532215f3bb9b59ecf9",
        },
    }
    for package, members in package_files.items():
        distribution = metadata.distribution("SQLAlchemy" if package == "sqlalchemy" else "asyncpg")
        for member, digest in members.items():
            source_file(str(distribution.locate_file(package + "/" + member)), digest)
    # The archive's protocol.pyx member is source provenance, not an installed
    # wheel member or compiled-extension attestation. Never demand an invented
    # runtime source mount; compiled-driver/image custody remains the parent's
    # independent gate. Six installed Python members are qualified here.


async def python_actor(value, channel):
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    from src.core import database, redis_client
    from src.jobs.consumers import workflow_execution as consumer_module
    from src.services.execution import process_pool as pool_module
    from src.services.solutions import guard

    from tests.parity.workflow_commit_fault import (
        SdkTrace,
        SessionJoins,
        assigned,
        classify_python,
        cleanup_failure,
        driver_snapshot,
        loaded_sdk_source,
    )
    from tests.parity.workflow_domain_harness import dormant, unstarted_consumer
    from tests.parity.workflow_sql_harness import materialize

    environment = actor_environment()
    qualify_sources()
    constructors = ForwardedConstructors(database, guard, channel)
    pool = consumer = previous_callback = trace = joins = None
    previous_pool = pool_module._pool
    previous_redis = redis_client._redis_client
    owned_redis = None
    listeners = []
    original = None
    commit_error = None
    captured = {
        "connection": None,
        "original": None,
        "wrapper": None,
        "count": 0,
        "hook_returned": False,
        "failed": False,
    }
    selected = None
    commit = "not_dispatched"
    pool_closed = False
    try:
        constructors.install()
        await database.init_db()
        database.get_session_factory()
        require(len(environment) == 28, "environment_count")
        await channel.send("constructor_ready", constructors.ready())
        acceptance = await channel.receive("constructor_accept", ("role",))
        require(acceptance["role"] == "python", "constructor_role")
        pool = pool_module.get_process_pool()
        previous_callback = pool.on_result
        dormant(pool)
        consumer = consumer_module.WorkflowExecutionConsumer()
        if previous_redis is None:
            owned_redis = redis_client._redis_client
        unstarted_consumer(consumer, pool)
        request = value["request"]
        owned = request["cohort"]["execution_id"]
        scope = value["scope"]
        payload = materialize(request["operation"]["raw_fields"], "success")
        require(payload == {}, "materialized_absence")
        payload.update(sync=False, execution_id=owned, attempt_token=request["cohort"]["submitted_token"])
        trace = SdkTrace(consumer._process_success.__func__.__code__, owned, loaded_sdk_source)
        joins = SessionJoins(database.get_engine().sync_engine, event, Session)
        joins.install()
        trace.install()

        def commit_hook(connection):
            nonlocal selected, commit
            captured["count"] += 1
            if captured["count"] != 1:
                captured["failed"] = True
                return
            captured["connection"] = connection
            require(connection.connection.dbapi_connection is constructors.physical.adapter, "commit_pair")
            require(
                constructors.physical.update_acks == ["workflow_execution_attempts", "executions"]
                or constructors.physical.update_acks == ["executions", "workflow_execution_attempts"],
                "update_ack",
            )
            witness = trace.witness(joins.join(trace.session, connection))

            async def capture(driver):
                nonlocal selected, commit
                rows = await driver_snapshot(driver, owned, scope["foreign_execution_id"])
                selected = await assigned(driver)
                binding = {"cycle": 1, "actor": "python", "connection": 1}
                await channel.send(
                    "snapshot", {"cycle": 1, "actor": "python", "phase": "post_flush", "rows": rows}, data=True
                )
                await channel.send(
                    "selected_ready",
                    {
                        **binding,
                        "xid": selected["xid"],
                        "backend_pid": selected["backend_pid"],
                        "query_witness": selected,
                        "source_witness": witness,
                    },
                )
                release = await channel.receive("release_commit", ("cycle", "actor", "connection"))
                cycle(release, (1, "python"))
                require(type(release["connection"]) is int and release["connection"] == 1, "release_connection")
                await channel.send("listener_returned", binding)
                captured["hook_returned"] = True

            connection.connection.dbapi_connection.run_async(capture)

        def handle_error(context):
            if captured["hook_returned"] and context.connection is captured["connection"]:
                if captured["original"] is not None:
                    captured["failed"] = True
                else:
                    captured["original"] = context.original_exception
                    captured["wrapper"] = context.sqlalchemy_exception
            return

        engine = database.get_engine().sync_engine
        for name, callback in (("commit", commit_hook), ("handle_error", handle_error)):
            listeners.append((engine, name, callback))
            event.listen(engine, name, callback)
        try:
            await consumer._process_success(owned, payload)
            commit = "acknowledged" if captured["hook_returned"] else "not_dispatched"
        except BaseException as error:
            commit_error = error
            commit = "error" if captured["hook_returned"] else "not_dispatched"
            original = error
        # Normal ACK remains genuine and is non-target; no synthetic error.
    except BaseException as error:
        if original is None:
            original = error
    finally:
        for target, name, callback in reversed(listeners):
            try:
                require(event.contains(target, name, callback), "commit_listener_lost")
                event.remove(target, name, callback)
            except BaseException as error:
                original = cleanup_failure(captured, original, error)
        for restore in (
            (trace.restore if trace is not None else None),
            (joins.restore if joins is not None else None),
            constructors.restore,
            (constructors.physical.restore if constructors.physical is not None else None),
        ):
            if restore is not None:
                try:
                    restore()
                except BaseException as error:
                    original = cleanup_failure(captured, original, error)
        if pool is not None:
            try:
                dormant(pool)
                unstarted_consumer(consumer, pool)
                require(pool_module._pool is pool and pool.on_result == consumer._handle_result, "pool_replaced")
                pool.on_result = previous_callback
                if previous_pool is None:
                    pool_module._pool = None
            except BaseException as error:
                original = cleanup_failure(captured, original, error)
        database_closed = False
        owned_engine = constructors.values.get("engine")

        async def close_owned_database():
            nonlocal database_closed
            if owned_engine is None:
                require(database._engine is None, "database_unowned")
                return
            if database._engine is owned_engine:
                await database.close_db()
                database_closed = True
            else:
                require(database._engine is None, "database_replaced")
                # Constructor returned this owned engine before a later install
                # failed, so the source global may never have received it.
                await owned_engine.dispose()
                database_closed = True

        async def close_owned_redis():
            current = redis_client._redis_client
            if previous_redis is not None:
                require(current is previous_redis, "redis_replaced")
            elif owned_redis is None:
                require(current is None, "redis_unowned")
            else:
                require(current is owned_redis, "redis_replaced")
                await redis_client.close_redis_client()
                require(redis_client._redis_client is None, "redis_close")

        for close in (close_owned_database, close_owned_redis):
            try:
                async with asyncio.timeout_at(channel.deadline.total_end):
                    await close()
            except BaseException as error:
                original = cleanup_failure(captured, original, error)
        pool_closed = database_closed and database._engine is None and database._async_session_factory is None
        if trace is not None:
            trace.release_references()
        if joins is not None:
            joins.release_references()
        try:
            error_label = (
                classify_python(commit_error, captured["original"], captured["wrapper"]) if commit_error else None
            )
            require(not captured["failed"], "observation_failed")
            await channel.send(
                "actor_finished",
                {
                    "cycle": 1,
                    "actor": "python",
                    "connection": 1,
                    "commit": commit,
                    "error": error_label,
                    "pool_closed": pool_closed,
                },
                cleanup=True,
            )
        except BaseException as error:
            if original is None:
                original = error
    if original is not None:
        raise original


async def fault_stdin(deadline):
    descriptor = sys.stdin.fileno()
    identity = os.fstat(descriptor)
    require(stat.S_ISFIFO(identity.st_mode) or stat.S_ISSOCK(identity.st_mode), "stdin_profile")
    os.set_blocking(descriptor, False)
    result = bytearray()
    while True:
        deadline.remaining()
        try:
            chunk = os.read(descriptor, 65537 - len(result))
        except BlockingIOError:
            await asyncio.sleep(min(0.01, deadline.remaining()))
            continue
        if not chunk:
            return bytes(result)
        result.extend(chunk)
        require(len(result) <= 65536, "input_bound")


async def python_main():
    # Thirty seconds is a conservative local upper bound beginning after
    # startup. The parent's earlier before-create absolute actor/case ends
    # remain authoritative; this does not renew the host allocation.
    loop = asyncio.get_running_loop()
    end = loop.time() + 30
    deadline = Deadline(end, end)
    reader = writer = channel = None
    original = None
    try:
        raw = await fault_stdin(deadline)
        value = fault_input(decode(raw), "python")
        async with asyncio.timeout(deadline.remaining()):
            reader, writer = await connect_control("/run/f4-control.sock", 0o600)
        channel = Channel(reader, writer, value["invocation"], "python", "python", IPC_SCHEMA, deadline)
        await channel.send("hello", {})
        await channel.receive("hello_accept", ())
        async with asyncio.timeout(deadline.remaining()):
            await python_actor(value, channel)
    except BaseException as error:
        original = error
    finally:
        if channel is not None:
            try:
                await channel.close()
            except BaseException as error:
                if original is None:
                    original = error
        elif writer is not None:
            try:
                writer.close()
            except BaseException as error:
                if original is None:
                    original = error
    if original is not None:
        raise original


def main():
    # No raw error text, credentials, control/snapshot frames or recovery
    # channel goes to stdout/stderr. Genuine errors remain objects until the
    # outer process converts them to a nonzero status.
    if sys.argv[1:] not in (["relay"], ["python"]):
        return 2
    try:
        asyncio.run(relay() if sys.argv[1] == "relay" else python_main())
    except KeyboardInterrupt:
        return 130
    except SystemExit as error:
        return error.code if type(error.code) is int and error.code != 0 else 1
    except BaseException:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
