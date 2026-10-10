-- Isolated actual-schema experiment ONLY. Not an Alembic migration or dispatch release.
-- Install once in a fresh task database after current migrations. No real credentials.
CREATE ROLE isolated_writer_guard NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION NOBYPASSRLS;
CREATE ROLE wex_incumbent LOGIN PASSWORD 'synthetic_primer_incumbent'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE wex_core LOGIN PASSWORD 'synthetic_primer_core'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
-- Server role defaults avoid unsupported PgBouncer startup parameters. Client
-- commands also retain finite deadlines; no timeout/retry enlargement is used.
ALTER ROLE wex_incumbent SET statement_timeout = '3s';
ALTER ROLE wex_incumbent SET lock_timeout = '1s';
ALTER ROLE wex_core SET statement_timeout = '3s';
ALTER ROLE wex_core SET lock_timeout = '1s';
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE TEMPORARY ON DATABASE bifrost_test FROM PUBLIC;
GRANT CONNECT ON DATABASE bifrost_test TO wex_incumbent, wex_core;
GRANT USAGE ON SCHEMA public TO wex_incumbent, wex_core, isolated_writer_guard;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO wex_incumbent, wex_core;

ALTER TABLE executions ADD COLUMN isolated_owner text NOT NULL DEFAULT 'incumbent'
    CHECK (isolated_owner IN ('incumbent', 'coordinator'));
ALTER TABLE executions ADD CONSTRAINT ck_isolated_coordinator_source CHECK (
    isolated_owner <> 'coordinator' OR
    (runtime_mode = 'deployment-v1' AND workflow_id IS NOT NULL AND solution_deployment_id IS NOT NULL)
);
ALTER TABLE executions ADD CONSTRAINT uq_isolated_execution_owner UNIQUE (id, isolated_owner);
ALTER TABLE executions ADD COLUMN isolated_coordinator_id uuid GENERATED ALWAYS AS
    (CASE WHEN isolated_owner = 'coordinator' THEN id END) STORED;
-- A coordinator execution is born with its retained owner, never taken over later.
ALTER TABLE executions ADD CONSTRAINT fk_isolated_execution_retained_owner
    FOREIGN KEY (isolated_coordinator_id) REFERENCES runtime_execution_owners(execution_id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE runtime_execution_owners ADD COLUMN isolated_owner text NOT NULL DEFAULT 'coordinator'
    CHECK (isolated_owner = 'coordinator');
ALTER TABLE runtime_execution_owners ADD CONSTRAINT fk_isolated_retained_owner_class
    FOREIGN KEY (execution_id, isolated_owner) REFERENCES executions(id, isolated_owner);

DO $install$
DECLARE relation text; action text;
BEGIN
    FOREACH relation IN ARRAY ARRAY['workflow_execution_attempts','execution_logs','ai_usage'] LOOP
        action := CASE WHEN relation = 'execution_logs' THEN 'NO ACTION' ELSE 'CASCADE' END;
        EXECUTE format('ALTER TABLE public.%I ADD COLUMN isolated_owner text NOT NULL DEFAULT ''incumbent'' CHECK (isolated_owner IN (''incumbent'', ''coordinator''))', relation);
        -- Bound value survives parent removal; FK action preserves the original policy.
        EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I FOREIGN KEY (execution_id, isolated_owner) REFERENCES public.executions(id, isolated_owner) ON DELETE %s', relation, 'fk_isolated_' || relation || '_owner', action);
    END LOOP;
    FOREACH relation IN ARRAY ARRAY['event_deliveries','execution_attempts','execution_lifecycle_events'] LOOP
        EXECUTE format('ALTER TABLE public.%I ADD COLUMN isolated_owner text NOT NULL DEFAULT ''incumbent'' CHECK (isolated_owner IN (''incumbent'', ''coordinator''))', relation);
        IF relation = 'event_deliveries' THEN
            EXECUTE 'ALTER TABLE public.event_deliveries ADD COLUMN isolated_coordinator_id uuid GENERATED ALWAYS AS (CASE WHEN isolated_owner = ''coordinator'' THEN execution_id END) STORED';
            EXECUTE 'ALTER TABLE public.event_deliveries ADD CONSTRAINT ck_isolated_delivery_parent CHECK (isolated_owner <> ''coordinator'' OR execution_id IS NOT NULL)';
        ELSE
            EXECUTE format('ALTER TABLE public.%I ADD COLUMN isolated_coordinator_id uuid GENERATED ALWAYS AS (CASE WHEN isolated_owner = ''coordinator'' AND logical_job_type IN (''workflow'', ''workflow_execution'') THEN logical_job_id END) STORED', relation);
            EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I CHECK (isolated_owner <> ''coordinator'' OR logical_job_type IN (''workflow'', ''workflow_execution''))', relation, 'ck_isolated_' || relation || '_kind');
        END IF;
        -- Incumbent pre-execution links remain legal. Coordinator links cannot be orphaned.
        EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I FOREIGN KEY (isolated_coordinator_id, isolated_owner) REFERENCES public.executions(id, isolated_owner)', relation, 'fk_isolated_' || relation || '_coordinator');
    END LOOP;
END $install$;
ALTER TABLE execution_attempts ADD CONSTRAINT uq_isolated_generic_attempt_owner
    UNIQUE (id, isolated_owner, logical_job_type, logical_job_id);
ALTER TABLE execution_lifecycle_events ADD CONSTRAINT fk_isolated_event_attempt_owner
    FOREIGN KEY (attempt_id, isolated_owner, logical_job_type, logical_job_id)
    REFERENCES execution_attempts(id, isolated_owner, logical_job_type, logical_job_id)
    ON DELETE CASCADE;

-- This function has read-only authority. No actor can call it directly or replace it.
GRANT SELECT ON executions, event_deliveries, execution_attempts TO isolated_writer_guard;
CREATE FUNCTION public.isolated_writer_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $guard$
DECLARE previous jsonb; proposed jsonb; facts jsonb; actor text; parent uuid; parent_owner text;
BEGIN
    actor := CASE session_user WHEN 'wex_core' THEN 'coordinator'
        WHEN 'wex_incumbent' THEN 'incumbent' WHEN 'bifrost' THEN 'incumbent' END;
    IF TG_OP <> 'INSERT' THEN previous := to_jsonb(OLD); END IF;
    IF TG_OP <> 'DELETE' THEN proposed := to_jsonb(NEW); END IF;
    IF TG_OP = 'UPDATE' AND (
        proposed->'id' IS DISTINCT FROM previous->'id' OR
        proposed->'isolated_owner' IS DISTINCT FROM previous->'isolated_owner' OR
        proposed->'execution_id' IS DISTINCT FROM previous->'execution_id' OR
        proposed->'logical_job_type' IS DISTINCT FROM previous->'logical_job_type' OR
        proposed->'logical_job_id' IS DISTINCT FROM previous->'logical_job_id' OR
        proposed->'attempt_id' IS DISTINCT FROM previous->'attempt_id'
    ) THEN
        RAISE EXCEPTION 'isolated immutable writer binding' USING ERRCODE = '42501';
    END IF;
    IF TG_TABLE_NAME = 'executions' AND TG_OP = 'UPDATE'
        AND previous->>'isolated_owner' = 'coordinator' AND (
        proposed->'workflow_id' IS DISTINCT FROM previous->'workflow_id' OR
        proposed->'solution_deployment_id' IS DISTINCT FROM previous->'solution_deployment_id' OR
        proposed->'executed_by' IS DISTINCT FROM previous->'executed_by' OR
        proposed->'executed_by_name' IS DISTINCT FROM previous->'executed_by_name' OR
        proposed->'organization_id' IS DISTINCT FROM previous->'organization_id' OR
        proposed->'runtime_mode' IS DISTINCT FROM previous->'runtime_mode' OR
        proposed->'parameters' IS DISTINCT FROM previous->'parameters' OR
        proposed->'execution_context' IS DISTINCT FROM previous->'execution_context' OR
        proposed->'runtime_evidence' IS DISTINCT FROM previous->'runtime_evidence' OR
        proposed->'runtime_evidence_hash' IS DISTINCT FROM previous->'runtime_evidence_hash' OR
        proposed->'dispatch_evidence' IS DISTINCT FROM previous->'dispatch_evidence' OR
        proposed->'dispatch_evidence_hash' IS DISTINCT FROM previous->'dispatch_evidence_hash' OR
        proposed->'retry_policy' IS DISTINCT FROM previous->'retry_policy'
    ) THEN
        RAISE EXCEPTION 'isolated immutable execution facts' USING ERRCODE = '42501';
    END IF;
    FOREACH facts IN ARRAY ARRAY[previous, proposed] LOOP
        IF facts IS NULL THEN CONTINUE; END IF;
        IF facts->>'isolated_owner' IS DISTINCT FROM actor THEN
            RAISE EXCEPTION 'isolated foreign lifecycle owner' USING ERRCODE = '42501';
        END IF;
        IF TG_TABLE_NAME = 'executions' THEN parent := (facts->>'id')::uuid;
        ELSIF TG_TABLE_NAME IN ('execution_attempts','execution_lifecycle_events') THEN
            parent := CASE WHEN facts->>'logical_job_type' IN ('workflow','workflow_execution')
                THEN (facts->>'logical_job_id')::uuid END;
        ELSE parent := (facts->>'execution_id')::uuid;
        END IF;
        IF parent IS NOT NULL THEN
            -- Non-blocking use of the incumbent poison fence prevents absent-parent
            -- non-FK links racing a newly born coordinator execution. Never wait
            -- behind execution-first writers while holding owner/attempt locks.
            IF NOT pg_try_advisory_xact_lock(hashtext('bifrost:workflow-execution:' || parent::text)) THEN
                RAISE EXCEPTION 'isolated writer fence contention' USING ERRCODE = '55P03';
            END IF;
            SELECT isolated_owner INTO parent_owner FROM public.executions WHERE id = parent;
            IF parent_owner IS NOT NULL AND parent_owner IS DISTINCT FROM actor THEN
                RAISE EXCEPTION 'isolated foreign parent owner' USING ERRCODE = '42501';
            END IF;
            IF TG_TABLE_NAME = 'executions' AND TG_OP = 'INSERT' AND actor = 'coordinator' AND (
                EXISTS (SELECT 1 FROM public.event_deliveries WHERE execution_id = parent AND isolated_owner <> actor) OR
                EXISTS (SELECT 1 FROM public.execution_attempts WHERE logical_job_id = parent
                    AND logical_job_type IN ('workflow','workflow_execution') AND isolated_owner <> actor)
            ) THEN
                RAISE EXCEPTION 'isolated incumbent pre-execution link' USING ERRCODE = '42501';
            END IF;
        END IF;
    END LOOP;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $guard$;
ALTER FUNCTION public.isolated_writer_guard() OWNER TO isolated_writer_guard;
REVOKE ALL ON FUNCTION public.isolated_writer_guard() FROM PUBLIC;

CREATE FUNCTION public.isolated_runtime_writer_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $guard$
BEGIN
    IF session_user <> 'wex_core' THEN
        RAISE EXCEPTION 'isolated runtime authority required' USING ERRCODE = '42501';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $guard$;
ALTER FUNCTION public.isolated_runtime_writer_guard() OWNER TO isolated_writer_guard;
REVOKE ALL ON FUNCTION public.isolated_runtime_writer_guard() FROM PUBLIC;

DO $triggers$
DECLARE relation text;
BEGIN
    FOREACH relation IN ARRAY ARRAY['executions','workflow_execution_attempts','execution_logs','ai_usage',
        'event_deliveries','execution_attempts','execution_lifecycle_events'] LOOP
        EXECUTE format('CREATE TRIGGER isolated_writer_owner BEFORE INSERT OR UPDATE OR DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.isolated_writer_guard()', relation);
        EXECUTE format('GRANT INSERT, UPDATE, DELETE ON public.%I TO wex_incumbent, wex_core', relation);
    END LOOP;
    FOREACH relation IN ARRAY ARRAY['runtime_execution_owners','runtime_sessions','runtime_starts','runtime_report_receipts',
        'workflow_runtime_sdk_grants','workflow_runtime_sdk_grant_operations','runtime_admissions'] LOOP
        EXECUTE format('CREATE TRIGGER isolated_runtime_owner BEFORE INSERT OR UPDATE OR DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.isolated_runtime_writer_guard()', relation);
        EXECUTE format('GRANT INSERT, UPDATE ON public.%I TO wex_core', relation);
    END LOOP;
END $triggers$;
GRANT USAGE ON SEQUENCE execution_logs_id_seq, ai_usage_id_seq TO wex_incumbent, wex_core;
-- Exact ancestor path needed to exercise cascade rollback, not a maintenance bypass.
GRANT DELETE ON event_sources TO wex_incumbent;
ALTER TABLE ai_usage ADD CONSTRAINT ck_isolated_usage_parent
    CHECK (isolated_owner <> 'coordinator' OR execution_id IS NOT NULL);

-- PostgreSQL row locks require UPDATE privilege on at least one source column.
-- Permit only the privilege needed to lock; every coordinator UPDATE is rejected
-- by a trigger owned by the existing NOLOGIN custodian. Incumbent source writes
-- remain unchanged. This is isolated fixture DDL, not an operational role grant.
CREATE FUNCTION public.isolated_source_lock_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $guard$
BEGIN
    IF session_user = 'wex_core' THEN
        RAISE EXCEPTION 'isolated coordinator source writes forbidden' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END $guard$;
ALTER FUNCTION public.isolated_source_lock_guard() OWNER TO isolated_writer_guard;
REVOKE ALL ON FUNCTION public.isolated_source_lock_guard() FROM PUBLIC;
CREATE TRIGGER isolated_source_lock_only BEFORE UPDATE ON public.solutions
    FOR EACH ROW EXECUTE FUNCTION public.isolated_source_lock_guard();
CREATE TRIGGER isolated_source_lock_only BEFORE UPDATE ON public.solution_deployments
    FOR EACH ROW EXECUTE FUNCTION public.isolated_source_lock_guard();
GRANT UPDATE (id) ON public.solutions, public.solution_deployments TO wex_core;
