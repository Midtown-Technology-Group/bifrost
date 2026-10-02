"""Disposable PgBouncer identity/race experiment; CI or authorized VM only."""
import concurrent.futures
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

RUST = "11111111-1111-4111-8111-111111111111"
PYTHON = "22222222-2222-4222-8222-222222222222"
checks = []


def command(role, query, database="ownership_fixture", app="ownership-check"):
    return subprocess.run(
        ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=verbose",
         "-h", "127.0.0.1", "-p", "16432", "-U", role, "-d", database, "-Atc", query],
        env={**os.environ, "PGCONNECT_TIMEOUT": "5", "PGAPPNAME": app},
        capture_output=True, text=True, timeout=10,
    )


def check(role, query, denied=False, database="ownership_fixture"):
    result = command(role, query, database)
    if denied:
        assert result.returncode != 0 and "42501" in result.stderr, result.stderr
    else:
        assert result.returncode == 0, result.stderr
    checks.append({"frontend_login": role, "database": database, "query": query,
                   "expected_denial": denied, "passed": True})
    return result.stdout.strip()


version = subprocess.check_output(["pgbouncer", "--version"], text=True).strip()
with tempfile.TemporaryDirectory(prefix="bifrost-owned-pool-") as temporary:
    root = Path(temporary)
    users = root / "users.txt"
    users.write_text('"fixture_python" ""\n"fixture_rust" ""\n"fixture_summary" ""\n')
    users.chmod(0o600)
    config = root / "pgbouncer.ini"
    config.write_text(f"""[databases]
ownership_fixture = host=127.0.0.1 port=5432 dbname=ownership_fixture
forced_identity = host=127.0.0.1 port=5432 dbname=ownership_fixture user=fixture_python
[pgbouncer]
listen_addr = 127.0.0.1
listen_port = 16432
unix_socket_dir = {root}
auth_type = trust
auth_file = {users}
pool_mode = transaction
default_pool_size = 3
max_client_conn = 12
server_reset_query_always = 1
pidfile = {root / 'pool.pid'}
logfile = {root / 'pool.log'}
""")
    config.chmod(0o600)
    with (root / "process.log").open("w") as log:
        pool = subprocess.Popen(["pgbouncer", str(config)], stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 10
            while True:
                assert pool.poll() is None, (root / "process.log").read_text()
                try:
                    with socket.create_connection(("127.0.0.1", 16432), timeout=0.2):
                        break
                except OSError:
                    assert time.monotonic() < deadline, "task pool startup deadline"
                    time.sleep(0.05)
            for role in ("fixture_python", "fixture_rust", "fixture_summary"):
                identity = check(role, "SELECT session_user,current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
                assert identity == f"{role}|{role}|f|f", identity
            check("fixture_python", f"UPDATE ownership.agent_runs SET status='failed' WHERE id='{RUST}'", True)
            check("fixture_rust", f"UPDATE ownership.agent_runs SET status='failed' WHERE id='{PYTHON}'", True)
            check("fixture_summary", f"UPDATE ownership.agent_runs SET answered='pooled answer' WHERE id='{RUST}'")
            check("fixture_summary", f"UPDATE ownership.agent_runs SET status='failed' WHERE id='{RUST}'", True)
            check("fixture_python", "SET ROLE fixture_rust", True)
            # Negative control: a distinct frontend name does not imply a
            # distinct authenticated backend when the database forces user=.
            identity = check("fixture_rust", "SELECT session_user,current_user", database="forced_identity")
            assert identity == "fixture_python|fixture_python", identity
            check("fixture_rust", f"UPDATE ownership.agent_runs SET status='failed' WHERE id='{RUST}'", True, "forced_identity")

            # Observe the live owner transaction before racing the incumbent.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                owner = executor.submit(command, "fixture_rust",
                    f"BEGIN; UPDATE ownership.agent_runs SET status='running' WHERE id='{RUST}'; SELECT pg_sleep(1); UPDATE ownership.agent_runs SET status='completed' WHERE id='{RUST}'; COMMIT",
                    app="owned-fixture-race")
                deadline = time.monotonic() + 5
                while check("fixture_rust", "SELECT count(*) FROM pg_stat_activity WHERE usename='fixture_rust' AND application_name='owned-fixture-race' AND state='active' AND query LIKE '%pg_sleep%'") == "0":
                    assert not owner.done() and time.monotonic() < deadline, "owner transaction was not observed live"
                    time.sleep(0.02)
                check("fixture_python", f"UPDATE ownership.agent_runs SET status='failed' WHERE id='{RUST}'", True)
                outcome = owner.result(timeout=5)
                assert outcome.returncode == 0, outcome.stderr
            assert check("fixture_python", f"SELECT status,answered FROM ownership.agent_runs WHERE id='{RUST}'") == "completed|pooled answer"
            check("fixture_rust", f"BEGIN; UPDATE ownership.agent_runs SET status='failed' WHERE id='{RUST}'; ROLLBACK")
            assert check("fixture_python", f"SELECT status FROM ownership.agent_runs WHERE id='{RUST}'") == "completed"
        finally:
            pool.terminate()
            try:
                pool.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pool.kill()
                pool.wait(timeout=5)
            assert pool.poll() is not None

json.dump({"status": "candidate-fixture-only", "pgbouncer_version": version,
           "pool_mode": "transaction", "runtime_credentials": "synthetic-local-trust-only",
           "forced_backend_identity_detected": True, "task_pool_reaped": True,
           "checks": checks}, sys.stdout, indent=2)
print()
