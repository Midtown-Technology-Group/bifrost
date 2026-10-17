-- Capability floor: 20260924_device_job_logs and its ancestors. No migrations.
-- Compare physical PostgreSQL types/nullability; extra columns remain compatible.
WITH required(table_name, column_name, type_name, not_null) AS (VALUES
    ('devices', 'id', 'uuid', true),
    ('devices', 'organization_id', 'uuid', true),
    ('devices', 'display_name', 'character varying(255)', true),
    ('devices', 'external_ref', 'character varying(255)', false),
    ('devices', 'status', 'character varying(32)', true),
    ('devices', 'agent_version', 'character varying(64)', false),
    ('devices', 'os', 'character varying(128)', false),
    ('devices', 'hostname', 'character varying(255)', false),
    ('devices', 'api_key_hash', 'character varying(255)', false),
    ('devices', 'api_key_enabled', 'boolean', true),
    ('devices', 'enrollment_token_hash', 'character varying(255)', false),
    ('devices', 'enrollment_expires_at', 'timestamp with time zone', false),
    ('devices', 'last_seen_at', 'timestamp with time zone', false),
    ('devices', 'created_at', 'timestamp with time zone', true),
    ('devices', 'updated_at', 'timestamp with time zone', true),
    ('device_jobs', 'id', 'uuid', true),
    ('device_jobs', 'organization_id', 'uuid', true),
    ('device_jobs', 'device_id', 'uuid', true),
    ('device_jobs', 'status', 'character varying(32)', true),
    ('device_jobs', 'script_name', 'character varying(255)', true),
    ('device_jobs', 'script_content', 'text', true),
    ('device_jobs', 'params', 'jsonb', false),
    ('device_jobs', 'timeout_seconds', 'integer', true),
    ('device_jobs', 'max_output_bytes', 'integer', true),
    ('device_jobs', 'requested_by_user_id', 'uuid', false),
    ('device_jobs', 'requested_by_api_key_id', 'uuid', false),
    ('device_jobs', 'requested_by_workflow_id', 'uuid', false),
    ('device_jobs', 'requested_by_execution_id', 'uuid', false),
    ('device_jobs', 'claim_token', 'uuid', false),
    ('device_jobs', 'claimed_at', 'timestamp with time zone', false),
    ('device_jobs', 'agent_session_id', 'uuid', false),
    ('device_jobs', 'last_agent_activity_at', 'timestamp with time zone', false),
    ('device_jobs', 'started_at', 'timestamp with time zone', false),
    ('device_jobs', 'cancel_requested_at', 'timestamp with time zone', false),
    ('device_jobs', 'exit_code', 'integer', false),
    ('device_jobs', 'result', 'text', false),
    ('device_jobs', 'error', 'text', false),
    ('device_jobs', 'log_sequence', 'integer', true),
    ('device_jobs', 'created_at', 'timestamp with time zone', true),
    ('device_jobs', 'updated_at', 'timestamp with time zone', true),
    ('device_job_logs', 'job_id', 'uuid', true),
    ('device_job_logs', 'seq', 'integer', true),
    ('device_job_logs', 'stream', 'character varying(16)', true),
    ('device_job_logs', 'text', 'text', true),
    ('device_job_logs', 'ts', 'timestamp with time zone', false),
    ('device_job_logs', 'received_at', 'timestamp with time zone', true)
), actual AS (
    SELECT c.relname AS table_name, a.attname AS column_name,
           format_type(a.atttypid, a.atttypmod) AS type_name, a.attnotnull AS not_null
    FROM pg_catalog.pg_attribute a
    JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped
), required_constraints(table_name, definition) AS (VALUES
    ('devices', 'PRIMARY KEY (id)'),
    ('devices', 'FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE'),
    ('device_jobs', 'PRIMARY KEY (id)'),
    ('device_jobs', 'FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE'),
    ('device_jobs', 'FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE'),
    ('device_jobs', 'CHECK (((timeout_seconds >= 1) AND (timeout_seconds <= 900)))'),
    ('device_jobs', 'CHECK (((max_output_bytes >= 1024) AND (max_output_bytes <= 16777216)))'),
    ('device_job_logs', 'PRIMARY KEY (job_id, seq)'),
    ('device_job_logs', 'FOREIGN KEY (job_id) REFERENCES device_jobs(id) ON DELETE CASCADE')
), active_predicate(expression) AS (VALUES (
    '(status)::text = ANY ((ARRAY[''pending''::character varying, ''claimed''::character varying, ''running''::character varying])::text[])'
))
SELECT
    EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num <> '')
    AND NOT EXISTS (
        SELECT 1 FROM required r LEFT JOIN actual a
          ON a.table_name = r.table_name AND a.column_name = r.column_name
        WHERE a.column_name IS NULL OR a.type_name <> r.type_name OR a.not_null <> r.not_null
            OR NOT has_column_privilege(current_user, 'public.' || r.table_name, r.column_name, 'SELECT')
    )
    AND NOT EXISTS (
        SELECT 1 FROM required_constraints r WHERE NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_constraint con
            JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = r.table_name AND con.convalidated
                AND NOT con.condeferrable AND pg_get_constraintdef(con.oid) = r.definition
        )
    )
    AND EXISTS (
        SELECT 1 FROM pg_catalog.pg_index i
        JOIN pg_catalog.pg_class idx ON idx.oid = i.indexrelid
        WHERE i.indrelid = 'public.device_jobs'::regclass
            AND idx.relname = 'uq_device_jobs_one_active'
            AND i.indisunique AND i.indisvalid AND i.indisready
            AND pg_get_indexdef(i.indexrelid, 1, true) = 'device_id'
            AND i.indnkeyatts = 1 AND i.indnatts = 1
            -- The only permitted deparse variation is one pair of parentheses
            -- enclosing the entire identical boolean expression. No tokens,
            -- casts, statuses, operators or internal grouping are normalized.
            AND pg_get_expr(i.indpred, i.indrelid) IN (
                SELECT expression FROM active_predicate
                UNION ALL SELECT '(' || expression || ')' FROM active_predicate
            )
    );
