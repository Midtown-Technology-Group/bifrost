"""Authenticate common writers and fence source admission — REVIEW ONLY, C5-B2.

Not active history or a complete C5 installation. No role grants, issuer,
runtime/profile admission or physical-retirement inference. C1-C4 retain exact
identity/immutability constraints; B1 retains atomic close/grant enforcement.
Native source-obligation inventory and destructive-drain policy remain required.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261016_runtime_common_writes"
down_revision = "20261016_runtime_close_grants"
branch_labels = None
depends_on = None

TABLES = (
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


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF pg_catalog.current_setting('transaction_isolation') <> 'read committed' THEN
                RAISE EXCEPTION 'common writer upgrade requires read committed';
            END IF;
        END $$
    """)
    op.execute(
        "LOCK TABLE "
        + ", ".join(f"public.{table}" for table in TABLES)
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute(
        "DO $$ BEGIN IF "
        + " OR ".join(f"EXISTS (SELECT 1 FROM public.{table})" for table in TABLES)
        + " THEN RAISE EXCEPTION 'common writer upgrade requires empty stores'; END IF; END $$"
    )
    _install_guard()
    for table in TABLES:
        # Sort before B1's runtime_close_grant_writer: source fence precedes
        # execution fence even when the caller omitted explicit preparation.
        op.execute(f"""
            CREATE TRIGGER runtime_00_common_writer BEFORE INSERT OR UPDATE OR DELETE
            ON public.{table} FOR EACH ROW
            EXECUTE FUNCTION public.guard_runtime_common_writer()
        """)
    op.execute(
        "ALTER FUNCTION public.guard_runtime_common_writer() OWNER TO runtime_lifecycle_guard"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.guard_runtime_common_writer() FROM PUBLIC"
    )


def _install_guard() -> None:
    op.execute("""
        CREATE FUNCTION public.guard_runtime_common_writer() RETURNS trigger
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog,pg_temp AS $$
        DECLARE actor text; proposed jsonb; previous jsonb; target_execution uuid;
            target_session uuid; target_grant uuid; detach_fields text[];
        BEGIN
            IF TG_TABLE_SCHEMA <> 'public' OR TG_TABLE_NAME NOT IN
                ('runtime_deployment_artifacts','runtime_execution_owners','runtime_sessions',
                 'runtime_workflow_live_links','runtime_starts','runtime_report_receipts',
                 'workflow_runtime_sdk_grants','workflow_runtime_sdk_grant_operations',
                 'runtime_admissions','runtime_resource_settlements') THEN
                RAISE EXCEPTION 'unknown common writer relation' USING ERRCODE='42501';
            END IF;
            actor := public.runtime_workflow_actor();
            IF TG_OP='DELETE' THEN
                RAISE EXCEPTION 'retain common runtime evidence' USING ERRCODE='42501';
            END IF;
            -- Serialize with the existing exclusive workspace source/accounting
            -- fence. Try only: trigger entry may follow an implicit row lock.
            IF NOT pg_try_advisory_xact_lock_shared(hashtext('bifrost:workspace-release')) THEN
                RAISE EXCEPTION 'common source admission contended' USING ERRCODE='55P03';
            END IF;
            proposed := to_jsonb(NEW);
            IF TG_OP='UPDATE' THEN previous:=to_jsonb(OLD); END IF;
            IF actor='python' THEN
                IF TG_OP <> 'UPDATE' THEN
                    RAISE EXCEPTION 'incumbent cannot create common authority' USING ERRCODE='42501';
                END IF;
                detach_fields := CASE TG_TABLE_NAME
                    WHEN 'runtime_deployment_artifacts' THEN
                        ARRAY['live_deployment_id','live_workflow_id','live_solution_id','live_reviewed_by']
                    WHEN 'runtime_execution_owners' THEN ARRAY['live_execution_id']
                    WHEN 'runtime_workflow_live_links' THEN ARRAY['native_attempt_id'] END;
                IF detach_fields IS NULL OR proposed IS NOT DISTINCT FROM previous OR
                   (proposed-detach_fields) IS DISTINCT FROM (previous-detach_fields) THEN
                    RAISE EXCEPTION 'incumbent common mutation denied' USING ERRCODE='42501';
                END IF;
                -- C1's still-enabled identity guards additionally require exact
                -- old-value -> NULL and absence of the actual native parent.
                -- This is not a blanket incumbent UPDATE exemption.
            END IF;
            IF TG_TABLE_NAME='runtime_deployment_artifacts' THEN
                RETURN NEW; -- no execution exists at reviewed artifact admission.
            ELSIF TG_TABLE_NAME IN ('runtime_report_receipts','runtime_admissions') THEN
                target_session := (proposed->>'session_id')::uuid;
                SELECT s.execution_id INTO target_execution FROM public.runtime_sessions s
                    WHERE s.id=target_session;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'common session parent unavailable' USING ERRCODE='42501';
                END IF;
            ELSIF TG_TABLE_NAME='workflow_runtime_sdk_grant_operations' THEN
                target_grant := (proposed->>'grant_id')::uuid;
                SELECT g.execution_id INTO target_execution FROM public.workflow_runtime_sdk_grants g
                    WHERE g.id=target_grant;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'common grant parent unavailable' USING ERRCODE='42501';
                END IF;
            ELSE target_execution := (proposed->>'execution_id')::uuid;
            END IF;
            IF target_execution IS NULL OR NOT pg_try_advisory_xact_lock(
                hashtext('bifrost:workflow-execution:' || target_execution::text)) THEN
                RAISE EXCEPTION 'common execution fence unavailable' USING ERRCODE='55P03';
            END IF;
            IF TG_OP='INSERT' AND TG_TABLE_NAME='runtime_sessions' THEN
                PERFORM 1 FROM public.executions e
                    JOIN public.runtime_execution_owners o ON o.live_execution_id=e.id
                    WHERE e.id=target_execution AND e.control_owner='rust'
                        AND e.status::text IN ('Pending','Running')
                        AND o.execution_id=target_execution;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'new session cannot revive terminal/cancelling work' USING ERRCODE='42501';
                END IF;
            END IF;
            -- Parent keys never change or disappear under C1-C4. Shared source
            -- and native execution fences now protect the fresh eligibility read.
            IF TG_OP='INSERT' AND TG_TABLE_NAME IN ('runtime_starts','runtime_admissions') THEN
                target_session := (proposed->>'session_id')::uuid;
                PERFORM 1 FROM public.runtime_sessions s
                    JOIN public.runtime_execution_owners o ON o.execution_id=s.execution_id
                        AND o.owner_incarnation_id=s.owner_incarnation_id
                    JOIN public.runtime_workflow_live_links l ON l.session_id=s.id
                    JOIN public.workflow_execution_attempts a ON a.id=l.native_attempt_id
                    JOIN public.executions e ON e.id=o.live_execution_id
                    WHERE s.id=target_session AND s.execution_id=target_execution
                        AND s.closed_at IS NULL AND o.live_execution_id=o.execution_id
                        AND e.control_owner='rust' AND e.status::text='Running' AND e.runtime_mode='deployment-v1'
                        AND e.workflow_id=o.workflow_id AND e.solution_deployment_id=o.deployment_id
                        AND l.native_attempt_id=s.workflow_attempt_id AND a.execution_id=s.execution_id
                        AND a.claim_token=s.claim_token AND a.worker_incarnation_id=s.worker_incarnation_id
                        AND a.status='running' AND a.phase='execution' AND a.completed_at IS NULL;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'common Start/admission needs open current live fence' USING ERRCODE='42501';
                END IF;
                IF TG_TABLE_NAME='runtime_starts' THEN
                    PERFORM 1 FROM public.workflow_execution_attempts a
                        WHERE a.id=NEW.workflow_attempt_id AND a.started_at=NEW.started_at;
                    IF NOT FOUND THEN
                        RAISE EXCEPTION 'common Start clock differs' USING ERRCODE='42501';
                    END IF;
                ELSE
                    PERFORM 1 FROM public.workflow_runtime_sdk_grants g
                        WHERE g.id=NEW.grant_id AND g.runtime_session_id=target_session
                            AND g.revoked_at IS NULL;
                    IF NOT FOUND THEN
                        RAISE EXCEPTION 'common provision/release grant unavailable' USING ERRCODE='42501';
                    END IF;
                END IF;
            END IF;
            RETURN NEW;
        END $$
    """)


def downgrade() -> None:
    bind = op.get_bind()
    if (
        bind.execute(sa.text("SHOW transaction_isolation")).scalar_one()
        != "read committed"
    ):
        raise RuntimeError("Common writer downgrade requires read committed isolation")
    bind.execute(
        sa.text(
            "LOCK TABLE "
            + ", ".join(f"public.{table}" for table in TABLES)
            + " IN ACCESS EXCLUSIVE MODE NOWAIT"
        )
    )
    if bind.execute(
        sa.text(
            "SELECT "
            + " OR ".join(f"EXISTS (SELECT 1 FROM public.{table})" for table in TABLES)
        )
    ).scalar_one():
        raise RuntimeError("Retain common writer guards while evidence remains")
    for table in TABLES:
        op.execute(f"DROP TRIGGER runtime_00_common_writer ON public.{table}")
    op.execute("DROP FUNCTION public.guard_runtime_common_writer()")
