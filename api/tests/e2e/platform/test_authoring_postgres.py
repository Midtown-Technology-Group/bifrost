"""Populated migration proof with real FKs/uniqueness and a disposable database.

A minimal historical schema is populated before the accepted no-op token
migration runs with real Alembic operations and the asyncpg driver. This pattern
does not substitute SQL-string assertions for execution.
"""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.helpers.postgres import disposable_postgres


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_populated_upgrade_preserves_credentials_references_and_rollback():
    async with disposable_postgres() as engine:
        path = (
            Path(__file__).resolve().parents[3]
            / "alembic/versions/20260601_promote_mis_stamped_global_oauth_tokens.py"
        )
        spec = importlib.util.spec_from_file_location("author_token_migration", path)
        assert spec and spec.loader
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)

        async with engine.begin() as connection:
            for statement in [
                "CREATE TABLE organizations (id uuid PRIMARY KEY,name text,is_provider boolean,created_by text)",
                "CREATE TABLE oauth_providers (id uuid PRIMARY KEY,provider_name text,client_id text,encrypted_client_secret bytea,scopes jsonb,token_url_defaults jsonb,provider_metadata jsonb)",
                "CREATE TABLE oauth_tokens (id uuid PRIMARY KEY,organization_id uuid REFERENCES organizations(id),provider_id uuid REFERENCES oauth_providers(id),encrypted_access_token bytea,encrypted_refresh_token bytea,scopes jsonb)",
            ]:
                await connection.execute(text(statement))
        org, provider, token, global_token = (uuid4() for _ in range(4))
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO organizations (id,name,is_provider,created_by) VALUES (:id,'author-fixture',true,'test')"
                ),
                {"id": org},
            )
            await connection.execute(
                text(
                    "INSERT INTO oauth_providers (id,provider_name,client_id,encrypted_client_secret,scopes,token_url_defaults,provider_metadata) VALUES (:id,'author-provider','client',:secret,'[]','{}','{}')"
                ),
                {"id": provider, "secret": b"sentinel-client-secret"},
            )
            for identity, scope, secret in [
                (token, org, b"private-token"),
                (global_token, None, b"global-token"),
            ]:
                await connection.execute(
                    text(
                        "INSERT INTO oauth_tokens (id,organization_id,provider_id,encrypted_access_token,encrypted_refresh_token,scopes) VALUES (:id,:org,:provider,:access,:refresh,'[]')"
                    ),
                    {
                        "id": identity,
                        "org": scope,
                        "provider": provider,
                        "access": secret,
                        "refresh": b"refresh-" + secret,
                    },
                )

        async def snapshot():
            async with engine.connect() as connection:
                return (
                    await connection.execute(
                        text(
                            "SELECT t.id,t.organization_id,t.provider_id,t.encrypted_access_token,t.encrypted_refresh_token,p.client_id,p.encrypted_client_secret FROM oauth_tokens t JOIN oauth_providers p ON p.id=t.provider_id ORDER BY t.id"
                        )
                    )
                ).all()

        before = await snapshot()
        assert len(before) == 2
        async with engine.begin() as connection:

            def migrate(sync):
                with Operations.context(MigrationContext.configure(sync)):
                    migration.upgrade()

            await connection.run_sync(migrate)
        assert await snapshot() == before
        async with engine.begin() as connection:

            def downgrade(sync):
                with Operations.context(MigrationContext.configure(sync)):
                    migration.downgrade()

            await connection.run_sync(downgrade)
        assert await snapshot() == before

        # A failed conflicting-identity transaction must preserve the populated
        # credential rows. PostgreSQL enforces the actual PK/FK constraints.
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text("DELETE FROM oauth_tokens WHERE id=:id"), {"id": token}
                )
                await connection.execute(
                    text(
                        "INSERT INTO oauth_tokens SELECT * FROM oauth_tokens WHERE id=:id"
                    ),
                    {"id": global_token},
                )
        assert await snapshot() == before
