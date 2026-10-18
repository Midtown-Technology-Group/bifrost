"""Translate MTG's frozen legacy device/promotion grants into permission rows.

Only the three literal boolean-true keys below become permissions. The JSON
column remains physically present, unmapped, and frozen against normal DML.
This revision supports an immediate, unchanged-state downgrade of this bridge
segment only. It cannot reverse the full incoming graph or restore stale JSON
after grant, membership, home-organization or namespace edits.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "20261017_mtg_device_grant_bridge"
down_revision = "20260929_r2b_roles"
branch_labels = None
depends_on = None

# Frozen at migration authoring time. Never import application permission,
# role, evaluator or catalog modules from this historical revision.
LEGACY_PERMISSION_MAP = {
    "can_manage_config": "devices.readwrite",
    "can_execute_devices": "devices.execute",
    "can_promote_agent": "agents.promote",
}
USER_SEED = frozenset({
    "agentruns.read", "agents.read", "apps.read", "configs.read", "events.read",
    "executions.read", "forms.read", "integrations.read", "knowledge.read", "mcp.read",
    "metrics.read", "policyrules.read", "roles.read", "settings.read", "tables.read", "workflows.read",
})
OPERATOR_SEED = frozenset({
    "agentruns.read", "agents.read", "apps.read", "configs.read", "executions.read",
    "forms.read", "integrations.read", "metrics.read", "organizations.read", "workflows.read",
})
USER_ID = "00000000-0000-0000-0000-000000000006"
OPERATOR_ID = "00000000-0000-0000-0000-000000000007"


def _fence(bind) -> None:
    bind.execute(sa.text(
        "LOCK TABLE public.roles, public.user_roles, public.users, "
        "public.organizations, public.knowledge_namespace_roles, "
        "public.role_permissions, public.user_role_boundaries "
        "IN SHARE ROW EXCLUSIVE MODE"
    ))


def _assert_zero(bind, query: str, reason: str) -> None:
    count = bind.execute(sa.text(query)).scalar_one()
    if count:
        raise RuntimeError(f"MTG device-grant bridge: {reason} ({count} offending rows)")


def _expected_permissions(bind) -> set[tuple[str, str]]:
    expected = {(USER_ID, permission) for permission in USER_SEED}
    expected |= {(OPERATOR_ID, permission) for permission in OPERATOR_SEED}
    for key, permission in LEGACY_PERMISSION_MAP.items():
        roles = bind.execute(sa.text(
            "SELECT id::text FROM public.roles WHERE permissions -> :key = 'true'::jsonb"
        ), {"key": key}).scalars()
        expected.update((role_id, permission) for role_id in roles)
    return expected


def upgrade() -> None:
    bind = op.get_bind()
    _fence(bind)
    valid = bind.execute(sa.text("""
        SELECT contract_version = 1 AND transaction_id = txid_current()
          AND migration_owner = current_user
          AND original_state = mtg_rbac_bridge.legacy_state()
          AND bridged_state IS NULL
        FROM mtg_rbac_bridge.rollback_record WHERE singleton
    """)).scalar_one()
    if not valid:
        raise RuntimeError("MTG device-grant bridge requires its unchanged same-transaction preflight")
    # Repeat strict validation after R2b; builtin JSON defaults must also be
    # valid. No malformed input can be masked by joins or ON CONFLICT.
    _assert_zero(bind,
        "SELECT count(*) FROM public.roles WHERE jsonb_typeof(permissions) IS DISTINCT FROM 'object'",
        "legacy permissions must be JSON objects",
    )
    _assert_zero(bind, """
        SELECT count(*) FROM public.roles r CROSS JOIN LATERAL jsonb_each(r.permissions) e
        WHERE e.key NOT IN ('can_manage_config', 'can_execute_devices', 'can_promote_agent')
           OR jsonb_typeof(e.value) IS DISTINCT FROM 'boolean'
    """, "unknown legacy keys or nonboolean values")
    _assert_zero(bind, """
        SELECT count(*) FROM public.roles r
        WHERE r.id NOT IN (
          '00000000-0000-0000-0000-000000000005'::uuid,
          '00000000-0000-0000-0000-000000000006'::uuid,
          '00000000-0000-0000-0000-000000000007'::uuid)
          AND NOT ((SELECT original_state -> 'roles'
                    FROM mtg_rbac_bridge.rollback_record WHERE singleton) ? r.id::text)
    """, "unexpected role identity created during R2b")
    _assert_zero(bind, """
        SELECT count(*) FROM public.roles WHERE is_builtin AND permissions <> '{}'::jsonb
    """, "builtin role acquired legacy grants")
    builtin_count = bind.execute(sa.text("""
        SELECT count(*) FROM public.roles
        WHERE id IN ('00000000-0000-0000-0000-000000000005'::uuid,
                     '00000000-0000-0000-0000-000000000006'::uuid,
                     '00000000-0000-0000-0000-000000000007'::uuid)
          AND is_builtin
    """)).scalar_one()
    if builtin_count != 3:
        raise RuntimeError("MTG device-grant bridge: fixed builtin role identities differ")
    _assert_zero(bind, """
        SELECT count(*) FROM public.user_role_boundaries b
        LEFT JOIN public.user_roles ur ON ur.user_id = b.user_id AND ur.role_id = b.role_id
        LEFT JOIN public.users u ON u.id = b.user_id
        WHERE ur.user_id IS NULL OR u.id IS NULL OR u.organization_id IS NULL
          OR b.kind <> 'organization' OR b.organization_id IS DISTINCT FROM u.organization_id
    """, "unexpected or foreign assignment boundaries")

    seeded = {(USER_ID, permission) for permission in USER_SEED}
    seeded |= {(OPERATOR_ID, permission) for permission in OPERATOR_SEED}
    existing = set(bind.execute(sa.text(
        "SELECT role_id::text, permission FROM public.role_permissions"
    )).tuples())
    if existing != seeded:
        raise RuntimeError("MTG device-grant bridge: R2b seed permission set differs")

    created_permissions: list[list[str]] = []
    for key, permission in LEGACY_PERMISSION_MAP.items():
        rows = bind.execute(sa.text("""
            INSERT INTO public.role_permissions (role_id, permission)
            SELECT id, :permission FROM public.roles
            WHERE permissions -> :key = 'true'::jsonb
            RETURNING role_id::text, permission
        """), {"permission": permission, "key": key}).tuples()
        created_permissions.extend([role_id, grant] for role_id, grant in rows)

    # Only an assignment with NO existing boundary is proven missing. Any
    # non-home, platform or managed-org boundary was rejected above.
    repaired_boundaries = bind.execute(sa.text("""
        INSERT INTO public.user_role_boundaries (id, user_id, role_id, kind, organization_id)
        SELECT gen_random_uuid(), ur.user_id, ur.role_id, 'organization', u.organization_id
        FROM public.user_roles ur JOIN public.users u ON u.id = ur.user_id
        WHERE u.organization_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM public.user_role_boundaries b
            WHERE b.user_id = ur.user_id AND b.role_id = ur.role_id)
        RETURNING id::text
    """)).scalars().all()
    _assert_zero(bind, """
        SELECT count(*) FROM (
          (SELECT user_id, role_id, kind, organization_id FROM public.user_role_boundaries
           EXCEPT SELECT ur.user_id, ur.role_id, 'organization', u.organization_id
             FROM public.user_roles ur JOIN public.users u ON u.id = ur.user_id
             WHERE u.organization_id IS NOT NULL)
          UNION ALL
          (SELECT ur.user_id, ur.role_id, 'organization', u.organization_id
             FROM public.user_roles ur JOIN public.users u ON u.id = ur.user_id
             WHERE u.organization_id IS NOT NULL
           EXCEPT SELECT user_id, role_id, kind, organization_id FROM public.user_role_boundaries)
        ) differences
    """, "boundary sets differ from exact home-organization assignments")
    actual = set(bind.execute(sa.text(
        "SELECT role_id::text, permission FROM public.role_permissions"
    )).tuples())
    if actual != _expected_permissions(bind):
        raise RuntimeError("MTG device-grant bridge: exact permission-set reconciliation failed")
    valid = bind.execute(sa.text("""
        SELECT original_state = mtg_rbac_bridge.legacy_state()
        FROM mtg_rbac_bridge.rollback_record WHERE singleton
    """)).scalar_one()
    if not valid:
        raise RuntimeError("MTG device-grant bridge changed original JSON, membership or knowledge state")

    bind.execute(sa.text("""
        CREATE FUNCTION mtg_rbac_bridge.row_state() RETURNS jsonb
        LANGUAGE sql STABLE SET search_path = pg_catalog AS $function$
          SELECT jsonb_build_object(
            'legacy', mtg_rbac_bridge.legacy_state(),
            'roles', (SELECT encode(sha256(convert_to(coalesce(
                jsonb_agg(to_jsonb(r) ORDER BY id), '[]'::jsonb)::text, 'UTF8')), 'hex')
                FROM public.roles r),
            'bases', (SELECT encode(sha256(convert_to(coalesce(
                jsonb_agg(jsonb_build_array(id, base_role_id) ORDER BY id), '[]'::jsonb)::text,
                'UTF8')), 'hex') FROM public.users),
            'permissions', (SELECT encode(sha256(convert_to(coalesce(
                jsonb_agg(to_jsonb(p) ORDER BY role_id, permission), '[]'::jsonb)::text, 'UTF8')), 'hex')
                FROM public.role_permissions p),
            'boundaries', (SELECT encode(sha256(convert_to(coalesce(
                jsonb_agg(to_jsonb(b) ORDER BY id), '[]'::jsonb)::text, 'UTF8')), 'hex')
                FROM public.user_role_boundaries b)
          )
        $function$
    """))
    bind.execute(sa.text("REVOKE ALL ON FUNCTION mtg_rbac_bridge.row_state() FROM PUBLIC"))
    bind.execute(sa.text("""
        UPDATE mtg_rbac_bridge.rollback_record
        SET bridged_state = mtg_rbac_bridge.row_state(),
            created_permission_rows = CAST(:permissions AS jsonb),
            created_boundary_rows = CAST(:boundaries AS jsonb)
        WHERE singleton
    """), {
        "permissions": json.dumps(sorted(created_permissions)),
        "boundaries": json.dumps(sorted(repaired_boundaries)),
    })
    bind.execute(sa.text("""
        CREATE FUNCTION mtg_rbac_bridge.reject_archive_write() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $function$
        BEGIN
          RAISE EXCEPTION 'MTG RBAC rollback record is migration-owned and immutable';
        END
        $function$
    """))
    bind.execute(sa.text("REVOKE ALL ON FUNCTION mtg_rbac_bridge.reject_archive_write() FROM PUBLIC"))
    bind.execute(sa.text("""
        CREATE TRIGGER mtg_rbac_rollback_immutable
        BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON mtg_rbac_bridge.rollback_record
        FOR EACH STATEMENT EXECUTE FUNCTION mtg_rbac_bridge.reject_archive_write()
    """))
    bind.execute(sa.text("""
        CREATE FUNCTION mtg_rbac_bridge.freeze_legacy_permissions() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $function$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF NEW.permissions IS DISTINCT FROM '{}'::jsonb THEN
              RAISE EXCEPTION 'Legacy role permission JSON is inert; use typed permission grants';
            END IF;
          ELSIF NEW.permissions IS DISTINCT FROM OLD.permissions THEN
            RAISE EXCEPTION 'Legacy role permission JSON is frozen for the rollback window';
          END IF;
          RETURN NEW;
        END
        $function$
    """))
    bind.execute(sa.text("REVOKE ALL ON FUNCTION mtg_rbac_bridge.freeze_legacy_permissions() FROM PUBLIC"))
    bind.execute(sa.text("""
        CREATE TRIGGER mtg_rbac_legacy_permissions_frozen
        BEFORE INSERT OR UPDATE OF permissions ON public.roles
        FOR EACH ROW EXECUTE FUNCTION mtg_rbac_bridge.freeze_legacy_permissions()
    """))
    # A current-state fingerprint alone misses revoke+regrant or a
    # delete+restore with identical values. nextval is deliberately not
    # transactional: once the migrated schema is used to change authority,
    # even an aborted attempt conservatively closes snapshot rollback.
    bind.execute(sa.text("CREATE SEQUENCE mtg_rbac_bridge.authority_change_epoch"))
    bind.execute(sa.text("REVOKE ALL ON SEQUENCE mtg_rbac_bridge.authority_change_epoch FROM PUBLIC"))
    bind.execute(sa.text("""
        CREATE FUNCTION mtg_rbac_bridge.mark_authority_change() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $function$
        BEGIN
          PERFORM nextval('mtg_rbac_bridge.authority_change_epoch'::regclass);
          RETURN NULL;
        END
        $function$
    """))
    bind.execute(sa.text("REVOKE ALL ON FUNCTION mtg_rbac_bridge.mark_authority_change() FROM PUBLIC"))
    for table in ("roles", "user_roles", "role_permissions", "user_role_boundaries", "knowledge_namespace_roles"):
        bind.execute(sa.text(
            f"CREATE TRIGGER mtg_rbac_authority_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE "
            f"ON public.{table} FOR EACH STATEMENT "
            "EXECUTE FUNCTION mtg_rbac_bridge.mark_authority_change()"
        ))
    bind.execute(sa.text("""
        CREATE TRIGGER mtg_rbac_authority_changed
        AFTER INSERT OR DELETE OR UPDATE OF organization_id, is_superuser, base_role_id ON public.users
        FOR EACH STATEMENT EXECUTE FUNCTION mtg_rbac_bridge.mark_authority_change()
    """))
    bind.execute(sa.text("""
        CREATE TRIGGER mtg_rbac_authority_truncated AFTER TRUNCATE ON public.users
        FOR EACH STATEMENT EXECUTE FUNCTION mtg_rbac_bridge.mark_authority_change()
    """))
    _assert_zero(bind, """
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
    """, "rollback archive ACL is not limited to its migration owner")


def downgrade() -> None:
    bind = op.get_bind()
    _fence(bind)
    valid = bind.execute(sa.text("""
        SELECT contract_version = 1 AND migration_owner = current_user
          AND bridged_state IS NOT NULL
          AND bridged_state = mtg_rbac_bridge.row_state()
          AND original_state = mtg_rbac_bridge.legacy_state()
          AND NOT (SELECT is_called FROM mtg_rbac_bridge.authority_change_epoch)
        FROM mtg_rbac_bridge.rollback_record WHERE singleton
    """)).scalar_one()
    if not valid:
        raise RuntimeError(
            "MTG RBAC rollback refused: grant, revocation, membership, home-org, "
            "role or knowledge state changed; restore a consistent backup with matching code"
        )
    for table in ("roles", "user_roles", "role_permissions", "user_role_boundaries", "knowledge_namespace_roles", "users"):
        bind.execute(sa.text(f"DROP TRIGGER mtg_rbac_authority_changed ON public.{table}"))
    bind.execute(sa.text("DROP TRIGGER mtg_rbac_authority_truncated ON public.users"))
    bind.execute(sa.text("DROP FUNCTION mtg_rbac_bridge.mark_authority_change()"))
    bind.execute(sa.text("DROP SEQUENCE mtg_rbac_bridge.authority_change_epoch"))
    # Only migration-created rows are removed. Never repopulate permission
    # rows from stale JSON or overwrite a post-cutover grant/revocation.
    bind.execute(sa.text("""
        DELETE FROM public.role_permissions p USING mtg_rbac_bridge.rollback_record r
        WHERE r.singleton AND EXISTS (
          SELECT 1 FROM jsonb_array_elements(r.created_permission_rows) e
          WHERE p.role_id = (e ->> 0)::uuid AND p.permission = e ->> 1)
    """))
    bind.execute(sa.text("""
        DELETE FROM public.user_role_boundaries b USING mtg_rbac_bridge.rollback_record r
        WHERE r.singleton AND b.id IN (
            SELECT value::uuid FROM jsonb_array_elements_text(r.created_boundary_rows))
    """))
    bind.execute(sa.text("DROP TRIGGER mtg_rbac_legacy_permissions_frozen ON public.roles"))
    bind.execute(sa.text("DROP FUNCTION mtg_rbac_bridge.freeze_legacy_permissions()"))
    bind.execute(sa.text("DROP TRIGGER mtg_rbac_rollback_immutable ON mtg_rbac_bridge.rollback_record"))
    bind.execute(sa.text("DROP FUNCTION mtg_rbac_bridge.reject_archive_write()"))
    bind.execute(sa.text("""
        UPDATE mtg_rbac_bridge.rollback_record SET bridged_state = NULL,
          created_permission_rows = NULL, created_boundary_rows = NULL WHERE singleton
    """))
    bind.execute(sa.text("DROP FUNCTION mtg_rbac_bridge.row_state()"))
