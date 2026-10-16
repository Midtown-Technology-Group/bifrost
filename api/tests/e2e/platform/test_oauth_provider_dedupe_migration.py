"""Exercise the OAuth-provider dedupe migration through real asyncpg."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


pytestmark = pytest.mark.e2e

MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "alembic"
    / "versions"
    / "20261011_oauth_provider_dedupe.py"
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"oauth_provider_dedupe_{uuid4().hex}", MIGRATION_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_upgrade(sync_connection, migration: ModuleType) -> None:
    context = MigrationContext.configure(sync_connection)
    operations = Operations(context)
    original_op = migration.op
    migration.op = operations
    try:
        migration.upgrade()
    finally:
        migration.op = original_op


async def _prepare_pre_migration_schema(connection, schema: str) -> None:
    await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
    await connection.execute(text("CREATE TABLE integrations (id UUID PRIMARY KEY)"))
    await connection.execute(
        text("""
        CREATE TABLE oauth_providers (
            id UUID PRIMARY KEY,
            provider_name TEXT NOT NULL,
            organization_id UUID,
            integration_id UUID,
            encrypted_client_secret BYTEA NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT uq_oauth_providers_integration_id UNIQUE (integration_id)
        )
    """)
    )
    await connection.execute(
        text("""
        CREATE UNIQUE INDEX ix_oauth_providers_org_name
        ON oauth_providers (organization_id, provider_name)
    """)
    )
    await connection.execute(
        text("""
        CREATE TABLE oauth_tokens (
            id UUID PRIMARY KEY,
            provider_id UUID NOT NULL REFERENCES oauth_providers(id)
        )
    """)
    )
    await connection.execute(
        text("""
        CREATE TABLE mcp_servers (
            id UUID PRIMARY KEY,
            oauth_provider_id UUID REFERENCES oauth_providers(id)
        )
    """)
    )


async def _index_uses_nulls_not_distinct(connection, schema: str) -> bool:
    result = await connection.scalar(
        text("""
            SELECT idx.indnullsnotdistinct
            FROM pg_index idx
            JOIN pg_class relation ON relation.oid = idx.indexrelid
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = :schema
              AND relation.relname = 'ix_oauth_providers_org_name'
        """),
        {"schema": schema},
    )
    assert isinstance(result, bool)
    return result


@pytest.mark.asyncio
async def test_empty_upgrade_creates_null_aware_natural_key(async_engine) -> None:
    schema = f"oauth_dedupe_empty_{uuid4().hex}"
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await _prepare_pre_migration_schema(connection, schema)
            await connection.run_sync(_run_upgrade, _load_migration())

            assert (
                await connection.scalar(text("SELECT count(*) FROM oauth_providers"))
                == 0
            )
            assert await _index_uses_nulls_not_distinct(connection, schema)
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_populated_upgrade_preserves_credentials_and_repoints_references(
    async_engine,
) -> None:
    schema = f"oauth_dedupe_populated_{uuid4().hex}"
    survivor_id = uuid4()
    duplicate_id = uuid4()
    integration_id = uuid4()
    token_id = uuid4()
    server_id = uuid4()
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await _prepare_pre_migration_schema(connection, schema)
            await connection.execute(
                text("INSERT INTO integrations (id) VALUES (:id)"),
                {"id": integration_id},
            )
            await connection.execute(
                text("""
                    INSERT INTO oauth_providers (
                        id, provider_name, organization_id, integration_id,
                        encrypted_client_secret, created_at, updated_at
                    ) VALUES
                    (:survivor, 'shared', NULL, NULL, :secret, NOW(), NOW()),
                    (:duplicate, 'shared', NULL, :integration, :empty, NOW(), NOW())
                """),
                {
                    "survivor": survivor_id,
                    "duplicate": duplicate_id,
                    "integration": integration_id,
                    "secret": b"configured-secret",
                    "empty": b"",
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO oauth_tokens (id, provider_id) VALUES (:id, :provider)"
                ),
                {"id": token_id, "provider": duplicate_id},
            )
            await connection.execute(
                text("""
                    INSERT INTO mcp_servers (id, oauth_provider_id)
                    VALUES (:id, :provider)
                """),
                {"id": server_id, "provider": duplicate_id},
            )

            await connection.run_sync(_run_upgrade, _load_migration())

            providers = (
                (
                    await connection.execute(
                        text("""
                    SELECT id, integration_id, encrypted_client_secret
                    FROM oauth_providers
                """)
                    )
                )
                .mappings()
                .all()
            )
            assert [dict(provider) for provider in providers] == [
                {
                    "id": survivor_id,
                    "integration_id": integration_id,
                    "encrypted_client_secret": b"configured-secret",
                }
            ]
            assert (
                await connection.scalar(
                    text("SELECT provider_id FROM oauth_tokens WHERE id = :id"),
                    {"id": token_id},
                )
                == survivor_id
            )
            assert (
                await connection.scalar(
                    text("SELECT oauth_provider_id FROM mcp_servers WHERE id = :id"),
                    {"id": server_id},
                )
                == survivor_id
            )
            assert await _index_uses_nulls_not_distinct(connection, schema)
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_ambiguous_upgrade_rolls_back_without_deleting_providers(
    async_engine,
) -> None:
    schema = f"oauth_dedupe_ambiguous_{uuid4().hex}"
    first_integration = uuid4()
    second_integration = uuid4()
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await _prepare_pre_migration_schema(connection, schema)
            await connection.execute(
                text("INSERT INTO integrations (id) VALUES (:first), (:second)"),
                {"first": first_integration, "second": second_integration},
            )
            await connection.execute(
                text("""
                    INSERT INTO oauth_providers (
                        id, provider_name, organization_id, integration_id,
                        encrypted_client_secret, created_at, updated_at
                    ) VALUES
                    (:first_id, 'ambiguous', NULL, :first_integration, :empty, NOW(), NOW()),
                    (:second_id, 'ambiguous', NULL, :second_integration, :empty, NOW(), NOW())
                """),
                {
                    "first_id": uuid4(),
                    "second_id": uuid4(),
                    "first_integration": first_integration,
                    "second_integration": second_integration,
                    "empty": b"",
                },
            )

            savepoint = await connection.begin_nested()
            try:
                with pytest.raises(DBAPIError, match="global OAuth provider names"):
                    await connection.run_sync(_run_upgrade, _load_migration())
            finally:
                await savepoint.rollback()

            assert (
                await connection.scalar(text("SELECT count(*) FROM oauth_providers"))
                == 2
            )
            assert not await _index_uses_nulls_not_distinct(connection, schema)
        finally:
            await transaction.rollback()
