"""Deduplicate global OAuth providers and enforce their natural key.

Revision ID: 20261011_oauth_provider_dedupe
Revises: 20261006_mtg_upstream_sdk
Create Date: 2026-10-11
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20261011_oauth_provider_dedupe"
down_revision: str = "20261006_mtg_upstream_sdk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep writers from recreating a duplicate between cleanup and index DDL.
    # Reads continue while the transactional migration owns this lock.
    op.execute(
        "LOCK TABLE oauth_providers, oauth_tokens, mcp_servers "
        "IN SHARE ROW EXCLUSIVE MODE"
    )

    # Multiple attached integrations with the same global provider name cannot
    # be merged without choosing which integration loses OAuth configuration.
    # Refuse that ambiguous state rather than silently deleting live linkage.
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM oauth_providers
                    WHERE organization_id IS NULL
                    GROUP BY provider_name
                    HAVING COUNT(DISTINCT integration_id) FILTER (
                        WHERE integration_id IS NOT NULL
                    ) > 1
                ) THEN
                    RAISE EXCEPTION
                        'global OAuth provider names are attached to multiple integrations; resolve before upgrade';
                END IF;
            END
            $$;
            """
        )
    )

    # Preserve the best credential-bearing row, all token and MCP references,
    # and the group's single integration link. This repairs the specific state
    # produced when manifest deletion orphaned a configured provider and a
    # later import inserted a new placeholder with the same global name.
    op.execute(
        sa.text(
            """
            CREATE TEMPORARY TABLE oauth_provider_dedupe
            ON COMMIT DROP AS
            WITH ranked AS (
                SELECT
                    provider.id,
                    FIRST_VALUE(provider.id) OVER (
                        PARTITION BY provider.provider_name
                        ORDER BY
                            (
                                OCTET_LENGTH(provider.encrypted_client_secret) > 0
                                OR EXISTS (
                                    SELECT 1
                                    FROM oauth_tokens token
                                    WHERE token.provider_id = provider.id
                                )
                            ) DESC,
                            (provider.integration_id IS NOT NULL) DESC,
                            provider.updated_at DESC,
                            provider.created_at DESC,
                            provider.id
                    ) AS survivor_id,
                    FIRST_VALUE(provider.integration_id) OVER (
                        PARTITION BY provider.provider_name
                        ORDER BY
                            (provider.integration_id IS NULL),
                            provider.id
                    ) AS integration_id
                FROM oauth_providers provider
                WHERE provider.organization_id IS NULL
            )
            SELECT id, survivor_id, integration_id
            FROM ranked
            WHERE id <> survivor_id;

            UPDATE oauth_providers provider
            SET integration_id = NULL
            FROM oauth_provider_dedupe duplicate
            WHERE provider.id = duplicate.id;

            UPDATE oauth_providers provider
            SET integration_id = duplicate.integration_id
            FROM oauth_provider_dedupe duplicate
            WHERE provider.id = duplicate.survivor_id
              AND duplicate.integration_id IS NOT NULL;

            UPDATE oauth_tokens token
            SET provider_id = duplicate.survivor_id
            FROM oauth_provider_dedupe duplicate
            WHERE token.provider_id = duplicate.id;

            UPDATE mcp_servers server
            SET oauth_provider_id = duplicate.survivor_id
            FROM oauth_provider_dedupe duplicate
            WHERE server.oauth_provider_id = duplicate.id;

            DELETE FROM oauth_providers provider
            USING oauth_provider_dedupe duplicate
            WHERE provider.id = duplicate.id;
            """
        )
    )

    op.drop_index("ix_oauth_providers_org_name", table_name="oauth_providers")
    op.create_index(
        "ix_oauth_providers_org_name",
        "oauth_providers",
        ["organization_id", "provider_name"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index("ix_oauth_providers_org_name", table_name="oauth_providers")
    op.create_index(
        "ix_oauth_providers_org_name",
        "oauth_providers",
        ["organization_id", "provider_name"],
        unique=True,
    )
