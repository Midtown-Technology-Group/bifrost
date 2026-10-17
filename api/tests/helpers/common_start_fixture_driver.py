"""Explicit driver for separately held Start SQL fixtures; never creates authority.

Native30/0 histories precede C1-C5. Common phase requires independently approved
installed schema/principals. No DDL, roles/grants, default DSN, runtime or retry.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.helpers.common_cancel_fixture import FixturePreparationError
from tests.helpers.common_cancel_fixture_driver import _url
from tests.helpers.common_start_fixture import (
    seed_prepared_pair,
    seed_start_native_history,
)


async def _run(arguments: argparse.Namespace) -> None:
    native_phase = arguments.phase == "native"
    if native_phase:
        if (
            arguments.timeout_seconds is None
            or arguments.finite_seed
            or arguments.no_timeout_seed
        ):
            raise FixturePreparationError(
                "native Start stage requires only its timeout"
            )
    elif (
        arguments.timeout_seconds is not None
        or not arguments.finite_seed
        or not arguments.no_timeout_seed
    ):
        raise FixturePreparationError(
            "common Start stage requires both native histories"
        )
    async with AsyncExitStack() as cleanup:

        def factory(variable: str, principal: str):
            engine = create_async_engine(
                _url(variable, principal),
                poolclass=NullPool,
                isolation_level="READ COMMITTED",
                echo=False,
                hide_parameters=True,
            )
            cleanup.push_async_callback(engine.dispose)
            return async_sessionmaker(engine, expire_on_commit=False)

        native = factory(
            "BIFROST_COMMON_NATIVE_TEST_DATABASE_URL", arguments.native_principal
        )
        if native_phase:
            await seed_start_native_history(
                native,
                expected_principal=arguments.native_principal,
                destination=arguments.destination,
                timeout_seconds=arguments.timeout_seconds,
            )
        else:
            core = factory("BIFROST_COMMON_CORE_TEST_DATABASE_URL", "wex_core")
            await seed_prepared_pair(
                native,
                core,
                expected_native_principal=arguments.native_principal,
                finite_seed=arguments.finite_seed,
                no_timeout_seed=arguments.no_timeout_seed,
                destination=arguments.destination,
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("native", "common"))
    parser.add_argument("--native-principal", required=True)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--timeout-seconds", choices=(0, 30), type=int)
    parser.add_argument("--finite-seed", type=Path)
    parser.add_argument("--no-timeout-seed", type=Path)
    arguments = parser.parse_args()
    try:
        asyncio.run(_run(arguments))
    except BaseException:
        # Includes disposal/control failures: never expose URL/claim/SQL details.
        print("Start fixture failed; preserve private outputs/database; no retry")
        return 1
    print("Start fixture prepared; no Rust Start or runtime acceptance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
