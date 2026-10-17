"""Disposable databases on the test Compose network; no configurable remote URL."""

from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


@asynccontextmanager
async def disposable_postgres():
    # Fixed Compose hostname/credentials: never derive an admin URL from app or
    # production configuration. UUID identifiers are generated here, not callers.
    name = f"author_{uuid4().hex}"
    admin = create_async_engine(
        "postgresql+asyncpg://bifrost:bifrost_test@postgres:5432/postgres",
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    engine = create_async_engine(
        f"postgresql+asyncpg://bifrost:bifrost_test@postgres:5432/{name}",
        poolclass=NullPool,
    )
    created = False
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
            created = True
        yield engine
    finally:
        await engine.dispose()
        try:
            if created:
                async with admin.connect() as connection:
                    await connection.execute(
                        text(f'DROP DATABASE "{name}" WITH (FORCE)')
                    )
        finally:
            await admin.dispose()


async def wait_for_blocked(connection, *, waiter: int, blocker: int) -> None:
    """Observe a real PostgreSQL wait edge before releasing a transaction."""
    import asyncio

    async with asyncio.timeout(2):
        while not await connection.scalar(
            text("SELECT :blocker = ANY(pg_blocking_pids(:waiter))"),
            {"blocker": blocker, "waiter": waiter},
        ):
            # Each SQL round trip yields to the transactions under observation.
            # No sleep is used to guess when the lock has been acquired.
            pass
