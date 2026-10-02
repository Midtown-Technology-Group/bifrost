"""Run only on isolated hosted CI / authorized test VM, never the physical host."""
import json
import os
import subprocess
import sys

RUST = "11111111-1111-4111-8111-111111111111"
PYTHON = "22222222-2222-4222-8222-222222222222"
ATTEMPT = "33333333-3333-4333-8333-333333333333"
checks = []


def sql(role, statement, denied=False):
    result = subprocess.run(
        ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=verbose",
         "-h", "127.0.0.1", "-U", role, "-d", "ownership_fixture", "-Atc", statement],
        capture_output=True, text=True, env={**os.environ, "PGCONNECT_TIMEOUT": "5"},
        timeout=10,
    )
    if denied:
        assert result.returncode != 0 and "42501" in result.stderr, result.stderr
    else:
        assert result.returncode == 0, result.stderr
    checks.append({"role": role, "sql": statement, "expected_denial": denied, "passed": True})
    return result.stdout.strip()


for role, identity, owner in [("fixture_python", PYTHON, "python"), ("fixture_rust", RUST, "rust")]:
    sql(role, f"INSERT INTO ownership.agent_runs(id,control_owner,status) VALUES ('{identity}','{owner}','running')")
    sql(role, f"UPDATE ownership.agent_runs SET output='{{\"ready\":true}}' WHERE id='{identity}'")
for role, identity in [("fixture_python", RUST), ("fixture_rust", PYTHON)]:
    for mutation in ["status='completed'", "output='{}'", "tokens_used=99", "control_owner='python'"]:
        sql(role, f"UPDATE ownership.agent_runs SET {mutation} WHERE id='{identity}'", True)
    sql(role, f"DELETE FROM ownership.agent_runs WHERE id='{identity}'", True)
sql("fixture_python", "INSERT INTO ownership.agent_runs(id,control_owner,status) VALUES ('44444444-4444-4444-8444-444444444444','rust','running')", True)
sql("fixture_summary", f"UPDATE ownership.agent_runs SET asked='question', answered='answer', summary_status='completed' WHERE id='{RUST}'")
for mutation in ["status='completed'", "output='{}'", "tokens_used=99", "verdict='up'"]:
    sql("fixture_summary", f"UPDATE ownership.agent_runs SET {mutation} WHERE id='{RUST}'", True)
sql("fixture_rust", f"INSERT INTO ownership.execution_attempts VALUES ('{ATTEMPT}','agent_run','{RUST}','running',NULL,NULL)")
sql("fixture_python", f"UPDATE ownership.execution_attempts SET status='failed' WHERE id='{ATTEMPT}'", True)
sql("fixture_python", f"DELETE FROM ownership.execution_attempts WHERE id='{ATTEMPT}'", True)
sql("fixture_rust", f"UPDATE ownership.execution_attempts SET logical_job_id='{PYTHON}' WHERE id='{ATTEMPT}'", True)
sql("fixture_python", "SET ROLE fixture_rust", True)
sql("fixture_python", "SET session_replication_role=replica", True)
sql("fixture_python", "ALTER TABLE ownership.agent_runs DISABLE TRIGGER ALL", True)
sql("fixture_python", "TRUNCATE ownership.agent_runs CASCADE", True)
sql("fixture_python", f"SET bifrost.control_owner='rust'; UPDATE ownership.agent_runs SET status='failed' WHERE id='{RUST}'", True)
sql("fixture_rust", f"UPDATE ownership.agent_runs SET status='completed',completed_at=now() WHERE id='{RUST}'")
assert sql("fixture_python", f"SELECT status,output->>'ready',answered FROM ownership.agent_runs WHERE id='{RUST}'") == "completed|true|answer"
identity = sql("fixture_rust", "SELECT session_user,current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
assert identity == "fixture_rust|fixture_rust|f|f", identity
assert sql("fixture_rust", "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner WHERE c.relnamespace='ownership'::regnamespace AND r.rolname IN ('fixture_python','fixture_rust','fixture_summary')") == "0"
json.dump({"status": "candidate-fixture-only", "checks": checks}, sys.stdout, indent=2)
print()
