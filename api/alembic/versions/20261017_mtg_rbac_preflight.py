"""Validate and preserve MTG's legacy RBAC state before incoming R2b DDL.

The private rollback schema is migration-owned and has no ORM/API surface.
PUBLIC has no privileges and immutable triggers protect normal application
DML. As elsewhere in this deployment, the database owner/superuser remains a
trusted DDL principal: revoking PUBLIC cannot restrict the object's owner.
The release lane must hold its external maintenance fence across the complete
upstream graph, which contains later autocommit/concurrent-index revisions.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261017_mtg_rbac_preflight"
down_revision = "20260928_audit_op_surface"
branch_labels = None
depends_on = None

# Frozen bridge vocabulary, deliberately independent of application imports.
LEGACY_KEYS = ("can_manage_config", "can_execute_devices", "can_promote_agent")
BUILTIN_IDS = tuple(f"00000000-0000-0000-0000-{suffix:012d}" for suffix in (5, 6, 7, 8))


def _refuse_count(bind, query: str, reason: str, parameters=None) -> None:
    count = bind.execute(sa.text(query), parameters or {}).scalar_one()
    if count:
        # Never put legacy JSON, names, namespaces or holder IDs in errors.
        raise RuntimeError(f"MTG RBAC preflight: {reason} ({count} offending rows)")


def upgrade() -> None:
    bind = op.get_bind()
    if not bind.in_transaction():
        raise RuntimeError("MTG RBAC preflight requires an online PostgreSQL transaction")
    _refuse_count(
        bind,
        """
        SELECT count(*) FROM pg_namespace WHERE nspname = 'mtg_rbac_bridge'
        """,
        "rollback schema already exists; reconcile migration identity",
    )
    _refuse_count(
        bind,
        """
        SELECT (to_regclass('public.role_permissions') IS NOT NULL)::int
             + (to_regclass('public.user_role_boundaries') IS NOT NULL)::int
             + (SELECT count(*) FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND ((table_name = 'users' AND column_name = 'base_role_id')
                    OR (table_name = 'roles' AND column_name = 'is_base')))
        """,
        "R2b schema or unexpected assignment boundaries already exist",
    )
    # Held through preflight, R2b and bridge in the SAME transaction. Also
    # fence home-org deletion and namespace ACL changes during reconciliation.
    bind.execute(sa.text(
        "LOCK TABLE public.roles, public.user_roles, public.users, "
        "public.organizations, public.knowledge_namespace_roles "
        "IN SHARE ROW EXCLUSIVE MODE"
    ))
    # Abort FIRST, separately: never rely on WHERE evaluation order to make
    # jsonb_each safe for arrays/scalars/SQL NULL/JSON null.
    _refuse_count(
        bind,
        "SELECT count(*) FROM public.roles "
        "WHERE jsonb_typeof(permissions) IS DISTINCT FROM 'object'",
        "legacy permissions must be JSON objects",
    )
    _refuse_count(
        bind,
        """
        SELECT count(*) FROM public.roles r
        CROSS JOIN LATERAL jsonb_each(r.permissions) e
        WHERE e.key NOT IN ('can_manage_config', 'can_execute_devices', 'can_promote_agent')
           OR jsonb_typeof(e.value) IS DISTINCT FROM 'boolean'
        """,
        "unknown legacy keys or nonboolean values",
    )
    _refuse_count(
        bind,
        "SELECT count(*) FROM public.roles WHERE id = ANY(CAST(:ids AS uuid[]))",
        "fixed builtin UUID collision",
        {"ids": list(BUILTIN_IDS)},
    )
    # Check actual references even if an installation has disabled/NOT VALID
    # foreign keys. All FK references to roles/users/orgs are included, not
    # just the device-holder join (which would hide orphaned memberships).
    references = bind.execute(sa.text("""
        SELECT sn.nspname AS source_schema, st.relname AS source_table,
               sa.attname AS source_column, tt.relname AS target_table,
               ta.attname AS target_column
        FROM pg_constraint c
        JOIN pg_class st ON st.oid = c.conrelid
        JOIN pg_namespace sn ON sn.oid = st.relnamespace
        JOIN pg_class tt ON tt.oid = c.confrelid
        JOIN pg_namespace tn ON tn.oid = tt.relnamespace
        JOIN pg_attribute sa ON sa.attrelid = st.oid AND sa.attnum = c.conkey[1]
        JOIN pg_attribute ta ON ta.attrelid = tt.oid AND ta.attnum = c.confkey[1]
        WHERE c.contype = 'f' AND tn.nspname = 'public'
          AND tt.relname IN ('roles', 'users', 'organizations')
          AND cardinality(c.conkey) = 1
    """)).mappings().all()
    quote = bind.dialect.identifier_preparer.quote
    for reference in references:
        source = f"{quote(reference['source_schema'])}.{quote(reference['source_table'])}"
        target = f"public.{quote(reference['target_table'])}"
        column = quote(reference["source_column"])
        target_column = quote(reference["target_column"])
        _refuse_count(
            bind,
            f"SELECT count(*) FROM {source} s LEFT JOIN {target} t "
            f"ON t.{target_column} = s.{column} "
            f"WHERE s.{column} IS NOT NULL AND t.{target_column} IS NULL",
            "orphan identity, organization or role references",
        )
    # Explicit checks also detect absent FK declarations on the core tables.
    _refuse_count(bind, """
        SELECT count(*) FROM public.user_roles ur
        LEFT JOIN public.users u ON u.id = ur.user_id
        LEFT JOIN public.roles r ON r.id = ur.role_id
        WHERE u.id IS NULL OR r.id IS NULL
    """, "orphan role memberships")
    _refuse_count(bind, """
        SELECT count(*) FROM public.users u
        LEFT JOIN public.organizations o ON o.id = u.organization_id
        WHERE u.organization_id IS NOT NULL AND o.id IS NULL
    """, "orphan home organizations")
    _refuse_count(bind, """
        SELECT count(*) FROM public.knowledge_namespace_roles k
        LEFT JOIN public.roles r ON r.id = k.role_id
        LEFT JOIN public.organizations o ON o.id = k.organization_id
        WHERE r.id IS NULL OR (k.organization_id IS NOT NULL AND o.id IS NULL)
    """, "orphan knowledge assignments")
    _refuse_count(bind, """
        SELECT count(*) FROM public.user_roles ur
        JOIN public.users u ON u.id = ur.user_id
        JOIN public.roles r ON r.id = ur.role_id
        WHERE u.is_superuser IS NOT TRUE AND u.organization_id IS NULL
          AND (r.permissions -> 'can_manage_config' = 'true'::jsonb
            OR r.permissions -> 'can_execute_devices' = 'true'::jsonb)
    """, "ordinary device-grant holder has no home organization")
    _refuse_count(bind, """
        SELECT count(*) FROM (
            SELECT namespace, organization_id, role_id
            FROM public.knowledge_namespace_roles
            GROUP BY namespace, organization_id, role_id HAVING count(*) > 1
        ) duplicates
    """, "duplicate knowledge assignment identities")
    valid_knowledge_unique = bind.execute(sa.text("""
        SELECT count(*) FROM pg_constraint c
        JOIN pg_index i ON i.indexrelid = c.conindid
        WHERE c.conrelid = 'public.knowledge_namespace_roles'::regclass
          AND c.conname = 'uq_knowledge_ns_role_org' AND c.contype = 'u'
          AND i.indisunique AND i.indisvalid AND i.indnullsnotdistinct
          AND (SELECT array_agg(a.attname::text ORDER BY k.position)
               FROM unnest(c.conkey) WITH ORDINALITY k(number, position)
               JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.number)
              = ARRAY['namespace', 'organization_id', 'role_id']::text[]
    """)).scalar_one()
    if valid_knowledge_unique != 1:
        raise RuntimeError("MTG RBAC preflight: knowledge NULLS NOT DISTINCT uniqueness differs")

    bind.execute(sa.text("CREATE SCHEMA mtg_rbac_bridge"))
    bind.execute(sa.text("REVOKE ALL ON SCHEMA mtg_rbac_bridge FROM PUBLIC"))
    bind.execute(sa.text("""
        CREATE TABLE mtg_rbac_bridge.rollback_record (
            singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
            contract_version integer NOT NULL CHECK (contract_version = 1),
            transaction_id bigint NOT NULL,
            migration_owner name NOT NULL DEFAULT current_user,
            original_state jsonb NOT NULL,
            bridged_state jsonb,
            created_permission_rows jsonb,
            created_boundary_rows jsonb
        )
    """))
    bind.execute(sa.text("REVOKE ALL ON TABLE mtg_rbac_bridge.rollback_record FROM PUBLIC"))
    # SHA-256 fingerprints contain no readable holder/namespace data. Original
    # role JSON is retained only inside this restricted migration record.
    bind.execute(sa.text("""
        CREATE FUNCTION mtg_rbac_bridge.legacy_state() RETURNS jsonb
        LANGUAGE sql STABLE SET search_path = pg_catalog AS $function$
          SELECT jsonb_build_object(
            'roles', (SELECT coalesce(jsonb_object_agg(id::text, permissions ORDER BY id), '{}'::jsonb)
                      FROM public.roles WHERE id NOT IN (
                        '00000000-0000-0000-0000-000000000005'::uuid,
                        '00000000-0000-0000-0000-000000000006'::uuid,
                        '00000000-0000-0000-0000-000000000007'::uuid,
                        '00000000-0000-0000-0000-000000000008'::uuid)),
            'memberships', (SELECT encode(sha256(convert_to(coalesce(
                jsonb_agg(to_jsonb(ur) ORDER BY user_id, role_id), '[]'::jsonb)::text, 'UTF8')), 'hex')
                FROM public.user_roles ur),
            'holders', (SELECT encode(sha256(convert_to(coalesce(
                jsonb_agg(jsonb_build_array(id, organization_id, is_superuser) ORDER BY id),
                '[]'::jsonb)::text, 'UTF8')), 'hex') FROM public.users),
            'knowledge', (SELECT encode(sha256(convert_to(coalesce(
                jsonb_agg(to_jsonb(k) ORDER BY id), '[]'::jsonb)::text, 'UTF8')), 'hex')
                FROM public.knowledge_namespace_roles k),
            'knowledge_indexes', (SELECT coalesce(jsonb_agg(jsonb_build_array(
                indexrelid::regclass::text, pg_get_indexdef(indexrelid),
                indisvalid, indisunique, indnullsnotdistinct) ORDER BY indexrelid::regclass::text), '[]'::jsonb)
                FROM pg_index WHERE indrelid = 'public.knowledge_namespace_roles'::regclass)
          )
        $function$
    """))
    bind.execute(sa.text("REVOKE ALL ON FUNCTION mtg_rbac_bridge.legacy_state() FROM PUBLIC"))
    bind.execute(sa.text("""
        INSERT INTO mtg_rbac_bridge.rollback_record
            (singleton, contract_version, transaction_id, original_state)
        VALUES (true, 1, txid_current(), mtg_rbac_bridge.legacy_state())
    """))
    # REVOKE PUBLIC alone does not remove grants inherited from customized
    # ALTER DEFAULT PRIVILEGES. Refuse extra ACL grantees rather than expose
    # the rollback record or change installation-wide default privileges.
    _refuse_count(bind, """
        SELECT count(*) FROM (
          SELECT a.grantee FROM pg_namespace n
            CROSS JOIN LATERAL aclexplode(n.nspacl) a
            WHERE n.nspname = 'mtg_rbac_bridge' AND a.grantee <> n.nspowner
          UNION ALL
          SELECT a.grantee FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            CROSS JOIN LATERAL aclexplode(c.relacl) a
            WHERE n.nspname = 'mtg_rbac_bridge' AND a.grantee <> c.relowner
          UNION ALL
          SELECT a.grantee FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            CROSS JOIN LATERAL aclexplode(p.proacl) a
            WHERE n.nspname = 'mtg_rbac_bridge' AND a.grantee <> p.proowner
        ) extra_archive_grants
    """, "default privileges grant rollback archive access to additional roles")


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "LOCK TABLE public.roles, public.user_roles, public.users, "
        "public.organizations, public.knowledge_namespace_roles "
        "IN SHARE ROW EXCLUSIVE MODE"
    ))
    unchanged = bind.execute(sa.text("""
        SELECT original_state = mtg_rbac_bridge.legacy_state()
          AND bridged_state IS NULL
          AND migration_owner = current_user
        FROM mtg_rbac_bridge.rollback_record WHERE singleton
    """)).scalar_one()
    if not unchanged:
        raise RuntimeError("MTG RBAC rollback refused: original grants or identity state changed")
    bind.execute(sa.text("DROP FUNCTION mtg_rbac_bridge.legacy_state()"))
    bind.execute(sa.text("DROP TABLE mtg_rbac_bridge.rollback_record"))
    bind.execute(sa.text("DROP SCHEMA mtg_rbac_bridge"))
