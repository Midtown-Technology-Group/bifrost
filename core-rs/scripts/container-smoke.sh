#!/usr/bin/env bash
# Actual production binary smoke against an already-running isolated test stack.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(git -C "$script_dir" rev-parse --show-toplevel)"
cd "$repo_root"
source scripts/lib/test_helpers.sh
project="$(compute_project_name .)"
image="${1:?Usage: container-smoke.sh <built-runtime-image>}"
owned=()
cleanup() {
    for container in "${owned[@]}"; do
        docker rm -f "$container" >/dev/null 2>&1 || true
    done
}
trap cleanup EXIT

curl_from_api() {
    docker compose -p "$project" -f docker-compose.test.yml exec -T api \
        curl --silent --show-error --max-time 4 "$@"
}

start() {
    local name="$1" database="$2"
    if docker container inspect "$name" >/dev/null 2>&1; then
        echo 'Smoke container already exists; preserving it' >&2
        return 1
    fi
    docker run -d --name "$name" --network "${project}_default" \
        --read-only --cap-drop ALL --security-opt no-new-privileges \
        --pids-limit 64 --memory 128m --cpus 1 \
        -e "BIFROST_DATABASE_URL=$database" \
        -e BIFROST_RUST_SHUTDOWN_TIMEOUT_MS=2000 "$image" >/dev/null
    owned+=("$name")
    local response=''
    for ((attempt=0; attempt<15; attempt++)); do
        if response="$(curl_from_api --fail "http://$name:8001/health" 2>/dev/null)"; then
            break
        fi
        sleep 1
    done
    test "$response" = '{"status":"ok"}'
}

stop_cleanly() {
    local name="$1"
    docker kill --signal=SIGTERM "$name" >/dev/null
    local status
    status="$(timeout 15 docker wait "$name")"
    test "$status" = '0'
}

valid="${project}-rust-runtime-valid"
start "$valid" 'postgresql://bifrost:bifrost_test@pgbouncer:5432/bifrost_test'
test "$(curl_from_api --fail "http://$valid:8001/ready")" = '{"status":"ready"}'
test "$(curl_from_api --fail -H 'Authorization: Bearer never-log-this' -H 'X-Request-Id: never-log-this' "http://$valid:8001/health?token=never-log-this")" = '{"status":"ok"}'
stop_cleanly "$valid"
runtime_logs="$(docker logs "$valid" 2>&1)"
if [[ "$runtime_logs" == *never-log-this* ]]; then
    echo 'Runtime logs exposed synthetic request credentials' >&2
    exit 1
fi

# The test stack migrates bifrost_test, not the default postgres database. Verify
# that fixture assumption read-only before testing a real schema failure.
test "$(docker compose -p "$project" -f docker-compose.test.yml exec -T postgres \
    psql -U bifrost -d postgres -tAc "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname='public' AND tablename IN ('alembic_version','devices','device_jobs','device_job_logs')")" = '0'
incompatible="${project}-rust-runtime-incompatible"
start "$incompatible" 'postgresql://bifrost:bifrost_test@postgres:5432/postgres'
test "$(curl_from_api -o /dev/null -w '%{http_code}' "http://$incompatible:8001/ready")" = '503'
stop_cleanly "$incompatible"
echo 'PASS: production image health/readiness, incompatible schema, sanitized logs and SIGTERM'
