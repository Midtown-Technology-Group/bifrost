-- Disposable PostgreSQL experiment, NOT an Alembic migration or runtime store.
-- Real field names come from e58db4955ddd30177bd613f1d85b7e203ad7832a.
CREATE ROLE fixture_owner NOLOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE fixture_guard NOLOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE fixture_python LOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE fixture_rust LOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE fixture_summary LOGIN NOSUPERUSER NOBYPASSRLS;
CREATE SCHEMA ownership AUTHORIZATION fixture_owner;
REVOKE ALL ON SCHEMA ownership FROM PUBLIC;
GRANT USAGE ON SCHEMA ownership TO fixture_python, fixture_rust, fixture_summary, fixture_guard;
SET ROLE fixture_owner;
CREATE TABLE ownership.agent_runs (
  id uuid PRIMARY KEY,
  control_owner text NOT NULL CHECK (control_owner IN ('python', 'rust')),
  status text NOT NULL,
  output jsonb, error text, started_at timestamptz, completed_at timestamptz,
  tokens_used integer NOT NULL DEFAULT 0,
  asked text, did text, answered text, metadata jsonb NOT NULL DEFAULT '{}',
  confidence double precision, confidence_reason text,
  summary_generated_at timestamptz, summary_status text NOT NULL DEFAULT 'pending',
  summary_delivery_id uuid, summary_error text, summary_prompt_version text,
  verdict text, verdict_note text,
  UNIQUE (id, control_owner)
);
CREATE TABLE ownership.execution_attempts (
  id uuid PRIMARY KEY,
  logical_job_type text NOT NULL CHECK (logical_job_type = 'agent_run'),
  logical_job_id uuid NOT NULL,
  status text NOT NULL, lease_token uuid, completed_at timestamptz,
  control_owner text NOT NULL CHECK (control_owner IN ('python', 'rust')),
  FOREIGN KEY (logical_job_id, control_owner)
    REFERENCES ownership.agent_runs(id, control_owner) ON DELETE CASCADE
);
RESET ROLE;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ownership
  TO fixture_python, fixture_rust;
GRANT SELECT, UPDATE ON ownership.agent_runs TO fixture_summary;
-- No runtime login owns any table, trigger, function or schema.
GRANT CREATE ON SCHEMA ownership TO fixture_guard;
SET ROLE fixture_guard;
CREATE FUNCTION ownership.guard_run() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, ownership AS $$
DECLARE actor text;
BEGIN
  -- session_user survives SECURITY DEFINER and SET ROLE. Pool login identity
  -- must consequently be distinct: an application-supplied GUC is not proof.
  actor := CASE session_user
    WHEN 'fixture_python' THEN 'python' WHEN 'fixture_rust' THEN 'rust'
    WHEN 'fixture_summary' THEN 'summary' ELSE NULL END;
  IF actor IS NULL THEN RAISE EXCEPTION 'unknown lifecycle writer' USING ERRCODE='42501'; END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.control_owner IS DISTINCT FROM actor THEN
      RAISE EXCEPTION 'owner mismatch' USING ERRCODE='42501';
    END IF;
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE' AND (NEW.id IS DISTINCT FROM OLD.id OR
      NEW.control_owner IS DISTINCT FROM OLD.control_owner) THEN
    RAISE EXCEPTION 'immutable ownership' USING ERRCODE='42501';
  END IF;
  IF actor = 'summary' AND TG_OP = 'UPDATE' THEN
    -- Closed ancillary fields, not a whole-table exemption. This deliberately
    -- excludes tokens_used: actual summarizer metering needs separate custody.
    IF (to_jsonb(NEW) - ARRAY['asked','did','answered','metadata','confidence',
      'confidence_reason','summary_generated_at','summary_status',
      'summary_delivery_id','summary_error','summary_prompt_version']) IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['asked','did','answered','metadata','confidence',
      'confidence_reason','summary_generated_at','summary_status',
      'summary_delivery_id','summary_error','summary_prompt_version']) THEN
      RAISE EXCEPTION 'summary lifecycle mutation' USING ERRCODE='42501';
    END IF;
    RETURN NEW;
  END IF;
  IF actor IS DISTINCT FROM OLD.control_owner THEN
    RAISE EXCEPTION 'foreign lifecycle owner' USING ERRCODE='42501';
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;
CREATE FUNCTION ownership.guard_attempt() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, ownership AS $$
DECLARE bound_owner text; actor text;
BEGIN
  actor := CASE session_user WHEN 'fixture_python' THEN 'python'
    WHEN 'fixture_rust' THEN 'rust' ELSE NULL END;
  IF TG_OP = 'UPDATE' AND (NEW.id IS DISTINCT FROM OLD.id OR
    NEW.logical_job_id IS DISTINCT FROM OLD.logical_job_id OR
    NEW.logical_job_type IS DISTINCT FROM OLD.logical_job_type OR
    NEW.control_owner IS DISTINCT FROM OLD.control_owner) THEN
    RAISE EXCEPTION 'immutable attempt identity' USING ERRCODE='42501';
  END IF;
  -- The immutable composite FK binds the child to its parent's owner. Checking
  -- the child's bound value also works during an owner-authorized cascade after
  -- the parent disappears. No trigger-level parent-first lock order is claimed.
  bound_owner := CASE WHEN TG_OP = 'DELETE' THEN OLD.control_owner ELSE NEW.control_owner END;
  IF actor IS NULL OR actor IS DISTINCT FROM bound_owner THEN
    RAISE EXCEPTION 'foreign attempt owner' USING ERRCODE='42501';
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;
RESET ROLE;
REVOKE CREATE ON SCHEMA ownership FROM fixture_guard;
REVOKE ALL ON FUNCTION ownership.guard_run(), ownership.guard_attempt() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ownership.guard_run(), ownership.guard_attempt() TO fixture_owner;
SET ROLE fixture_owner;
CREATE TRIGGER lifecycle_owner BEFORE INSERT OR UPDATE OR DELETE ON ownership.agent_runs
  FOR EACH ROW EXECUTE FUNCTION ownership.guard_run();
CREATE TRIGGER attempt_owner BEFORE INSERT OR UPDATE OR DELETE ON ownership.execution_attempts
  FOR EACH ROW EXECUTE FUNCTION ownership.guard_attempt();
RESET ROLE;
