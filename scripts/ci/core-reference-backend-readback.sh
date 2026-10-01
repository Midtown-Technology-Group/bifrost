#!/usr/bin/env bash
# Sanitized settings readback in the isolated supported reference containers.
# This does not prove a worker handled a delivery; the scenarios must prove that.
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$TASK_ROOT"
source scripts/lib/test_helpers.sh
export COMPOSE_PROJECT_NAME
COMPOSE_PROJECT_NAME="$(compute_project_name .)"
export LOG_DIR="/tmp/bifrost-$COMPOSE_PROJECT_NAME"
if [ "${BIFROST_TEST_WORK_DELIVERY_BACKEND:-}" != postgres ]; then
    echo 'Select the isolated PostgreSQL reference lane explicitly.' >&2
    exit 1
fi
probe='from src.config import get_settings; s=get_settings(); assert s.environment == "testing", "refuse non-testing environment"; assert s.work_delivery_backend == "postgres", "refuse non-PostgreSQL delivery"; print("environment=testing work_delivery_backend=postgres")'
for service in api api-replica worker scheduler; do
    printf '%s: ' "$service"
    docker compose -f docker-compose.test.yml exec -T "$service" python -c "$probe"
done
printf 'test-runner: '
docker compose -f docker-compose.test.yml --profile test run --rm --no-deps test-runner python -c "$probe"
