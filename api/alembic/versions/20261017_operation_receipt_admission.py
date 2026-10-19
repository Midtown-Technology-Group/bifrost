"""bound operation receipt admission per hashed caller scope

Revision ID: 20261017_receipt_admission
Revises: 20261017_teams_receipt_index
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261017_receipt_admission"
down_revision: str = "20261017_teams_receipt_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_INDEX_NAME = "ix_operation_receipts_namespace_admission"
_EXPECTED_INDEX_COLUMNS = ["namespace", "admission_key"]


def _validate_admission_key_column() -> None:
    column = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT
                    format_type(attribute.atttypid, attribute.atttypmod) AS data_type,
                    attribute.attnotnull AS not_null,
                    pg_get_expr(default_value.adbin, default_value.adrelid) AS default_value,
                    attribute.attidentity <> '' AS has_identity,
                    attribute.attgenerated <> '' AS has_generated_value
                FROM pg_attribute AS attribute
                LEFT JOIN pg_attrdef AS default_value
                  ON default_value.adrelid = attribute.attrelid
                 AND default_value.adnum = attribute.attnum
                WHERE attribute.attrelid = to_regclass('operation_receipts')
                  AND attribute.attname = 'admission_key'
                  AND NOT attribute.attisdropped
                """
            )
        )
        .mappings()
        .one_or_none()
    )
    if column is None:
        raise RuntimeError("operation_receipts.admission_key was not created")

    compatible = (
        column["data_type"] == "character varying(64)"
        and not column["not_null"]
        and column["default_value"] is None
        and not column["has_identity"]
        and not column["has_generated_value"]
    )
    if not compatible:
        raise RuntimeError(
            "operation_receipts.admission_key already exists with an "
            "incompatible definition"
        )


def _admission_index_is_complete() -> bool:
    index = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT
                    index_state.indrelid = to_regclass('operation_receipts')
                        AS correct_table,
                    index_state.indisvalid AS valid,
                    index_state.indisready AS ready,
                    index_state.indisunique AS unique_index,
                    access_method.amname AS access_method,
                    index_state.indnkeyatts AS key_count,
                    index_state.indnatts AS attribute_count,
                    ARRAY(
                        SELECT attribute.attname
                        FROM unnest(index_state.indkey)
                            WITH ORDINALITY AS index_key(attnum, position)
                        JOIN pg_attribute AS attribute
                          ON attribute.attrelid = index_state.indrelid
                         AND attribute.attnum = index_key.attnum
                        WHERE index_key.position <= index_state.indnkeyatts
                        ORDER BY index_key.position
                    ) AS columns,
                    pg_get_expr(index_state.indpred, index_state.indrelid)
                        AS predicate
                FROM pg_class AS index_relation
                JOIN pg_index AS index_state
                  ON index_state.indexrelid = index_relation.oid
                JOIN pg_am AS access_method
                  ON access_method.oid = index_relation.relam
                WHERE index_relation.oid = to_regclass(
                    'ix_operation_receipts_namespace_admission'
                )
                """
            )
        )
        .mappings()
        .one_or_none()
    )
    if index is None:
        return False

    predicate = "".join((index["predicate"] or "").split()).strip("()")
    return bool(
        index["correct_table"]
        and index["valid"]
        and index["ready"]
        and not index["unique_index"]
        and index["access_method"] == "btree"
        and index["key_count"] == 2
        and index["attribute_count"] == 2
        and list(index["columns"]) == _EXPECTED_INDEX_COLUMNS
        and predicate == "admission_keyISNOTNULL"
    )


def upgrade() -> None:
    # Legacy rows remain valid permanent tombstones. New bounded callers write
    # a privacy-preserving admission digest used only for exact quota counts.
    # IF NOT EXISTS is intentional: entering the autocommit block below commits
    # this DDL before the revision can be stamped. A retry after an interrupted
    # concurrent index build must resume instead of failing on the column.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "ALTER TABLE operation_receipts "
        "ADD COLUMN IF NOT EXISTS admission_key VARCHAR(64)"
    )
    _validate_admission_key_column()

    # This table is intentionally append-only. Build without blocking receipt
    # writes even when an existing installation already has many tombstones. A
    # failed concurrent build can leave an invalid catalog entry, so recognize
    # the exact completed index and replace every other state before retrying.
    with op.get_context().autocommit_block():
        try:
            op.execute("SET lock_timeout = '5s'")
            op.execute("SET statement_timeout = '30min'")
            if not _admission_index_is_complete():
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}")
                op.execute(
                    "CREATE INDEX CONCURRENTLY "
                    f"{_INDEX_NAME} "
                    "ON operation_receipts (namespace, admission_key) "
                    "WHERE admission_key IS NOT NULL"
                )
        finally:
            op.execute("RESET statement_timeout")
            op.execute("RESET lock_timeout")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        try:
            op.execute("SET lock_timeout = '5s'")
            op.execute("SET statement_timeout = '30min'")
            op.execute(
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "ix_operation_receipts_namespace_admission"
            )
        finally:
            op.execute("RESET statement_timeout")
            op.execute("RESET lock_timeout")
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE operation_receipts DROP COLUMN IF EXISTS admission_key")
