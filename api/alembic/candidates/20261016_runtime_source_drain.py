"""Retained common source obligations and destructive drain — REVIEW ONLY, B3.

No application or role grants. Physical settlement is usable only after its
original trusted observer/custody path is qualified. No-session owners stay
pinned; terminal status or missing history cannot manufacture resource proof.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261016_runtime_source_drain"
down_revision = "20261016_runtime_common_writes"
branch_labels = None
depends_on = None

TABLES = (
    "solutions",
    "solution_deployments",
    "executions",
    "workflow_execution_attempts",
)
COMMON_TABLES = (
    "runtime_deployment_artifacts",
    "runtime_execution_owners",
    "runtime_sessions",
    "runtime_workflow_live_links",
    "runtime_starts",
    "runtime_report_receipts",
    "workflow_runtime_sdk_grants",
    "workflow_runtime_sdk_grant_operations",
    "runtime_admissions",
    "runtime_resource_settlements",
)
FUNCTIONS = (
    "runtime_owner_undrained(uuid)",
    "runtime_undrained_sources()",
    "guard_runtime_source_drain()",
)


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF pg_catalog.current_setting('transaction_isolation') <> 'read committed' THEN
                RAISE EXCEPTION 'source drain upgrade requires read committed';
            END IF;
        END $$
    """)
    op.execute(
        "LOCK TABLE "
        + ", ".join(f"public.{t}" for t in (*TABLES, *COMMON_TABLES))
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute(
        "DO $$ BEGIN IF "
        + " OR ".join(f"EXISTS (SELECT 1 FROM public.{t})" for t in COMMON_TABLES)
        + " THEN RAISE EXCEPTION 'source drain upgrade requires empty common stores'; END IF; END $$"
    )
    _install_obligations()
    _install_drain_guard()
    for table in TABLES:
        operations = (
            "UPDATE OR DELETE"
            if table in {"solutions", "solution_deployments"}
            else "DELETE"
        )
        op.execute(f"""
            CREATE TRIGGER runtime_00_source_drain BEFORE {operations}
            ON public.{table} FOR EACH ROW
            EXECUTE FUNCTION public.guard_runtime_source_drain()
        """)
    for signature in FUNCTIONS:
        op.execute(
            f"ALTER FUNCTION public.{signature} OWNER TO runtime_lifecycle_guard"
        )
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")


def _install_obligations() -> None:
    op.execute("""
        CREATE FUNCTION public.runtime_owner_undrained(target_execution uuid) RETURNS boolean
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog,pg_temp AS $$
        BEGIN
            PERFORM public.runtime_workflow_actor();
            IF NOT EXISTS (SELECT 1 FROM public.runtime_execution_owners o
                WHERE o.execution_id=target_execution) THEN RETURN FALSE; END IF;
            RETURN
                NOT EXISTS (SELECT 1 FROM public.runtime_sessions s WHERE s.execution_id=target_execution) OR
                EXISTS (SELECT 1 FROM public.executions e WHERE e.id=target_execution
                    AND e.status::text NOT IN ('Success','Failed','Timeout','Cancelled','CompletedWithErrors')) OR
                EXISTS (SELECT 1 FROM public.workflow_execution_attempts a
                    WHERE a.execution_id=target_execution AND a.completed_at IS NULL) OR
                EXISTS (SELECT 1 FROM public.execution_attempts a
                    WHERE a.logical_job_type IN ('workflow','workflow_execution')
                        AND a.logical_job_id=target_execution AND a.completed_at IS NULL) OR
                EXISTS (SELECT 1 FROM public.runtime_sessions s WHERE s.execution_id=target_execution
                    AND (s.closed_at IS NULL OR NOT EXISTS (
                        SELECT 1 FROM public.runtime_resource_settlements r WHERE r.session_id=s.id)));
        END $$
    """)
    op.execute("""
        CREATE FUNCTION public.runtime_undrained_sources()
        RETURNS TABLE (execution_id uuid, deployment_id uuid)
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog,pg_temp AS $$
        BEGIN
            PERFORM public.runtime_workflow_actor();
            -- The existing accounting collector owns this exclusive fence.
            -- Re-enter it without waiting so direct reads cannot miss admission.
            IF NOT pg_try_advisory_xact_lock(hashtext('bifrost:workspace-release')) THEN
                RAISE EXCEPTION 'common source inventory contended' USING ERRCODE='55P03';
            END IF;
            RETURN QUERY SELECT o.execution_id,o.deployment_id
                FROM public.runtime_execution_owners o
                WHERE public.runtime_owner_undrained(o.execution_id)
                ORDER BY o.deployment_id,o.execution_id;
        END $$
    """)


def _install_drain_guard() -> None:
    op.execute("""
        CREATE FUNCTION public.guard_runtime_source_drain() RETURNS trigger
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog,pg_temp AS $$
        DECLARE actor text; target_execution uuid; pinned boolean;
        BEGIN
            IF TG_TABLE_SCHEMA <> 'public' OR TG_TABLE_NAME NOT IN
                ('solutions','solution_deployments','executions','workflow_execution_attempts') THEN
                RAISE EXCEPTION 'unknown source drain relation' USING ERRCODE='42501';
            END IF;
            actor:=public.runtime_workflow_actor();
            IF TG_TABLE_NAME IN ('solutions','solution_deployments') THEN
                IF actor='rust' THEN
                    RAISE EXCEPTION 'coordinator cannot mutate native source' USING ERRCODE='42501';
                END IF;
                IF TG_OP='UPDATE' THEN
                    -- Preserve status-only uninstall and normal activation/
                    -- metadata updates. Native immutable-deployment guards stay.
                    RETURN NEW;
                END IF;
                IF NOT pg_try_advisory_xact_lock(hashtext('bifrost:workspace-release')) THEN
                    RAISE EXCEPTION 'source removal contended' USING ERRCODE='55P03';
                END IF;
                IF TG_TABLE_NAME='solution_deployments' THEN
                    SELECT EXISTS (SELECT 1 FROM public.runtime_execution_owners o
                        WHERE o.deployment_id=OLD.id AND public.runtime_owner_undrained(o.execution_id)) INTO pinned;
                ELSE
                    SELECT EXISTS (SELECT 1 FROM public.runtime_execution_owners o
                        JOIN public.runtime_deployment_artifacts a
                            ON a.deployment_id=o.deployment_id AND a.workflow_id=o.workflow_id
                        WHERE a.solution_id=OLD.id AND public.runtime_owner_undrained(o.execution_id)) INTO pinned;
                END IF;
                IF pinned THEN
                    RAISE EXCEPTION 'runtime source has undrained obligations' USING ERRCODE='23503';
                END IF;
            ELSE
                IF TG_TABLE_NAME='executions' THEN target_execution:=OLD.id;
                ELSE target_execution:=OLD.execution_id; END IF;
                IF EXISTS (SELECT 1 FROM public.runtime_execution_owners o WHERE o.execution_id=target_execution) THEN
                    IF actor <> 'rust' THEN
                        RAISE EXCEPTION 'foreign native retirement writer' USING ERRCODE='42501';
                    END IF;
                    IF NOT pg_try_advisory_xact_lock_shared(hashtext('bifrost:workspace-release')) OR
                       NOT pg_try_advisory_xact_lock(hashtext('bifrost:workflow-execution:' || target_execution::text)) THEN
                        RAISE EXCEPTION 'native retirement contended' USING ERRCODE='55P03';
                    END IF;
                    IF public.runtime_owner_undrained(target_execution) THEN
                        RAISE EXCEPTION 'retain native history until runtime drain' USING ERRCODE='23503';
                    END IF;
                END IF;
            END IF;
            RETURN OLD;
        END $$
    """)


def downgrade() -> None:
    bind = op.get_bind()
    if (
        bind.execute(sa.text("SHOW transaction_isolation")).scalar_one()
        != "read committed"
    ):
        raise RuntimeError("Source drain downgrade requires read committed isolation")
    bind.execute(
        sa.text(
            "LOCK TABLE "
            + ", ".join(f"public.{t}" for t in (*TABLES, *COMMON_TABLES))
            + " IN ACCESS EXCLUSIVE MODE NOWAIT"
        )
    )
    if bind.execute(
        sa.text(
            "SELECT "
            + " OR ".join(f"EXISTS (SELECT 1 FROM public.{t})" for t in COMMON_TABLES)
        )
    ).scalar_one():
        raise RuntimeError("Retain source drain guards while common evidence remains")
    for table in TABLES:
        op.execute(f"DROP TRIGGER runtime_00_source_drain ON public.{table}")
    for signature in reversed(FUNCTIONS):
        op.execute(f"DROP FUNCTION public.{signature}")
