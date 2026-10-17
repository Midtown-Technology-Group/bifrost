"""Explicit supported-container entrypoint for the held SQL-only fixture.

No DDL, role/grant/login creation, runtime, default DSN or automatic retry.
Native stage precedes C1-C5; common stage requires their approved installation
and independent effective-catalog qualification. Run neither on the host.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from contextlib import AsyncExitStack
from pathlib import Path

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.helpers.common_cancel_fixture import (
    FixturePreparationError,
    seed_common_running,
    seed_native_history,
)


def _url(variable: str, principal: str) -> URL:
    try:
        url = make_url(os.environ[variable])
        if (
            url.drivername not in {"postgresql", "postgresql+asyncpg"}
            or url.database != "bifrost_test"
            or url.username != principal
            or not url.host
            or url.password is None
        ):
            raise ValueError
        return url.set(drivername="postgresql+asyncpg")
    except Exception:
        raise FixturePreparationError(
            "explicit fixture connection is invalid"
        ) from None


async def _run(arguments: argparse.Namespace) -> None:
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
        if arguments.phase == "native":
            if arguments.native_seed is not None:
                raise FixturePreparationError("native stage accepts no prior seed")
            await seed_native_history(
                native,
                expected_principal=arguments.native_principal,
                destination=arguments.destination,
            )
        else:
            if arguments.native_seed is None:
                raise FixturePreparationError("common stage requires its native seed")
            core = factory("BIFROST_COMMON_CORE_TEST_DATABASE_URL", "wex_core")
            await seed_common_running(
                native,
                core,
                expected_native_principal=arguments.native_principal,
                native_seed=arguments.native_seed,
                destination=arguments.destination,
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("native", "common"))
    parser.add_argument("--native-principal", required=True)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--native-seed", type=Path)
    arguments = parser.parse_args()
    try:
        asyncio.run(_run(arguments))
    except Exception:
        # Never print exception/URL/claim/SQL details, including disposal errors.
        print("fixture preparation failed; preserve private outputs/database; no retry")
        return 1
    print("fixture prepared; independent catalog/readback still required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
