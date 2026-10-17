"""Serialize common session close and grant writes — REVIEW ONLY, C5-B1.

Not active Alembic history. Complements, but does not complete, C5-A. This
enforces permanent close versus late first grant and atomic existing-grant
revocation. It neither issues credentials nor replaces CRED policy/admission.
Remaining common writers, source drain, finite principals, actual concurrency
proof and payload disposition must be reviewed before any application.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261016_runtime_close_grants"
down_revision = "20261016_runtime_writers"
branch_labels = None
depends_on = None

TABLES = (
    "runtime_sessions",
    "workflow_runtime_sdk_grants",
    "workflow_runtime_sdk_grant_operations",
)
FUNCTIONS = ("guard_runtime_close_grant_writer", "require_runtime_closed_grants")


def upgrade() -> None:
    # C5-A's empty-store application boundary must remain intact for this
    # continuation. No partial installation over already enabled owners.
    op.execute("""
        DO $$ BEGIN
            IF pg_catalog.current_setting('transaction_isolation') <> 'read committed' THEN
                RAISE EXCEPTION 'close/grant upgrade requires read committed';
            END IF;
        END $$
    """)
    op.execute(
        "LOCK TABLE public.executions, public.runtime_execution_owners, "
        + ", ".join(f"public.{table}" for table in TABLES)
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.runtime_execution_owners) OR
               EXISTS (SELECT 1 FROM public.runtime_sessions) OR
               EXISTS (SELECT 1 FROM public.workflow_runtime_sdk_grants) OR
               EXISTS (SELECT 1 FROM public.workflow_runtime_sdk_grant_operations) THEN
                RAISE EXCEPTION 'close/grant upgrade requires empty common authority';
            END IF;
        END $$
    """)
    _install_writer()
    _install_commit_check()
    for table in TABLES:
        op.execute(f"""
            CREATE TRIGGER runtime_close_grant_writer BEFORE INSERT OR UPDATE OR DELETE
            ON public.{table} FOR EACH ROW
            EXECUTE FUNCTION public.guard_runtime_close_grant_writer()
        """)
    for table in ("runtime_sessions", "workflow_runtime_sdk_grants"):
        op.execute(f"""
            CREATE CONSTRAINT TRIGGER runtime_closed_grants AFTER INSERT OR UPDATE
            ON public.{table} DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION public.require_runtime_closed_grants()
        """)
    for function in FUNCTIONS:
        op.execute(
            f"ALTER FUNCTION public.{function}() OWNER TO runtime_lifecycle_guard"
        )
        op.execute(f"REVOKE ALL ON FUNCTION public.{function}() FROM PUBLIC")


def _install_writer() -> None:
    op.execute("""
        CREATE FUNCTION public.guard_runtime_close_grant_writer() RETURNS trigger
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog,pg_temp AS $$
        DECLARE target_session uuid; target_execution uuid; target_grant uuid;
            current_session public.runtime_sessions%ROWTYPE;
            grant_revoked timestamptz;
        BEGIN
            IF TG_TABLE_SCHEMA <> 'public' OR TG_TABLE_NAME NOT IN
                ('runtime_sessions','workflow_runtime_sdk_grants','workflow_runtime_sdk_grant_operations') OR
               public.runtime_workflow_actor() <> 'rust' THEN
                RAISE EXCEPTION 'common session/grant writer denied' USING ERRCODE='42501';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'retain common session/grant authority' USING ERRCODE='42501';
            END IF;
            IF TG_TABLE_NAME = 'runtime_sessions' THEN
                target_session := NEW.id;
                target_execution := NEW.execution_id;
            ELSIF TG_TABLE_NAME = 'workflow_runtime_sdk_grants' THEN
                target_session := NEW.runtime_session_id;
                target_execution := NEW.execution_id;
            ELSE
                target_grant := NEW.grant_id;
                SELECT g.runtime_session_id,g.execution_id INTO target_session,target_execution
                    FROM public.workflow_runtime_sdk_grants g WHERE g.id=target_grant;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'common operation grant unavailable' USING ERRCODE='42501';
                END IF;
            END IF;
            -- Try only: this BEFORE trigger may already hold a later native or
            -- grant row lock. Never wait here or silently retry a possible effect.
            IF NOT pg_try_advisory_xact_lock(hashtext('bifrost:workflow-execution:' || target_execution::text)) THEN
                RAISE EXCEPTION 'common session/grant execution contended' USING ERRCODE='55P03';
            END IF;
            -- Plain reads keep the guard custodian read-only. All session/grant
            -- mutations share the execution fence; immutable parent identities
            -- cannot be deleted/replaced while resolving an operation's parent.
            PERFORM 1 FROM public.runtime_execution_owners o
                WHERE o.execution_id=target_execution;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'common session owner unavailable' USING ERRCODE='42501';
            END IF;
            IF TG_TABLE_NAME='runtime_sessions' AND TG_OP='INSERT' THEN
                PERFORM 1 FROM public.executions e
                    WHERE e.id=target_execution AND e.control_owner='rust';
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'new common session needs Rust execution' USING ERRCODE='42501';
                END IF;
                RETURN NEW; -- C1 validates exact identity/fence after generation.
            END IF;
            SELECT s.* INTO current_session FROM public.runtime_sessions s
                WHERE s.id=target_session AND s.execution_id=target_execution;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'common session binding unavailable' USING ERRCODE='42501';
            END IF;
            IF TG_TABLE_NAME='workflow_runtime_sdk_grants' AND TG_OP='INSERT' THEN
                IF current_session.closed_at IS NOT NULL OR NEW.revoked_at IS NOT NULL OR
                   NEW.revocation_reason IS NOT NULL THEN
                    RAISE EXCEPTION 'closed session cannot issue a grant' USING ERRCODE='42501';
                END IF;
            ELSIF TG_TABLE_NAME='workflow_runtime_sdk_grant_operations' THEN
                SELECT g.revoked_at INTO grant_revoked FROM public.workflow_runtime_sdk_grants g
                    WHERE g.id=target_grant AND g.runtime_session_id=target_session AND g.execution_id=target_execution;
                IF NOT FOUND OR current_session.closed_at IS NOT NULL OR grant_revoked IS NOT NULL THEN
                    RAISE EXCEPTION 'closed/revoked grant cannot accept operations' USING ERRCODE='42501';
                END IF;
            END IF;
            -- C1/C3 retain identity, monotonic close/revocation and operation
            -- immutability. A grant may be revoked after permanent session close;
            -- the deferred check requires both facts before COMMIT succeeds.
            RETURN NEW;
        END $$
    """)


def _install_commit_check() -> None:
    op.execute("""
        CREATE FUNCTION public.require_runtime_closed_grants() RETURNS trigger
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog,pg_temp AS $$
        DECLARE target_session uuid; target_execution uuid;
        BEGIN
            IF TG_TABLE_SCHEMA <> 'public' OR TG_TABLE_NAME NOT IN
                ('runtime_sessions','workflow_runtime_sdk_grants') OR
               public.runtime_workflow_actor() <> 'rust' THEN
                RAISE EXCEPTION 'common close constraint context denied' USING ERRCODE='42501';
            END IF;
            IF TG_TABLE_NAME='runtime_sessions' THEN target_session:=NEW.id;
            ELSE target_session:=NEW.runtime_session_id; END IF;
            SELECT s.execution_id INTO target_execution FROM public.runtime_sessions s
                WHERE s.id=target_session;
            IF NOT FOUND OR NOT pg_try_advisory_xact_lock(
                hashtext('bifrost:workflow-execution:' || target_execution::text)) THEN
                RAISE EXCEPTION 'common close constraint fence unavailable' USING ERRCODE='42501';
            END IF;
            -- Re-read final rows, not the triggering event's possibly open NEW
            -- image. Close-then-revoke and revoke-then-close are both atomic.
            IF EXISTS (SELECT 1 FROM public.runtime_sessions s
                JOIN public.workflow_runtime_sdk_grants g ON g.runtime_session_id=s.id
                WHERE s.id=target_session AND s.closed_at IS NOT NULL AND g.revoked_at IS NULL) THEN
                RAISE EXCEPTION 'closed session retains active SDK grant' USING ERRCODE='23514';
            END IF;
            RETURN NULL;
        END $$
    """)


def downgrade() -> None:
    bind = op.get_bind()
    if (
        bind.execute(sa.text("SHOW transaction_isolation")).scalar_one()
        != "read committed"
    ):
        raise RuntimeError("Close/grant downgrade requires read committed isolation")
    bind.execute(
        sa.text(
            "LOCK TABLE public.executions, public.runtime_execution_owners, "
            + ", ".join(f"public.{table}" for table in TABLES)
            + " IN ACCESS EXCLUSIVE MODE NOWAIT"
        )
    )
    if bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM public.runtime_execution_owners) OR "
            + " OR ".join(f"EXISTS (SELECT 1 FROM public.{table})" for table in TABLES)
        )
    ).scalar_one():
        raise RuntimeError(
            "Retain common close/grant enforcement while evidence remains"
        )
    for table in ("runtime_sessions", "workflow_runtime_sdk_grants"):
        op.execute(f"DROP TRIGGER runtime_closed_grants ON public.{table}")
    for table in TABLES:
        op.execute(f"DROP TRIGGER runtime_close_grant_writer ON public.{table}")
    for function in reversed(FUNCTIONS):
        op.execute(f"DROP FUNCTION public.{function}()")
