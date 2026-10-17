-- REVIEW INPUT ONLY. Separate explicit approval is required before invocation.
-- Gate: independent installed-catalog reconciliation while all roles NOLOGIN.
-- Only two synthetic test logins, never custodians or a runtime/SDK credential.
-- First set each NOLOGIN role's password with built-in psql \password, which
-- encrypts before sending SQL. Never interpolate plaintext passwords into SQL.
-- Supply only the nonsecret expiry environment value to this file.
-- Use psql --no-psqlrc --quiet; retain all output privately with exit status.
\set ON_ERROR_STOP on
\set ECHO none
\getenv r1_expiry R1_TEST_LOGIN_EXPIRY

BEGIN;
SET LOCAL password_encryption = 'scram-sha-256';
SET LOCAL statement_timeout = '5s';
SET LOCAL lock_timeout = '1s';
SET LOCAL search_path = pg_catalog,pg_temp;
DO $$ BEGIN
    IF current_database() <> 'bifrost_test' OR current_user <> session_user OR
       NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles
                   WHERE rolname=session_user AND rolsuper) OR
       (SELECT array_agg(version_num ORDER BY version_num) FROM public.alembic_version)
          IS DISTINCT FROM ARRAY['20261016_runtime_source_drain']::varchar[] OR
       (SELECT count(*) FROM pg_catalog.pg_roles r WHERE r.rolname IN
          ('wex_core','wex_incumbent','runtime_lifecycle_guard','runtime_artifact_lock_guard')
          AND NOT r.rolcanlogin AND NOT r.rolinherit AND NOT r.rolsuper
          AND NOT r.rolbypassrls AND NOT r.rolcreaterole AND NOT r.rolcreatedb
          AND NOT r.rolreplication
          AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m
                          WHERE m.member=r.oid OR m.roleid=r.oid)) <> 4 OR
       (SELECT count(*) FROM pg_catalog.pg_authid WHERE rolname IN
          ('wex_core','wex_incumbent')
          AND rolpassword LIKE 'SCRAM-SHA-256$%') <> 2 OR
       EXISTS (SELECT 1 FROM pg_catalog.pg_authid WHERE rolname IN
          ('runtime_lifecycle_guard','runtime_artifact_lock_guard')
          AND rolpassword IS NOT NULL) THEN
        RAISE EXCEPTION 'R1 requires qualified inactive roles on exact common head';
    END IF;
END $$;
-- Undefined/malformed expiry stops. This statement contains no password.
SELECT (isfinite(:'r1_expiry'::timestamptz)
        AND :'r1_expiry'::timestamptz > clock_timestamp()
        AND :'r1_expiry'::timestamptz <= clock_timestamp() + interval '2 hours')
AS r1_inputs_valid \gset
\if :r1_inputs_valid
\else
DO $$ BEGIN RAISE EXCEPTION 'R1 requires finite future login expiry within two hours'; END $$;
\endif
ALTER ROLE wex_core LOGIN VALID UNTIL :'r1_expiry';
ALTER ROLE wex_incumbent LOGIN VALID UNTIL :'r1_expiry';
COMMIT;
