#!/usr/bin/env bash
# Run an isolated issue-890 load sweep; results remain in this worktree's /tmp log directory.
set -euo pipefail

cd "$(dirname "$0")/.."
if [[ -n $(git status --porcelain) ]]; then
    echo "Commit the isolated lab source before recording a benchmark." >&2
    exit 2
fi
# shellcheck source=scripts/lib/test_helpers.sh
source scripts/lib/test_helpers.sh
COMPOSE_PROJECT_NAME="$(compute_project_name .)"
export COMPOSE_PROJECT_NAME
export LOG_DIR="/tmp/bifrost-$COMPOSE_PROJECT_NAME"

./test.sh stack up
./test.sh stack reset
docker compose -f docker-compose.test.yml -f scripts/issue-890-compose.yml --profile e2e \
    up -d --no-deps --no-build --wait api scheduler worker
docker compose -f docker-compose.test.yml -f scripts/issue-890-compose.yml --profile test run --rm --no-deps \
    -e "BIFROST_BENCH_SOURCE_SHA=$(git rev-parse HEAD)" test-runner \
    python scripts/issue_890_load.py "$@"
