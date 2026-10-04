"""Retain terminal workflow registration dispositions, including across rollback."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261003_workflow_retirement"
down_revision = "20261001_solution_src_account"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workflows", sa.Column("retirement_evidence", postgresql.JSONB(none_as_null=True), nullable=True))
    op.create_check_constraint("ck_workflow_retirement_terminal", "workflows",
        "retirement_evidence IS NULL OR (is_active IS FALSE AND solution_id IS NULL "
        "AND endpoint_enabled IS FALSE AND public_endpoint IS FALSE AND api_key_enabled IS FALSE)")
    op.execute("""
        CREATE FUNCTION enforce_workflow_retirement_integrity() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.retirement_evidence IS NOT NULL THEN
                    RAISE EXCEPTION 'retired workflow registration history cannot be deleted'
                        USING ERRCODE = 'check_violation';
                END IF;
                RETURN OLD;
            END IF;
            IF TG_OP = 'INSERT' AND NEW.retirement_evidence IS NOT NULL THEN
                RAISE EXCEPTION 'retirement requires an existing workflow registration'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.retirement_evidence IS NOT NULL
                AND NEW.retirement_evidence IS DISTINCT FROM OLD.retirement_evidence THEN
                RAISE EXCEPTION 'workflow retirement evidence is write-once'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.retirement_evidence IS NOT NULL THEN
                IF NEW.is_active IS DISTINCT FROM FALSE OR NEW.solution_id IS NOT NULL
                    OR NEW.endpoint_enabled IS DISTINCT FROM FALSE
                    OR NEW.public_endpoint IS DISTINCT FROM FALSE
                    OR NEW.api_key_enabled IS DISTINCT FROM FALSE THEN
                    RAISE EXCEPTION 'retired workflow registration must remain loose with closed admission'
                        USING ERRCODE = 'check_violation';
                END IF;
                IF NEW.id IS DISTINCT FROM OLD.id OR NEW.path IS DISTINCT FROM OLD.path
                    OR NEW.function_name IS DISTINCT FROM OLD.function_name
                    OR NEW.organization_id IS DISTINCT FROM OLD.organization_id THEN
                    RAISE EXCEPTION 'retired workflow registration identity is immutable'
                        USING ERRCODE = 'check_violation';
                END IF;
                IF NOT COALESCE(
                    jsonb_typeof(NEW.retirement_evidence) = 'object'
                    AND NEW.retirement_evidence->>'schema_version' = 'bifrost.workflow-registration-retirement/v1'
                    AND NEW.retirement_evidence->'identity'->>'workflow_id' = NEW.id::text
                    AND NEW.retirement_evidence->'identity'->>'raw_path' = NEW.path
                    AND NEW.retirement_evidence->'identity'->>'path' = ltrim(replace(NEW.path, chr(92), '/'), '/')
                    AND NEW.retirement_evidence->'identity'->>'function_name' = NEW.function_name
                    AND (NEW.retirement_evidence->'identity') ? 'organization_id'
                    AND (NEW.retirement_evidence->'identity'->>'organization_id') IS NOT DISTINCT FROM NEW.organization_id::text,
                    FALSE) THEN
                    RAISE EXCEPTION 'workflow retirement evidence identity is invalid'
                        USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_workflow_retirement_integrity
            BEFORE INSERT OR UPDATE OR DELETE ON workflows
            FOR EACH ROW EXECUTE FUNCTION enforce_workflow_retirement_integrity();
    """)


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM workflows WHERE retirement_evidence IS NOT NULL) THEN
            RAISE EXCEPTION 'retain workflow retirement evidence during application rollback';
        END IF;
    END; $$""")
    op.execute("DROP TRIGGER trg_workflow_retirement_integrity ON workflows")
    op.execute("DROP FUNCTION enforce_workflow_retirement_integrity()")
    op.drop_constraint("ck_workflow_retirement_terminal", "workflows", type_="check")
    op.drop_column("workflows", "retirement_evidence")
