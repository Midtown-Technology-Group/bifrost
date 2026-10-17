"""Native workflow ownership enforcement — REVIEW ONLY, C5-A.

Outside active history. Requires complete C5 common admission/drain enforcement,
the reviewed restricted-principal profile and adapter lock changes before any
application. No roles, grants, runtime, credential or routing are enabled here.
The named logins are isolated-proof identities, not production provisioning.

One native root marker plus retained common history classifies descendants.
Existing native foreign keys/cascades and unrelated Python relationships remain
unchanged. Agent/multi-parent adoption and remaining ancestor deletion/drain
semantics require their separately reviewed complete continuation.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261016_runtime_writers"
down_revision = "20261016_runtime_resources"
branch_labels = None
depends_on = None

NATIVE_TABLES = (
    "executions",
    "workflow_execution_attempts",
    "execution_logs",
    "ai_usage",
    "event_deliveries",
    "execution_attempts",
    "execution_lifecycle_events",
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
    "runtime_workflow_actor",
    "guard_runtime_native_writer",
    "guard_runtime_owner_birth",
)


def upgrade() -> None:
    # Reject an unreviewed prototype/partially enabled store. The migration
    # owner, not a runtime login, excludes writers before the fresh snapshot.
    op.execute("""
        DO $$
        BEGIN
            IF pg_catalog.current_setting('transaction_isolation') <> 'read committed' THEN
                RAISE EXCEPTION 'native writer upgrade requires read committed';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='runtime_lifecycle_guard'
                  AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
                  AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication
            ) THEN
                RAISE EXCEPTION 'reviewed NOLOGIN guard custodian is required';
            END IF;
        END $$
    """)
    op.execute(
        "LOCK TABLE "
        + ", ".join(f"public.{table}" for table in (*NATIVE_TABLES, *COMMON_TABLES))
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute(
        "DO $$ BEGIN IF "
        + " OR ".join(
            f"EXISTS (SELECT 1 FROM public.{table})" for table in COMMON_TABLES
        )
        + " THEN RAISE EXCEPTION 'native writer upgrade requires empty common stores'; END IF; END $$"
    )
    op.add_column(
        "executions",
        sa.Column(
            "control_owner", sa.String(16), nullable=False, server_default="python"
        ),
        schema="public",
    )
    op.create_check_constraint(
        "ck_execution_control_owner",
        "executions",
        "control_owner IN ('python','rust')",
        schema="public",
    )
    op.add_column(
        "executions",
        sa.Column(
            "runtime_owner_id",
            sa.Uuid(),
            sa.Computed(
                "CASE WHEN control_owner = 'rust' THEN id ELSE NULL END", persisted=True
            ),
            nullable=True,
        ),
        schema="public",
    )
    op.create_foreign_key(
        "fk_execution_retained_runtime_owner",
        "executions",
        "runtime_execution_owners",
        ["runtime_owner_id"],
        ["execution_id"],
        source_schema="public",
        referent_schema="public",
        deferrable=True,
        initially="DEFERRED",
    )
    _install_actor()
    _install_native_guard()
    _install_owner_birth_guard()
    for function in FUNCTIONS:
        op.execute(
            f"ALTER FUNCTION public.{function}() OWNER TO runtime_lifecycle_guard"
        )
        op.execute(f"REVOKE ALL ON FUNCTION public.{function}() FROM PUBLIC")
    for table in NATIVE_TABLES:
        op.execute(f"""
            CREATE TRIGGER runtime_native_writer BEFORE INSERT OR UPDATE OR DELETE
            ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.guard_runtime_native_writer()
        """)
    op.execute("""
        CREATE TRIGGER runtime_owner_birth_writer BEFORE INSERT
        ON public.runtime_execution_owners FOR EACH ROW
        EXECUTE FUNCTION public.guard_runtime_owner_birth()
    """)


def _install_actor() -> None:
    op.execute("""
        CREATE FUNCTION public.runtime_workflow_actor() RETURNS text
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog,pg_temp AS $$
        DECLARE actor text; principal oid;
        BEGIN
            IF pg_catalog.current_setting('transaction_isolation') <> 'read committed' THEN
                RAISE EXCEPTION 'unqualified lifecycle snapshot isolation' USING ERRCODE='42501';
            END IF;
            actor := CASE session_user WHEN 'wex_core' THEN 'rust'
                WHEN 'wex_incumbent' THEN 'python' END;
            SELECT oid INTO principal FROM pg_catalog.pg_roles
                WHERE rolname=session_user AND NOT rolsuper AND NOT rolbypassrls
                  AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication;
            IF actor IS NULL OR principal IS NULL OR
               EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members WHERE member=principal) OR
               EXISTS (SELECT 1 FROM pg_catalog.pg_namespace
                       WHERE nspname='public' AND nspowner=principal) OR
               EXISTS (SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n
                       ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relowner=principal) OR
               EXISTS (SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n
                       ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proowner=principal) OR
               EXISTS (SELECT 1 FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n
                       ON n.oid=t.typnamespace WHERE n.nspname='public' AND t.typowner=principal) THEN
                RAISE EXCEPTION 'unqualified lifecycle writer' USING ERRCODE='42501';
            END IF;
            -- Filtered catalog/parent reads cannot be treated as absent ownership.
            -- An RLS policy needs its own reviewed profile instead of fail-open lookup.
            IF EXISTS (SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n
                       ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relrowsecurity
                       AND c.relname IN ('executions','runtime_execution_owners',
                           'execution_attempts','workflow_execution_attempts','execution_lifecycle_events','event_deliveries',
                           'users','cli_sessions','forms','workflows','messages','organizations',
                           'runtime_deployment_artifacts','runtime_sessions','runtime_workflow_live_links',
                           'runtime_starts','runtime_report_receipts','workflow_runtime_sdk_grants',
                           'workflow_runtime_sdk_grant_operations','runtime_admissions',
                           'runtime_resource_settlements')) THEN
                RAISE EXCEPTION 'unqualified lifecycle read policy' USING ERRCODE='42501';
            END IF;
            RETURN actor;
        END $$
    """)


def _install_native_guard() -> None:
    op.execute("""
        CREATE FUNCTION public.guard_runtime_native_writer() RETURNS trigger
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog,pg_temp AS $$
        DECLARE actor text; previous jsonb; proposed jsonb; facts jsonb;
            parent uuid; actual_parent uuid; parent_owner text; parent_live boolean;
            bound_owner text; native_org uuid; retained boolean;
            actual_type text; actual_job uuid; actual_found boolean;
            rechecked_type text; rechecked_job uuid; rechecked_found boolean;
            attempt_bindings uuid[]; attempt_identity uuid;
            rust_touched boolean := FALSE; python_touched boolean := FALSE;
        BEGIN
            IF TG_TABLE_SCHEMA <> 'public' OR TG_TABLE_NAME NOT IN
                ('executions','workflow_execution_attempts','execution_logs','ai_usage',
                 'event_deliveries','execution_attempts','execution_lifecycle_events') THEN
                RAISE EXCEPTION 'unknown lifecycle relation' USING ERRCODE='42501';
            END IF;
            actor := public.runtime_workflow_actor();
            IF TG_OP <> 'INSERT' THEN previous := to_jsonb(OLD); END IF;
            IF TG_OP <> 'DELETE' THEN proposed := to_jsonb(NEW); END IF;
            -- Generated columns are not populated in BEFORE NEW records.
            IF TG_TABLE_NAME = 'executions' THEN
                previous := previous - 'runtime_owner_id';
                proposed := proposed - 'runtime_owner_id';
            END IF;
            -- A polymorphic or absent generic attempt has no workflow fence.
            -- Protect its row identity before choosing any execution fences so
            -- deletion/recreation cannot substitute the FK target afterward.
            IF TG_TABLE_NAME = 'execution_attempts' THEN
                attempt_bindings := ARRAY[(previous->>'id')::uuid,(proposed->>'id')::uuid];
            ELSIF TG_TABLE_NAME = 'execution_lifecycle_events' THEN
                attempt_bindings := ARRAY[(previous->>'attempt_id')::uuid,(proposed->>'attempt_id')::uuid];
            END IF;
            FOR attempt_identity IN SELECT DISTINCT value FROM unnest(attempt_bindings) AS t(value)
                WHERE value IS NOT NULL ORDER BY value LOOP
                IF NOT pg_try_advisory_xact_lock(hashtext('bifrost:execution-attempt-identity:' || attempt_identity::text)) THEN
                    RAISE EXCEPTION 'lifecycle attempt identity contended' USING ERRCODE='55P03';
                END IF;
            END LOOP;
            FOREACH facts IN ARRAY ARRAY[previous,proposed] LOOP
                IF facts IS NULL THEN CONTINUE; END IF;
                actual_parent := NULL;
                actual_found := FALSE;
                IF TG_TABLE_NAME = 'executions' THEN parent := (facts->>'id')::uuid;
                ELSIF TG_TABLE_NAME IN ('execution_attempts','execution_lifecycle_events') THEN
                    parent := CASE WHEN facts->>'logical_job_type' IN ('workflow','workflow_execution')
                        THEN (facts->>'logical_job_id')::uuid END;
                ELSE parent := (facts->>'execution_id')::uuid;
                END IF;
                IF TG_TABLE_NAME = 'execution_lifecycle_events' THEN
                    SELECT logical_job_type,logical_job_id INTO actual_type,actual_job
                        FROM public.execution_attempts WHERE id=(facts->>'attempt_id')::uuid;
                    actual_found := FOUND;
                    actual_parent := CASE WHEN actual_type IN ('workflow','workflow_execution')
                        THEN actual_job END;
                END IF;
                -- Both declared and actual event-parent paths use the same fence.
                -- Every edge is a try-lock; a trigger never waits behind an owner
                -- while holding a child or a later native row lock.
                FOR parent IN SELECT DISTINCT value FROM unnest(ARRAY[parent,actual_parent]) AS t(value)
                    WHERE value IS NOT NULL ORDER BY value LOOP
                    IF NOT pg_try_advisory_xact_lock(hashtext('bifrost:workflow-execution:' || parent::text)) THEN
                        RAISE EXCEPTION 'lifecycle writer fence contended' USING ERRCODE='55P03';
                    END IF;
                    SELECT control_owner,organization_id INTO parent_owner,native_org
                        FROM public.executions WHERE id=parent;
                    parent_live := FOUND;
                    retained := EXISTS (SELECT 1 FROM public.runtime_execution_owners WHERE execution_id=parent);
                    IF TG_TABLE_NAME = 'executions' THEN
                        parent_owner := facts->>'control_owner';
                        parent_live := TRUE;
                    END IF;
                    IF retained AND parent_owner = 'python' THEN
                        RAISE EXCEPTION 'retained ownership conflicts with native row' USING ERRCODE='42501';
                    END IF;
                    bound_owner := COALESCE(parent_owner,CASE WHEN retained THEN 'rust' ELSE 'python' END);
                    rust_touched := rust_touched OR bound_owner = 'rust';
                    python_touched := python_touched OR bound_owner = 'python';
                    IF bound_owner = 'rust' AND NOT parent_live AND TG_OP <> 'DELETE' THEN
                        RAISE EXCEPTION 'native runtime parent is detached' USING ERRCODE='42501';
                    END IF;
                    IF actor='rust' AND bound_owner = 'rust' AND TG_TABLE_NAME <> 'executions' AND
                       facts ? 'organization_id' AND facts->>'organization_id' IS DISTINCT FROM native_org::text
                       AND TG_OP <> 'DELETE' THEN
                        RAISE EXCEPTION 'runtime projection organization differs' USING ERRCODE='42501';
                    END IF;
                END LOOP;
                IF TG_TABLE_NAME = 'execution_lifecycle_events' THEN
                    -- The first lookup only selects fences. A delete/recreate
                    -- before those fences must not substitute a different parent
                    -- through the existing single-column native FK. VOLATILE
                    -- plus required READ COMMITTED gives this query a fresh view.
                    SELECT logical_job_type,logical_job_id INTO rechecked_type,rechecked_job
                        FROM public.execution_attempts WHERE id=(facts->>'attempt_id')::uuid;
                    rechecked_found := FOUND;
                    IF rechecked_found IS DISTINCT FROM actual_found OR
                       rechecked_type IS DISTINCT FROM actual_type OR
                       rechecked_job IS DISTINCT FROM actual_job THEN
                        RAISE EXCEPTION 'runtime event parent changed during fencing' USING ERRCODE='42501';
                    END IF;
                END IF;
                IF TG_TABLE_NAME = 'execution_lifecycle_events' AND rust_touched AND
                   ((actual_found AND (actual_type IS DISTINCT FROM facts->>'logical_job_type'
                       OR actual_job IS DISTINCT FROM (facts->>'logical_job_id')::uuid)) OR
                    (NOT actual_found AND TG_OP <> 'DELETE')) THEN
                    RAISE EXCEPTION 'runtime event attempt binding differs' USING ERRCODE='42501';
                END IF;
            END LOOP;
            IF TG_TABLE_NAME = 'executions' AND TG_OP = 'INSERT' THEN
                -- Birth requirements must not become permanent NOT NULL checks:
                -- a later permitted ancestor unlink keeps historical source.
                IF NEW.control_owner='rust' AND
                   (NEW.runtime_mode IS DISTINCT FROM 'deployment-v1' OR
                    NEW.workflow_id IS NULL OR NEW.solution_deployment_id IS NULL) THEN
                    RAISE EXCEPTION 'runtime birth requires pinned source' USING ERRCODE='42501';
                END IF;
                IF EXISTS (SELECT 1 FROM public.runtime_execution_owners WHERE execution_id=NEW.id) OR
                   (NEW.control_owner='rust' AND (
                    EXISTS (SELECT 1 FROM public.event_deliveries WHERE execution_id=NEW.id) OR
                    EXISTS (SELECT 1 FROM public.execution_attempts
                        WHERE logical_job_type IN ('workflow','workflow_execution') AND logical_job_id=NEW.id) OR
                    EXISTS (SELECT 1 FROM public.execution_lifecycle_events
                        WHERE logical_job_type IN ('workflow','workflow_execution') AND logical_job_id=NEW.id))) THEN
                    RAISE EXCEPTION 'runtime birth cannot adopt retained work' USING ERRCODE='42501';
                END IF;
            END IF;
            IF rust_touched AND actor='python' AND TG_OP='UPDATE' THEN
                IF TG_TABLE_NAME='executions' THEN
                    -- Exact public terminal sanitation; authorization and audit
                    -- stay in workspace_admin.redact_execution, never in a GUC.
                    IF OLD.status::text IN ('Success','Failed','Timeout','Cancelled','CompletedWithErrors') AND
                       proposed->'parameters'='{"redacted":true}'::jsonb AND
                       proposed->'result'='{"redacted":true}'::jsonb AND
                       proposed->'variables'='{"redacted":true}'::jsonb AND
                       proposed->'execution_context'='{"redacted":true}'::jsonb AND NEW.error_message IS NULL AND
                       (proposed-ARRAY['parameters','result','variables','execution_context','error_message']) =
                       (previous-ARRAY['parameters','result','variables','execution_context','error_message']) THEN
                        RETURN NEW;
                    END IF;
                    IF (proposed-ARRAY['executed_by','session_id','form_id','workflow_id','api_key_id']) =
                       (previous-ARRAY['executed_by','session_id','form_id','workflow_id','api_key_id']) AND
                       proposed IS DISTINCT FROM previous AND
                       (NEW.executed_by IS NOT DISTINCT FROM OLD.executed_by OR
                        (OLD.executed_by IS NOT NULL AND NEW.executed_by IS NULL AND
                         NOT EXISTS (SELECT 1 FROM public.users WHERE id=OLD.executed_by))) AND
                       (NEW.session_id IS NOT DISTINCT FROM OLD.session_id OR
                        (OLD.session_id IS NOT NULL AND NEW.session_id IS NULL AND
                         NOT EXISTS (SELECT 1 FROM public.cli_sessions WHERE id=OLD.session_id))) AND
                       (NEW.workflow_id IS NOT DISTINCT FROM OLD.workflow_id OR
                        (OLD.workflow_id IS NOT NULL AND NEW.workflow_id IS NULL AND
                         NOT EXISTS (SELECT 1 FROM public.workflows WHERE id=OLD.workflow_id))) AND
                       (NEW.api_key_id IS NOT DISTINCT FROM OLD.api_key_id OR
                        (OLD.api_key_id IS NOT NULL AND NEW.api_key_id IS NULL AND
                         NOT EXISTS (SELECT 1 FROM public.workflows WHERE id=OLD.api_key_id))) AND
                       (NEW.form_id IS NOT DISTINCT FROM OLD.form_id OR
                        (OLD.form_id IS NOT NULL AND NEW.form_id IS NULL AND
                         (NOT EXISTS (SELECT 1 FROM public.forms WHERE id=OLD.form_id) OR
                          EXISTS (SELECT 1 FROM public.forms WHERE id=OLD.form_id
                                  AND NOT is_active AND solution_id IS NULL)))) THEN
                        RETURN NEW;
                    END IF;
                ELSIF TG_TABLE_NAME='ai_usage' AND
                    (proposed-ARRAY['message_id','user_id','organization_id']) =
                    (previous-ARRAY['message_id','user_id','organization_id']) AND
                    proposed IS DISTINCT FROM previous AND
                    (NEW.message_id IS NOT DISTINCT FROM OLD.message_id OR
                     (OLD.message_id IS NOT NULL AND NEW.message_id IS NULL AND
                      NOT EXISTS (SELECT 1 FROM public.messages WHERE id=OLD.message_id))) AND
                    (NEW.user_id IS NOT DISTINCT FROM OLD.user_id OR
                     (OLD.user_id IS NOT NULL AND NEW.user_id IS NULL AND
                      NOT EXISTS (SELECT 1 FROM public.users WHERE id=OLD.user_id))) AND
                    (NEW.organization_id IS NOT DISTINCT FROM OLD.organization_id OR
                     (OLD.organization_id IS NOT NULL AND NEW.organization_id IS NULL AND
                      NOT EXISTS (SELECT 1 FROM public.organizations WHERE id=OLD.organization_id))) THEN
                    RETURN NEW;
                END IF;
            END IF;
            IF (actor='python' AND rust_touched) OR
               (actor='rust' AND (python_touched OR NOT rust_touched)) THEN
                RAISE EXCEPTION 'foreign lifecycle owner' USING ERRCODE='42501';
            END IF;
            IF rust_touched AND TG_OP='UPDATE' AND (
                proposed->'id' IS DISTINCT FROM previous->'id' OR
                proposed->'execution_id' IS DISTINCT FROM previous->'execution_id' OR
                proposed->'logical_job_type' IS DISTINCT FROM previous->'logical_job_type' OR
                proposed->'logical_job_id' IS DISTINCT FROM previous->'logical_job_id' OR
                proposed->'attempt_id' IS DISTINCT FROM previous->'attempt_id' OR
                proposed->'attempt_number' IS DISTINCT FROM previous->'attempt_number'
            ) THEN
                RAISE EXCEPTION 'immutable runtime projection binding' USING ERRCODE='42501';
            END IF;
            IF rust_touched AND TG_TABLE_NAME='executions' AND TG_OP='UPDATE' AND
               (proposed-ARRAY['status','result','result_type','variables','error_message','started_at',
                'completed_at','duration_ms','peak_memory_bytes','process_rss_bytes','cpu_user_seconds',
                'cpu_system_seconds','cpu_total_seconds','peak_cpu_cores','peak_process_rss_bytes',
                'time_saved','value','execution_model']) IS DISTINCT FROM
               (previous-ARRAY['status','result','result_type','variables','error_message','started_at',
                'completed_at','duration_ms','peak_memory_bytes','process_rss_bytes','cpu_user_seconds',
                'cpu_system_seconds','cpu_total_seconds','peak_cpu_cores','peak_process_rss_bytes',
                'time_saved','value','execution_model']) THEN
                RAISE EXCEPTION 'immutable runtime execution facts' USING ERRCODE='42501';
            END IF;
            IF actor='rust' AND TG_OP <> 'DELETE' AND (
                (TG_TABLE_NAME='ai_usage' AND
                 (proposed->>'conversation_id' IS NOT NULL OR proposed->>'agent_run_id' IS NOT NULL)) OR
                (TG_TABLE_NAME='event_deliveries' AND proposed->>'agent_run_id' IS NOT NULL)
            ) THEN
                RAISE EXCEPTION 'multi-parent agent ownership is not admitted' USING ERRCODE='42501';
            END IF;
            IF TG_OP='DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END $$
    """)


def _install_owner_birth_guard() -> None:
    op.execute("""
        CREATE FUNCTION public.guard_runtime_owner_birth() RETURNS trigger
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog,pg_temp AS $$
        BEGIN
            IF public.runtime_workflow_actor() <> 'rust' OR
               NOT pg_try_advisory_xact_lock(hashtext('bifrost:workflow-execution:' || NEW.execution_id::text)) THEN
                RAISE EXCEPTION 'runtime owner birth is not authorized' USING ERRCODE='42501';
            END IF;
            -- The read-only custodian does not need a row-lock privilege.
            -- C1's invoker identity guard performs the coordinator's row locks.
            PERFORM 1 FROM public.executions WHERE id=NEW.execution_id AND control_owner='rust';
            IF NOT FOUND THEN
                RAISE EXCEPTION 'runtime owner cannot claim an incumbent execution' USING ERRCODE='42501';
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
        raise RuntimeError("Native writer downgrade requires read committed isolation")
    bind.execute(
        sa.text(
            "LOCK TABLE "
            + ", ".join(f"public.{table}" for table in (*NATIVE_TABLES, *COMMON_TABLES))
            + " IN ACCESS EXCLUSIVE MODE NOWAIT"
        )
    )
    if bind.execute(
        sa.text(
            "SELECT "
            + " OR ".join(
                f"EXISTS (SELECT 1 FROM public.{table})" for table in COMMON_TABLES
            )
        )
    ).scalar_one():
        raise RuntimeError("Retain common ownership enforcement while evidence remains")
    op.execute(
        "DROP TRIGGER runtime_owner_birth_writer ON public.runtime_execution_owners"
    )
    for table in NATIVE_TABLES:
        op.execute(f"DROP TRIGGER runtime_native_writer ON public.{table}")
    for function in reversed(FUNCTIONS):
        op.execute(f"DROP FUNCTION public.{function}()")
    op.drop_constraint(
        "fk_execution_retained_runtime_owner",
        "executions",
        type_="foreignkey",
        schema="public",
    )
    op.drop_column("executions", "runtime_owner_id", schema="public")
    op.drop_constraint(
        "ck_execution_control_owner", "executions", type_="check", schema="public"
    )
    op.drop_column("executions", "control_owner", schema="public")
