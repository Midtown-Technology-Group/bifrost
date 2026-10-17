-- REVIEW-ONLY R1 SQL preparation. No startup/CI consumer applies this file.
-- Only after explicit approval, on a NEW task-owned disposable PostgreSQL16
-- cluster. Not production provisioning, not a repair of an existing role.
-- Native fixture preseed precedes this file; C1-C5 Alembic apply follows it.
-- No login/password/credential, membership, CREATE/TEMP or native ownership.
\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
    IF current_database() <> 'bifrost_test' OR current_user <> session_user OR
       NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles
                   WHERE rolname=session_user AND rolsuper) OR
       (SELECT array_agg(version_num ORDER BY version_num) FROM public.alembic_version)
          IS DISTINCT FROM ARRAY['20261012_work_delivery_started']::varchar[] OR
       EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname IN (
          'wex_core','wex_incumbent','runtime_lifecycle_guard','runtime_artifact_lock_guard')) THEN
        RAISE EXCEPTION 'R1 requires exact native stage and absent proposed roles';
    END IF;
END $$;
-- Explicit isolated-only ambient capability disposition, part of approval.
-- Nothing revokes arbitrary extension/function/default ACLs automatically.
REVOKE CONNECT, TEMPORARY ON DATABASE bifrost_test FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE ROLE "wex_core" NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE "wex_core" SET statement_timeout = '3s';
ALTER ROLE "wex_core" SET lock_timeout = '1s';
ALTER ROLE "wex_core" SET search_path = pg_catalog, pg_temp;
CREATE ROLE "wex_incumbent" NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE "wex_incumbent" SET statement_timeout = '3s';
ALTER ROLE "wex_incumbent" SET lock_timeout = '1s';
ALTER ROLE "wex_incumbent" SET search_path = pg_catalog, public, pg_temp;
CREATE ROLE "runtime_lifecycle_guard" NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE "runtime_lifecycle_guard" SET statement_timeout = '3s';
ALTER ROLE "runtime_lifecycle_guard" SET lock_timeout = '1s';
CREATE ROLE "runtime_artifact_lock_guard" NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE "runtime_artifact_lock_guard" SET statement_timeout = '3s';
ALTER ROLE "runtime_artifact_lock_guard" SET lock_timeout = '1s';
COMMIT;
