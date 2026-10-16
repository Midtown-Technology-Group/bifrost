-- REVIEW ARTIFACT ONLY. NOT an Alembic revision, installer, or approved migration.
-- main e42999395231ebc536711e5015422edfb75613a7; CRED 3918 is NOT merged.
-- No roles/grants/writer guards are generated from an incomplete writer catalog.
-- Explicit ROLLBACK protects an accidental interactive structural preview;
-- DO NOT RUN: approved admission procedures/state fences/deletion policy absent.
BEGIN;

-- Native attempt identity is id UUID -> executions.id UUID, not generic attempts.
ALTER TABLE workflow_execution_attempts ADD CONSTRAINT uq_owner_attempt_execution
  UNIQUE (id, execution_id);
CREATE TABLE runtime_workflow_owners (
  assignment_id uuid PRIMARY KEY,
  execution_id uuid NOT NULL UNIQUE REFERENCES executions(id) ON DELETE RESTRICT,
  -- absence means legacy Python, only where admitted by an approved native path;
  -- insertions are reserved to atomic Rust admission, never client chosen.
  admission_request_id uuid NOT NULL UNIQUE,
  admission_request_digest text NOT NULL CHECK (admission_request_digest ~ '^sha256:[0-9a-f]{64}$'),
  caller_snapshot_bytes bytea NOT NULL,
  caller_snapshot_digest text NOT NULL CHECK (caller_snapshot_digest ~ '^sha256:[0-9a-f]{64}$'),
  accepted_source_bytes bytea NOT NULL,
  accepted_source_digest text NOT NULL CHECK (accepted_source_digest ~ '^sha256:[0-9a-f]{64}$'),
  committed_at timestamptz NOT NULL,
  UNIQUE (assignment_id, execution_id)
);
CREATE TABLE runtime_workflow_sessions (
  session_id uuid PRIMARY KEY,
  assignment_id uuid NOT NULL,
  execution_id uuid NOT NULL,
  attempt_id uuid NOT NULL,
  -- Conservative first package: ONE session for an attempt, even after close.
  -- Pre-Start session replacement needs separately reviewed native recovery.
  UNIQUE (attempt_id),
  FOREIGN KEY (assignment_id, execution_id)
    REFERENCES runtime_workflow_owners(assignment_id, execution_id) ON DELETE RESTRICT,
  FOREIGN KEY (attempt_id, execution_id)
    REFERENCES workflow_execution_attempts(id, execution_id) ON DELETE RESTRICT,
  worker_incarnation_id uuid NOT NULL,
  supervisor_incarnation_id uuid NOT NULL,
  runtime_incarnation_id uuid NOT NULL,
  -- Validated trusted supervisor document; NOT a child-supplied PID assertion.
  process_identity_bytes bytea NOT NULL,
  prepare_message_id uuid NOT NULL UNIQUE,
  preparation_digest text NOT NULL CHECK (preparation_digest ~ '^sha256:[0-9a-f]{64}$'),
  artifact_digest text NOT NULL CHECK (artifact_digest ~ '^sha256:[0-9a-f]{64}$'),
  closure_digest text NOT NULL CHECK (closure_digest ~ '^sha256:[0-9a-f]{64}$'),
  provision_slot_id uuid NOT NULL UNIQUE,
  opened_at timestamptz NOT NULL,
  UNIQUE (session_id, assignment_id)
);
CREATE TABLE runtime_workflow_receipts (
  receipt_id uuid PRIMARY KEY,
  assignment_id uuid NOT NULL REFERENCES runtime_workflow_owners(assignment_id) ON DELETE RESTRICT,
  session_id uuid,
  operation_kind text NOT NULL CHECK (operation_kind IN
    ('admission','prepared','start','provision','report','cancel','close','stop')),
  operation_id uuid NOT NULL,
  committed_start_id uuid UNIQUE,
  CHECK ((operation_kind = 'start') = (committed_start_id IS NOT NULL)),
  exact_document_bytes bytea NOT NULL,
  document_digest text NOT NULL CHECK (document_digest ~ '^sha256:[0-9a-f]{64}$'),
  committed_at timestamptz NOT NULL,
  FOREIGN KEY (session_id, assignment_id)
    REFERENCES runtime_workflow_sessions(session_id, assignment_id) ON DELETE RESTRICT,
  CHECK ((operation_kind = 'admission' AND session_id IS NULL)
      OR operation_kind = 'cancel'
      OR (operation_kind NOT IN ('admission','cancel') AND session_id IS NOT NULL)),
  UNIQUE (receipt_id, assignment_id, session_id, operation_kind)
);
-- NULL-session operations cannot evade idempotency. Admission additionally has
-- global admission_request_id above, before any new owner can be accepted.
CREATE UNIQUE INDEX uq_owner_receipt_no_session ON runtime_workflow_receipts
  (assignment_id, operation_kind, operation_id) WHERE session_id IS NULL;
CREATE UNIQUE INDEX uq_owner_receipt_session ON runtime_workflow_receipts
  (session_id, operation_kind, operation_id) WHERE session_id IS NOT NULL;
CREATE UNIQUE INDEX uq_owner_admission_receipt ON runtime_workflow_receipts
  (assignment_id) WHERE operation_kind = 'admission';
CREATE UNIQUE INDEX uq_owner_session_single_phase ON runtime_workflow_receipts
  (session_id, operation_kind)
  WHERE operation_kind IN ('prepared','start','provision','close','stop');
-- 'start' exact_document carries committed_start_id + native claim/attempt/
-- prepare/deadline/expiry binding. Same digest never grants new authority.
-- Native projection identity stays in exact validated receipt document; no
-- polymorphic SQL FK to unenumerated event/usage/log tables is invented here.

-- Append-only rows make close/stop and original identity permanent. This does
-- NOT authorize INSERT or prove a legal transition. Approved role-bound narrow
-- operations must lock native attempt then validate existing receipts before
-- inserting. No arbitrary session_user role names/GUC are accepted here.
CREATE FUNCTION runtime_owner_evidence_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  RAISE EXCEPTION 'runtime owner evidence is append-only' USING ERRCODE='42501';
END $$;
CREATE TRIGGER owner_evidence_immutable BEFORE UPDATE OR DELETE ON runtime_workflow_owners
 FOR EACH ROW EXECUTE FUNCTION runtime_owner_evidence_immutable();
CREATE TRIGGER session_evidence_immutable BEFORE UPDATE OR DELETE ON runtime_workflow_sessions
 FOR EACH ROW EXECUTE FUNCTION runtime_owner_evidence_immutable();
CREATE TRIGGER receipt_evidence_immutable BEFORE UPDATE OR DELETE ON runtime_workflow_receipts
 FOR EACH ROW EXECUTE FUNCTION runtime_owner_evidence_immutable();
-- TRUNCATE must be revoked from service roles; row triggers do not cover it.
-- All function/table ownership and EXECUTE ACLs need exact reviewed role DDL.
-- No issuance/grant FK: CRED is separate unmerged source; approved session-close
-- coupling must be integrated there under the same native fence, not guessed.
ROLLBACK;
