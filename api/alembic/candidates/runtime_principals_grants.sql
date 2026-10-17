-- REVIEW-ONLY R1 finite grants, after complete reviewed C1-C5 Alembic apply.
-- No LOGIN, password, memberships, broad grants, automatic ACL repairs or
-- native ownership. Existing catalog must match before test-login activation.
-- Guard function owners are assigned by the reviewed Alembic revisions.
\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
    IF current_database() <> 'bifrost_test' OR current_user <> session_user OR
       NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles
                   WHERE rolname=session_user AND rolsuper) OR
       (SELECT array_agg(version_num ORDER BY version_num) FROM public.alembic_version)
          IS DISTINCT FROM ARRAY['20261016_runtime_source_drain']::varchar[] OR
       (SELECT count(*) FROM pg_catalog.pg_roles WHERE rolname IN (
          'wex_core','wex_incumbent','runtime_lifecycle_guard','runtime_artifact_lock_guard')
          AND NOT rolcanlogin AND NOT rolinherit AND NOT rolsuper AND NOT rolcreatedb
          AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls) <> 4 OR
       EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m
               JOIN pg_catalog.pg_roles r ON r.oid=m.member OR r.oid=m.roleid
               WHERE r.rolname IN ('wex_core','wex_incumbent',
                   'runtime_lifecycle_guard','runtime_artifact_lock_guard')) THEN
        RAISE EXCEPTION 'R1 requires complete schema and inactive finite principals';
    END IF;
END $$;
GRANT CONNECT ON DATABASE bifrost_test TO "wex_core";
GRANT USAGE ON SCHEMA public TO "wex_core";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.executions TO "wex_core";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.workflow_execution_attempts TO "wex_core";
GRANT SELECT, INSERT ON TABLE public.execution_logs TO "wex_core";
GRANT SELECT, INSERT ON TABLE public.ai_usage TO "wex_core";
GRANT SELECT, INSERT, UPDATE ON TABLE public.event_deliveries TO "wex_core";
GRANT SELECT, INSERT, UPDATE ON TABLE public.execution_attempts TO "wex_core";
GRANT SELECT, INSERT ON TABLE public.execution_lifecycle_events TO "wex_core";
GRANT SELECT, INSERT ON TABLE public.runtime_deployment_artifacts TO "wex_core";
GRANT SELECT, INSERT ON TABLE public.runtime_execution_owners TO "wex_core";
GRANT SELECT, INSERT ON TABLE public.runtime_sessions TO "wex_core";
GRANT SELECT, INSERT ON TABLE public.runtime_workflow_live_links TO "wex_core";
GRANT SELECT, INSERT ON TABLE public.runtime_starts TO "wex_core";
GRANT SELECT, INSERT ON TABLE public.runtime_report_receipts TO "wex_core";
GRANT SELECT, INSERT ON TABLE public.workflow_runtime_sdk_grants TO "wex_core";
GRANT SELECT, INSERT ON TABLE public.workflow_runtime_sdk_grant_operations TO "wex_core";
GRANT SELECT, INSERT ON TABLE public.runtime_admissions TO "wex_core";
GRANT SELECT, INSERT ON TABLE public.runtime_resource_settlements TO "wex_core";
GRANT UPDATE (live_deployment_id, live_workflow_id, live_solution_id, live_reviewed_by) ON TABLE public.runtime_deployment_artifacts TO "wex_core";
GRANT UPDATE (live_execution_id) ON TABLE public.runtime_execution_owners TO "wex_core";
GRANT UPDATE (closed_at, close_reason) ON TABLE public.runtime_sessions TO "wex_core";
GRANT UPDATE (native_attempt_id) ON TABLE public.runtime_workflow_live_links TO "wex_core";
GRANT UPDATE (id) ON TABLE public.runtime_starts TO "wex_core";
GRANT UPDATE (id) ON TABLE public.runtime_admissions TO "wex_core";
GRANT UPDATE (revoked_at, revocation_reason) ON TABLE public.workflow_runtime_sdk_grants TO "wex_core";
GRANT UPDATE (result_message_id) ON TABLE public.runtime_report_receipts TO "wex_core";
GRANT USAGE ON SEQUENCE public.execution_logs_id_seq TO "wex_core";
GRANT USAGE ON SEQUENCE public.ai_usage_id_seq TO "wex_core";
GRANT CONNECT ON DATABASE bifrost_test TO "wex_incumbent";
GRANT USAGE ON SCHEMA public TO "wex_incumbent";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.executions TO "wex_incumbent";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.workflow_execution_attempts TO "wex_incumbent";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.execution_logs TO "wex_incumbent";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.ai_usage TO "wex_incumbent";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.event_deliveries TO "wex_incumbent";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.execution_attempts TO "wex_incumbent";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.execution_lifecycle_events TO "wex_incumbent";
GRANT SELECT, UPDATE, DELETE ON TABLE public.users TO "wex_incumbent";
GRANT SELECT, UPDATE, DELETE ON TABLE public.workflows TO "wex_incumbent";
GRANT SELECT, UPDATE, DELETE ON TABLE public.forms TO "wex_incumbent";
GRANT SELECT, UPDATE, DELETE ON TABLE public.messages TO "wex_incumbent";
GRANT SELECT, UPDATE, DELETE ON TABLE public.cli_sessions TO "wex_incumbent";
GRANT SELECT, UPDATE, DELETE ON TABLE public.event_sources TO "wex_incumbent";
GRANT SELECT, UPDATE, DELETE ON TABLE public.solutions TO "wex_incumbent";
GRANT SELECT, UPDATE, DELETE ON TABLE public.solution_deployments TO "wex_incumbent";
GRANT SELECT ON TABLE public.organizations TO "wex_incumbent";
GRANT SELECT ON TABLE public.roles TO "wex_incumbent";
GRANT SELECT ON TABLE public.user_roles TO "wex_incumbent";
GRANT SELECT, INSERT ON TABLE public.audit_logs TO "wex_incumbent";
GRANT UPDATE (is_active) ON TABLE public.organizations TO "wex_incumbent";
GRANT USAGE ON SEQUENCE public.execution_logs_id_seq TO "wex_incumbent";
GRANT USAGE ON SEQUENCE public.ai_usage_id_seq TO "wex_incumbent";
GRANT EXECUTE ON FUNCTION public.runtime_undrained_sources() TO "wex_incumbent";
GRANT USAGE ON SCHEMA public TO "runtime_lifecycle_guard";
GRANT SELECT ON TABLE public.runtime_sessions TO "runtime_lifecycle_guard";
GRANT SELECT (id, control_owner, organization_id, status, runtime_mode, workflow_id, solution_deployment_id) ON TABLE public.executions TO "runtime_lifecycle_guard";
GRANT SELECT (execution_id, owner_incarnation_id, live_execution_id, workflow_id, deployment_id) ON TABLE public.runtime_execution_owners TO "runtime_lifecycle_guard";
GRANT SELECT (id, logical_job_type, logical_job_id, completed_at) ON TABLE public.execution_attempts TO "runtime_lifecycle_guard";
GRANT SELECT (logical_job_type, logical_job_id) ON TABLE public.execution_lifecycle_events TO "runtime_lifecycle_guard";
GRANT SELECT (execution_id) ON TABLE public.event_deliveries TO "runtime_lifecycle_guard";
GRANT SELECT (id) ON TABLE public.users TO "runtime_lifecycle_guard";
GRANT SELECT (id) ON TABLE public.cli_sessions TO "runtime_lifecycle_guard";
GRANT SELECT (id, is_active, solution_id) ON TABLE public.forms TO "runtime_lifecycle_guard";
GRANT SELECT (id) ON TABLE public.workflows TO "runtime_lifecycle_guard";
GRANT SELECT (id) ON TABLE public.messages TO "runtime_lifecycle_guard";
GRANT SELECT (id) ON TABLE public.organizations TO "runtime_lifecycle_guard";
GRANT SELECT (deployment_id, workflow_id, solution_id) ON TABLE public.runtime_deployment_artifacts TO "runtime_lifecycle_guard";
GRANT SELECT (session_id, native_attempt_id) ON TABLE public.runtime_workflow_live_links TO "runtime_lifecycle_guard";
GRANT SELECT (id, execution_id, claim_token, worker_incarnation_id, status, phase, completed_at, started_at) ON TABLE public.workflow_execution_attempts TO "runtime_lifecycle_guard";
GRANT SELECT (id, runtime_session_id, execution_id, revoked_at) ON TABLE public.workflow_runtime_sdk_grants TO "runtime_lifecycle_guard";
GRANT SELECT (session_id) ON TABLE public.runtime_resource_settlements TO "runtime_lifecycle_guard";
GRANT USAGE ON SCHEMA public TO "runtime_artifact_lock_guard";
GRANT SELECT (id) ON TABLE public.users TO "runtime_artifact_lock_guard";
GRANT UPDATE (id) ON TABLE public.users TO "runtime_artifact_lock_guard";
GRANT SELECT (id) ON TABLE public.solutions TO "runtime_artifact_lock_guard";
GRANT UPDATE (id) ON TABLE public.solutions TO "runtime_artifact_lock_guard";
GRANT SELECT (id, solution_id) ON TABLE public.workflows TO "runtime_artifact_lock_guard";
GRANT UPDATE (id) ON TABLE public.workflows TO "runtime_artifact_lock_guard";
GRANT SELECT (id, solution_id) ON TABLE public.solution_deployments TO "runtime_artifact_lock_guard";
GRANT UPDATE (id) ON TABLE public.solution_deployments TO "runtime_artifact_lock_guard";
COMMIT;
