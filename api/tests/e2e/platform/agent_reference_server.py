"""Fixed test-only Uvicorn 0.46 seam: upstream shutdown, then observer task grant."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import os
import sys
from pathlib import Path

import uvicorn
from uvicorn import config as uvicorn_config
from uvicorn import server as uvicorn_server
from uvicorn.lifespan import on as lifespan_on

from scripts import agent_reference_contract as wire
from tests.e2e.platform import agent_reference_observer as observation

# Independently verified locked public uvicorn-0.46.0-py3-none-any.whl:
# SHA256 bbebbcbed972d162afca128605223022bedd345b7bc7855ce66deb31487a9048.
# These exact wheel members also agree with the local cache's RECORD.
# The root still proves actual image/interpreter/command/config custody in the lane.
PINNED_SOURCES = (
    (
        uvicorn_server,
        "7d7937fff1c17f583ea21ff8fce5c8a5bf2d9d3c9a7f1b9646211eb7a49012f3",
    ),
    (
        uvicorn_config,
        "4a8f11e66a16dda519aa6dc7557683f6e178d3d8602beca80811a5564b2a56cb",
    ),
    (lifespan_on, "8844cf163e921613c33dc75e1b17dae372ca226254ecc2398bb41451ea4170f1"),
)


def require_source_pin() -> None:
    try:
        if importlib.metadata.version("uvicorn") != "0.46.0":
            raise observation.ObservationClosureError()
        for module, expected in PINNED_SOURCES:
            if (
                hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                != expected
            ):
                raise observation.ObservationClosureError()
    except (
        OSError,
        TypeError,
        AttributeError,
        importlib.metadata.PackageNotFoundError,
    ):
        raise observation.ObservationClosureError() from None


class Factory:
    """Exactly one actual app, independently retained through proxy middleware."""

    def __init__(self) -> None:
        self.observer: observation.Observer | None = None
        self._called = False

    def __call__(self) -> observation.Observer:
        if self._called:
            raise observation.ObservationClosureError()
        self._called = True
        self.observer = observation.create_app()
        return self.observer


class ReferenceServer(uvicorn.Server):
    """Only shutdown is overridden; listeners/ACKs/signals/run loop are upstream."""

    async def shutdown(self, sockets=None) -> None:
        await super().shutdown(sockets=sockets)
        factory = self.config.app
        observer = factory.observer if isinstance(factory, Factory) else None
        task = observer.lifespan_task if observer is not None else None
        loop = asyncio.get_running_loop()
        try:
            if (
                observer is None
                or task is None
                or task is asyncio.current_task()
                or task.get_loop() is not loop
                or observer.loop is not loop
                or task.done()
                or self.force_exit
                or not all(observer.upstream.values())
                or type(self.lifespan) is not lifespan_on.LifespanOn
            ):
                raise observation.ObservationClosureError()
            deadline = loop.time() + wire.OBSERVER_CLOSE_SECONDS
            observer.grant_closure(deadline)
            _, pending = await asyncio.wait(
                {task}, timeout=max(0, deadline - loop.time())
            )
            if pending or task.cancelled() or task.exception() is not None:
                raise observation.ObservationClosureError()
            # LifespanOn.main catches BaseException. A completed task alone is no proof.
            if (
                self.lifespan.error_occurred is not False
                or self.lifespan.startup_failed is not False
                or self.lifespan.shutdown_failed is not False
                or self.force_exit
                or not observer.closed
                or not observer.closed_acked
                or observer.first_failure is not None
                or observer.phase != "closed"
                or not all(observer.upstream.values())
            ):
                raise observation.ObservationClosureError()
        except asyncio.CancelledError:
            if observer is not None:
                observer.latch("shutdown_incomplete")
                observer.publish()
            if task is not None and not task.done():
                task.cancel()
            raise
        except (observation.ObservationClosureError, AttributeError):
            if observer is not None:
                observer.latch("shutdown_incomplete")
                observer.publish()
            if task is not None and not task.done():
                task.cancel()
            raise observation.ObservationClosureError() from None


def fixed_config(argv: list[str] | None = None) -> uvicorn.Config:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--host", choices=("0.0.0.0",), required=True)
    parser.add_argument("--port", choices=("8000",), required=True)
    args = parser.parse_args(argv)
    if any(name.startswith("UVICORN_") for name in os.environ) or os.environ.get(
        "WEB_CONCURRENCY"
    ) not in (None, "1"):
        raise observation.ObservationClosureError()
    require_source_pin()
    # All other defaults, including FORWARDED_ALLOW_IPS and auto loop, are upstream.
    return uvicorn.Config(Factory(), host=args.host, port=int(args.port), factory=True)


def main(argv: list[str] | None = None) -> int:
    try:
        server = ReferenceServer(fixed_config(argv))
        server.run()
        if not server.started:
            return 3  # Same startup-failure disposition as stock Uvicorn CLI.
        return 0
    except observation.ObservationClosureError:
        print("agent-reference observation failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
