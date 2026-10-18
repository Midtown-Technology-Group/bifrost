from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import TEST_DATABASE_URL, TEST_DATABASE_URL_SYNC

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "alembic"
    / "versions"
    / "20261017_operation_receipt_admission.py"
)
INDEX_NAME = "ix_operation_receipts_namespace_admission"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "operation_receipt_admission_e2e_migration",
        MIGRATION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_upgrade(connection, module: ModuleType) -> None:
    context = MigrationContext.configure(connection)
    module.op = Operations(context)
    with context.begin_transaction():
        module.upgrade()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_operation_receipt_migration_recovers_after_partial_commit() -> None:
    setup_database_url = make_url(TEST_DATABASE_URL_SYNC)
    if setup_database_url.host == "pgbouncer":
        setup_database_url = setup_database_url.set(host="postgres")
    migration_database_url = make_url(TEST_DATABASE_URL)
    if migration_database_url.host == "pgbouncer":
        migration_database_url = migration_database_url.set(host="postgres")

    setup_engine = create_engine(setup_database_url, poolclass=NullPool)
    migration_engine = create_async_engine(
        migration_database_url,
        poolclass=NullPool,
    )
    schema = f"receipt_migration_{uuid4().hex}"
    search_path = f'SET search_path TO "{schema}", public'
    migration = _load_migration()

    try:
        # Reproduce both durable artifacts an interrupted migration may leave:
        # the committed column and an invalid concurrent-index catalog entry.
        with setup_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as setup:
            setup.execute(text(f'CREATE SCHEMA "{schema}"'))
            setup.execute(text(search_path))
            setup.execute(
                text(
                    """
                    CREATE TABLE operation_receipts (
                        id BIGSERIAL PRIMARY KEY,
                        namespace VARCHAR(128) NOT NULL,
                        key_hash VARCHAR(64) NOT NULL
                    )
                    """
                )
            )
            setup.execute(
                text(
                    """
                    INSERT INTO operation_receipts (namespace, key_hash)
                    VALUES ('legacy', 'first'), ('legacy', 'second')
                    """
                )
            )
            setup.execute(
                text(
                    "ALTER TABLE operation_receipts "
                    "ADD COLUMN admission_key VARCHAR(64)"
                )
            )
            with pytest.raises(IntegrityError):
                setup.execute(
                    text(
                        f"CREATE UNIQUE INDEX CONCURRENTLY {INDEX_NAME} "
                        "ON operation_receipts (namespace)"
                    )
                )

            invalid = setup.execute(
                text(
                    """
                    SELECT index_state.indisvalid
                    FROM pg_index AS index_state
                    WHERE index_state.indexrelid = to_regclass(:index_name)
                    """
                ),
                {"index_name": INDEX_NAME},
            ).scalar_one()
            assert invalid is False

        async with migration_engine.connect() as connection:
            await connection.execute(text(search_path))
            await connection.commit()
            await connection.run_sync(_run_upgrade, migration)

            legacy_rows = (
                await connection.execute(
                    text(
                        """
                        SELECT namespace, key_hash, admission_key
                        FROM operation_receipts
                        ORDER BY key_hash
                        """
                    )
                )
            ).all()
            assert legacy_rows == [
                ("legacy", "first", None),
                ("legacy", "second", None),
            ]

            index = (
                await connection.execute(
                    text(
                        """
                        SELECT
                            index_relation.oid,
                            index_state.indisvalid,
                            index_state.indisready,
                            index_state.indisunique,
                            pg_get_indexdef(index_relation.oid)
                        FROM pg_class AS index_relation
                        JOIN pg_index AS index_state
                          ON index_state.indexrelid = index_relation.oid
                        WHERE index_relation.oid = to_regclass(:index_name)
                        """
                    ),
                    {"index_name": INDEX_NAME},
                )
                .mappings()
                .one()
            )
            assert index["indisvalid"] is True
            assert index["indisready"] is True
            assert index["indisunique"] is False
            assert "(namespace, admission_key)" in index["pg_get_indexdef"]
            assert "WHERE (admission_key IS NOT NULL)" in index["pg_get_indexdef"]

            # A retry after the index completed but before Alembic stamped the
            # revision recognizes the exact index rather than replacing it.
            index_oid = index["oid"]
            await connection.commit()
            await connection.run_sync(_run_upgrade, migration)
            preserved_oid = (
                await connection.execute(
                    text("SELECT to_regclass(:index_name)::oid"),
                    {"index_name": INDEX_NAME},
                )
            ).scalar_one()
            assert preserved_oid == index_oid
    finally:
        await migration_engine.dispose()
        with setup_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as cleanup:
            cleanup.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        setup_engine.dispose()
