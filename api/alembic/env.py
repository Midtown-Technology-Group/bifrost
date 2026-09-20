"""
Alembic Migration Environment

This file configures Alembic for database migrations.
It uses the SQLAlchemy models and settings from the application.
"""

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from src.config import get_settings
from src.core.database import Base

# Import all models so they're registered with Base.metadata
from src.models import (  # noqa: F401
    # Applications (App Builder)
    Application,
    AppRole,
    AuditLog,
    CLISession,
    Config,
    Execution,
    ExecutionLog,
    Form,
    FormRole,
    GlobalBranding,
    MFARecoveryCode,
    OAuthProvider,
    OAuthToken,
    Organization,
    Role,
    TrustedDevice,
    User,
    UserMFAMethod,
    UserOAuthAccount,
    UserRole,
    Workflow,
)

# Alembic Config object
config = context.config

# Setup logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for autogenerate
target_metadata = Base.metadata

# This fixed key is part of the native migration capability contract.  It is
# deliberately a session-level PostgreSQL advisory lock so the lock remains
# held across Alembic's transaction boundaries and is released automatically
# when the migration connection closes (including process termination).
MIGRATION_ADVISORY_LOCK_KEY = 0x4D54475F414C454D
MIGRATION_SERIALIZATION_VERSION = 1
_logger = logging.getLogger(__name__)


def _acquire_migration_advisory_lock(connection: Connection) -> None:
    """Acquire the migration lock without waiting behind another upgrader."""
    acquired = connection.execute(
        text("SELECT pg_try_advisory_lock(:lock_key)"),
        {"lock_key": MIGRATION_ADVISORY_LOCK_KEY},
    ).scalar_one()
    if not acquired:
        raise RuntimeError(
            "Database migration already in progress; refusing a concurrent upgrade"
        )


def _release_migration_advisory_lock(connection: Connection) -> None:
    """Release the lock and fail loudly if this session did not own it."""
    released = connection.execute(
        text("SELECT pg_advisory_unlock(:lock_key)"),
        {"lock_key": MIGRATION_ADVISORY_LOCK_KEY},
    ).scalar_one()
    if not released:
        raise RuntimeError(
            "Database migration advisory lock was not held by this session"
        )


def _expected_current_heads() -> tuple[str, ...] | None:
    """Validate and normalize an optional in-process migration head guard."""
    expected = config.attributes.get("expected_current_heads")
    if expected is None:
        return None
    if not isinstance(expected, list) or not all(
        isinstance(head, str) and head for head in expected
    ):
        raise RuntimeError(
            "expected_current_heads must be a list of non-empty revision strings"
        )
    return tuple(sorted(expected))


def _check_expected_current_heads(expected: tuple[str, ...] | None) -> None:
    """Reject a stale migration image before any revision DDL is emitted."""
    if expected is None:
        return
    actual = tuple(sorted(context.get_context().get_current_heads()))
    if actual != expected:
        raise RuntimeError(
            "Database migration heads changed; refusing upgrade "
            f"(expected={list(expected)!r}, actual={list(actual)!r})"
        )


@contextmanager
def migration_advisory_lock(connection: Connection) -> Iterator[None]:
    """Hold the native migration lock for the complete Alembic upgrade."""
    _acquire_migration_advisory_lock(connection)
    migration_error: BaseException | None = None
    try:
        # The lock probe is a SELECT and therefore autobegins a SQLAlchemy
        # transaction.  End that probe transaction before Alembic configures
        # its own transaction, otherwise Alembic treats the upgrade as an
        # externally managed transaction and the connection context can roll
        # the migration back when it closes.  The session-level advisory lock
        # survives this commit.
        connection.commit()
        yield
    except BaseException as exc:
        migration_error = exc
        raise
    finally:
        try:
            _release_migration_advisory_lock(connection)
        except Exception:
            if migration_error is None:
                raise
            _logger.exception(
                "Could not release the migration advisory lock after a failed upgrade"
            )


def get_url() -> str:
    """Get sync database URL from settings (for offline mode)."""
    settings = get_settings()
    return settings.database_url_sync


def get_async_url() -> str:
    """Get async database URL from settings (for online mode)."""
    settings = get_settings()
    return settings.database_url


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine,
    though an Engine is acceptable here as well. By skipping the Engine
    creation we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations using provided connection."""
    with migration_advisory_lock(connection):
        context.configure(connection=connection, target_metadata=target_metadata)
        expected_heads = _expected_current_heads()

        with context.begin_transaction():
            _check_expected_current_heads(expected_heads)
            context.run_migrations()


async def run_async_migrations() -> None:
    """
    Run migrations in 'online' mode with async engine.

    In this scenario we need to create an Engine and associate a
    connection with the context.
    """
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_async_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
