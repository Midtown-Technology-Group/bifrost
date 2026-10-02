"""Pinned upstream lifecycle with synthetic apps/peers; instrumentation proof only."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
import uvicorn
from uvicorn.lifespan.on import LifespanOn

from scripts import agent_reference_contract as wire
from tests.e2e.platform import agent_reference_observer as obs
from tests.e2e.platform import agent_reference_server as shim
from tests.unit.test_agent_reference_observer import observer, spin


class Harness:
    def __init__(self, monkeypatch):
        self.messages = []
        self.scopes = []
        self.received = []
        self.startup = {"type": "lifespan.startup.complete"}
        self.shutdown = {"type": "lifespan.shutdown.complete"}
        self.order = []

        async def app(scope, receive, send):
            self.scopes.append(scope)
            self.received.append(await receive())
            await send(self.startup)
            self.order.append("app-startup-returned")
            self.received.append(await receive())
            await send(self.shutdown)
            self.order.append("app-shutdown-returned")

        self.owner = observer(app)
        monkeypatch.setattr(obs, "create_app", lambda: self.owner)
        self.factory = shim.Factory()
        self.config = uvicorn.Config(
            self.factory, host="0.0.0.0", port=8000, factory=True
        )
        self.server = shim.ReferenceServer(self.config)
        self.server.servers = []
        self.server.lifespan = LifespanOn(self.config)
        upstream_send = self.server.lifespan.send

        async def send(message):
            await upstream_send(message)
            self.messages.append(message)
            self.order.append("forwarded-" + message["type"])

        self.server.lifespan.send = send

    async def start(self):
        await self.server.lifespan.startup()
        await spin(lambda: self.owner.ready_acked)
        assert self.owner.lifespan_task is not None
        assert self.owner.lifespan_task is not asyncio.current_task()
        assert self.messages[0] is self.startup
        assert self.factory.observer is self.owner

    async def dispose(self):
        tasks = tuple(
            task
            for task in (
                self.owner.lifespan_task,
                self.owner._sender_task,
                self.owner._writer_task,
            )
            if task is not None
        )
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_stock_shutdown_returns_before_post_ack_observer_closure(monkeypatch):
    harness = Harness(monkeypatch)
    try:
        await harness.start()
        await uvicorn.Server.shutdown(harness.server)
        assert harness.messages[-1] is harness.shutdown
        assert harness.owner.upstream["shutdown_forwarded"]
        assert not harness.owner._grant.is_set()
        assert not harness.owner.closed
        assert not harness.owner.lifespan_task.done()
        assert [receipt["kind"] for receipt in harness.owner.sender.receipts] == [
            "ready"
        ]
    finally:
        await harness.dispose()


async def test_shim_waits_actual_task_after_unchanged_ack_then_exact_status(
    monkeypatch,
):
    harness = Harness(monkeypatch)
    try:
        await harness.start()
        harness.owner.sender.block = asyncio.Event()
        shutdown = asyncio.create_task(harness.server.shutdown())
        # Actual upstream shutdown takes its pinned 100ms wait before lifespan.
        async with asyncio.timeout(1):
            while not harness.owner._grant.is_set():
                await asyncio.sleep(0.001)
        assert harness.messages[-1] is harness.shutdown
        assert harness.order.index(
            "forwarded-lifespan.shutdown.complete"
        ) < harness.order.index("app-shutdown-returned")
        assert not shutdown.done() and not harness.owner.lifespan_task.done()
        await spin(lambda: harness.owner.counters["sending"] == 1)
        deadline = harness.owner.deadline
        assert deadline is not None
        harness.owner.sender.block.set()
        await shutdown
        assert harness.owner.closed and harness.owner.lifespan_task.done()
        assert harness.owner.sender.deadlines == [None, deadline]
        assert all(
            task.done()
            for task in (harness.owner._sender_task, harness.owner._writer_task)
        )
        status = wire.decode_private("status", harness.owner.store.raw)
        assert (
            status["phase"] == "closed"
            and status["generation"] == harness.owner.generation
        )
        assert status["closed_acked"] and status["ready_acked"]
        assert status["first_failure"] is None
        assert (
            status["counters"]["receipts_seen"]
            == status["counters"]["acknowledged"]
            == 2
        )
        closed = harness.owner.sender.receipts[-1]
        assert (
            closed["kind"] == "closed"
            and closed["payload"]["prior"]["receipts_seen"] == 1
        )
        assert harness.owner.store.closed
    finally:
        await harness.dispose()


async def test_blocked_ready_sender_never_delays_actual_startup_or_shutdown_ack(
    monkeypatch,
):
    harness = Harness(monkeypatch)
    harness.owner.sender.block = asyncio.Event()
    try:
        await harness.server.lifespan.startup()
        assert harness.messages[0] is harness.startup
        assert harness.owner.upstream["startup_forwarded"]
        assert not harness.owner.ready_acked
        await uvicorn.Server.shutdown(harness.server)
        assert harness.messages[-1] is harness.shutdown
        assert harness.owner.upstream["shutdown_forwarded"]
        assert not harness.owner.closed_acked
    finally:
        await harness.dispose()


@pytest.mark.parametrize(
    "defect",
    ["missing-task", "done-task", "wrong-loop", "force-exit", "wrong-lifespan"],
)
async def test_task_loop_force_exit_and_lifespan_mismatch_fail_closed(
    monkeypatch, defect
):
    harness = Harness(monkeypatch)
    try:
        await harness.start()
        # Let real upstream shutdown finish first; no modified ACK semantics.
        await uvicorn.Server.shutdown(harness.server)
        original_task = harness.owner.lifespan_task
        if defect == "missing-task":
            harness.owner.lifespan_task = None
        elif defect == "done-task":
            original_task.cancel()
            # LifespanOn catches app cancellation; flags remain actual.
            await original_task
        elif defect == "wrong-loop":
            harness.owner.loop = object()
        elif defect == "force-exit":
            harness.server.force_exit = True
        else:
            harness.server.lifespan = object()

        async def already_completed_upstream(self, sockets=None):
            pass

        monkeypatch.setattr(uvicorn.Server, "shutdown", already_completed_upstream)
        with pytest.raises(
            obs.ObservationClosureError, match="^agent-reference observation failed$"
        ):
            await harness.server.shutdown()
        assert not harness.owner.closed
        assert not harness.owner._grant.is_set()
        harness.owner.lifespan_task = original_task
    finally:
        await harness.dispose()


@pytest.mark.parametrize(
    "attribute, value",
    [
        ("error_occurred", True),
        ("startup_failed", 0),
        ("shutdown_failed", None),
        ("shutdown_failed", "missing"),
    ],
)
async def test_completed_task_cannot_replace_actual_lifespan_flags(
    monkeypatch, attribute, value
):
    harness = Harness(monkeypatch)
    try:
        await harness.start()
        original_close = harness.owner._close

        async def close_and_drift_flag():
            await original_close()
            if value == "missing":
                delattr(harness.server.lifespan, attribute)
            else:
                setattr(harness.server.lifespan, attribute, value)

        harness.owner._close = close_and_drift_flag
        with pytest.raises(obs.ObservationClosureError):
            await harness.server.shutdown()
        assert harness.owner.lifespan_task.done()
        assert harness.owner.first_failure == "shutdown_incomplete"
    finally:
        await harness.dispose()


@pytest.mark.parametrize(
    "defect", ["lost-ack", "bad-ack", "status-io", "status-readback"]
)
async def test_receipt_or_exact_persisted_status_failure_prevents_server_success(
    monkeypatch, defect
):
    harness = Harness(monkeypatch)
    try:
        await harness.start()
        if defect == "lost-ack":

            async def lost(raw, deadline):
                raise TimeoutError("unit-only-fault")

            harness.owner.sender.send = lost
        elif defect == "bad-ack":
            harness.owner.sender.mutate = lambda ack: {**ack, "nonce": "f" * 32}
        elif defect == "status-io":
            harness.owner.store.fail = True
        else:
            harness.owner.store.corrupt = True
        with pytest.raises(obs.ObservationClosureError):
            await harness.server.shutdown()
        assert not harness.owner.closed
        assert harness.owner.first_failure is not None
        assert harness.server.lifespan.error_occurred is True
    finally:
        await harness.dispose()


async def test_single_absolute_deadline_cancels_inflight_preserves_unsent_and_no_resend(
    monkeypatch,
):
    harness = Harness(monkeypatch)
    try:
        await harness.start()
        harness.owner.sender.block = asyncio.Event()
        harness.owner.enqueue(
            "failure", {"phase": "request", "code": "observer_exception"}
        )
        harness.owner.enqueue(
            "failure", {"phase": "request", "code": "observer_exception"}
        )
        await spin(lambda: harness.owner.counters["sending"] == 1)
        # A shorter synthetic budget characterizes expiry without extending the real limit.
        monkeypatch.setattr(wire, "OBSERVER_CLOSE_SECONDS", 0.02)
        loop = asyncio.get_running_loop()
        before = loop.time()
        with pytest.raises(obs.ObservationClosureError):
            await harness.server.shutdown()
        assert harness.owner.deadline is not None
        assert harness.owner.deadline - before < 0.5
        await spin(lambda: harness.owner.counters["send_failed"] == 1)
        assert harness.owner.counters["queue_pending"] == 1
        assert harness.owner.counters["sending"] == 0
        assert (
            len(harness.owner.sender.receipts) == 2
        )  # Ready plus actual failed send, no resend.
        assert not harness.owner.closed
        wire.validate_private("status", harness.owner.snapshot())
    finally:
        await harness.dispose()


async def test_closure_grant_is_single_use(monkeypatch):
    harness = Harness(monkeypatch)
    try:
        await harness.start()
        await uvicorn.Server.shutdown(harness.server)
        deadline = asyncio.get_running_loop().time() + wire.OBSERVER_CLOSE_SECONDS
        harness.owner.grant_closure(deadline)
        with pytest.raises(obs.ObservationClosureError):
            harness.owner.grant_closure(deadline + 1)
        await harness.owner.lifespan_task
        assert not harness.owner.closed
        assert harness.owner.first_failure == "shutdown_incomplete"
        assert harness.server.lifespan.error_occurred is True
    finally:
        await harness.dispose()


def test_exactly_once_factory_preserves_product_exception_identity(monkeypatch):
    error = RuntimeError("product-original")

    def fail():
        raise error

    monkeypatch.setattr(obs, "create_app", fail)
    factory = shim.Factory()
    with pytest.raises(RuntimeError) as caught:
        factory()
    assert caught.value is error
    with pytest.raises(obs.ObservationClosureError):
        factory()


def test_server_overrides_only_shutdown_and_inherits_actual_run():
    assert {
        name
        for name, value in shim.ReferenceServer.__dict__.items()
        if callable(value) and not name.startswith("__")
    } == {"shutdown"}
    assert shim.ReferenceServer.run is uvicorn.Server.run
    assert shim.ReferenceServer.startup is uvicorn.Server.startup


def clean_config_environment(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("UVICORN_") or name == "WEB_CONCURRENCY":
            monkeypatch.delenv(name)


def test_fixed_config_preserves_all_upstream_defaults_and_forwarded_allow_ips(
    monkeypatch,
):
    clean_config_environment(monkeypatch)
    monkeypatch.setattr(shim, "require_source_pin", lambda: None)
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "127.0.0.1")
    config = shim.fixed_config(["--host", "0.0.0.0", "--port", "8000"])
    upstream = uvicorn.Config("unit-unused", host="0.0.0.0", port=8000, factory=True)
    assert isinstance(config.app, shim.Factory) and not config.loaded
    # Compare every constructed attribute except the intentionally fixed factory.
    assert {key: value for key, value in vars(config).items() if key != "app"} == {
        key: value for key, value in vars(upstream).items() if key != "app"
    }


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--host", "127.0.0.1", "--port", "8000"],
        ["--host", "0.0.0.0", "--port", "8001"],
        ["--h", "0.0.0.0", "--port", "8000"],
        ["--host", "0.0.0.0", "--port", "8000", "--reload"],
        ["--host", "0.0.0.0", "--port", "8000", "arbitrary:app"],
    ],
)
def test_command_has_no_app_or_protocol_selector(args):
    with pytest.raises(SystemExit):
        shim.fixed_config(args)


@pytest.mark.parametrize(
    "name, value",
    [("UVICORN_HOST", "0.0.0.0"), ("UVICORN_ANYTHING", ""), ("WEB_CONCURRENCY", "2")],
)
def test_unsupported_cli_supervisor_environment_rejected_before_factory(
    monkeypatch, name, value
):
    clean_config_environment(monkeypatch)
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        shim, "require_source_pin", lambda: pytest.fail("premature source/app setup")
    )
    with pytest.raises(obs.ObservationClosureError):
        shim.fixed_config(["--host", "0.0.0.0", "--port", "8000"])


def test_independently_pinned_installed_uvicorn_sources():
    # Executes only in supported lane; local static review never imported package.
    shim.require_source_pin()


def test_version_and_source_drift_have_no_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(shim.importlib.metadata, "version", lambda _: "0.46.1")
    with pytest.raises(obs.ObservationClosureError):
        shim.require_source_pin()
    monkeypatch.setattr(shim.importlib.metadata, "version", lambda _: "0.46.0")
    source = tmp_path / "server.py"
    source.write_bytes(b"# source drift\n")
    monkeypatch.setattr(shim.uvicorn_server, "__file__", str(source))
    with pytest.raises(obs.ObservationClosureError):
        shim.require_source_pin()


def test_pin_contains_only_exact_locked_source_members():
    assert {Path(module.__file__).name for module, _ in shim.PINNED_SOURCES} == {
        "server.py",
        "config.py",
        "on.py",
    }


def test_entrypoint_catches_only_static_observation_failure(monkeypatch, capsys):
    class StaticFailure:
        def __init__(self, config):
            pass

        def run(self):
            raise obs.ObservationClosureError()

    monkeypatch.setattr(shim, "fixed_config", lambda _: None)
    monkeypatch.setattr(shim, "ReferenceServer", StaticFailure)
    assert shim.main([]) == 1
    assert capsys.readouterr().err == "agent-reference observation failed\n"
    error = RuntimeError("product-original")

    def product_failure(self):
        raise error

    monkeypatch.setattr(StaticFailure, "run", product_failure)
    with pytest.raises(RuntimeError) as caught:
        shim.main([])
    assert caught.value is error


@pytest.mark.parametrize("started, code", [(False, 3), (True, 0)])
def test_entrypoint_retains_upstream_startup_disposition(monkeypatch, started, code):
    class ReturnedServer:
        def __init__(self, config):
            self.started = started

        def run(self):
            pass

    monkeypatch.setattr(shim, "fixed_config", lambda _: None)
    monkeypatch.setattr(shim, "ReferenceServer", ReturnedServer)
    assert shim.main([]) == code
