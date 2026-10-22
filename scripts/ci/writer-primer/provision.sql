-- Only a new, task-owned disposable cluster; never application authority.
\set ON_ERROR_STOP on
BEGIN;
CREATE ROLE wex_incumbent LOGIN PASSWORD 'synthetic_primer_incumbent'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE wex_core LOGIN PASSWORD 'synthetic_primer_core'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
REVOKE CONNECT, TEMPORARY ON DATABASE bifrost_wex_primer FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE bifrost_wex_primer TO wex_incumbent, wex_core;
GRANT USAGE ON SCHEMA public TO wex_incumbent, wex_core;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO wex_incumbent, wex_core;
COMMIT;
-- No callable/ownership/guard repair. Unexpected authority must fail probes.
