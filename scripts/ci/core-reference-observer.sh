#!/usr/bin/env bash
# Only this worktree's explicit, test-only core observer is managed here.
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$TASK_ROOT"
source scripts/lib/test_helpers.sh
export COMPOSE_PROJECT_NAME
COMPOSE_PROJECT_NAME="$(compute_project_name .)"
export LOG_DIR="/tmp/bifrost-$COMPOSE_PROJECT_NAME"
if [ "${CORE_REFERENCE_ISOLATED:-0}" != 1 ] || [ "${BIFROST_TEST_WORK_DELIVERY_BACKEND:-}" != postgres ]; then
    echo 'Select the isolated PostgreSQL core reference lane explicitly.' >&2
    exit 1
fi
case "${1:-}" in
    start)
        # Explicit CLI profiles replace COMPOSE_PROFILES. Start this service by
        # name after the ordinary e2e stack; do not recreate its dependencies.
        docker compose -f docker-compose.test.yml --profile core-reference up -d --no-build --no-deps core-sdk-observer
        wait_for_service docker-compose.test.yml core-sdk-observer curl --fail --silent http://localhost:8080/health/ready
        ;;
    stop)
        docker compose -f docker-compose.test.yml --profile core-reference rm -sf core-sdk-observer
        ;;
    *) echo 'Usage: core-reference-observer.sh {start|stop}' >&2; exit 2 ;;
esac
