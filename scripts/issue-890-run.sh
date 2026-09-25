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
sample_file="$LOG_DIR/issue-890-resources-$(date -u +%Y%m%dT%H%M%SZ).jsonl"
python3 api/scripts/issue_890_sample.py --project "$COMPOSE_PROJECT_NAME" > "$sample_file" &
sampler_pid=$!
trap 'kill "$sampler_pid" 2>/dev/null || true; wait "$sampler_pid" 2>/dev/null || true' EXIT
echo "Resource samples: $sample_file"
docker compose -f docker-compose.test.yml -f scripts/issue-890-compose.yml --profile test run --rm --no-deps \
    -e "BIFROST_BENCH_SOURCE_SHA=$(git rev-parse HEAD)" \
    -e "BIFROST_BENCH_RESOURCE_FILE=/tmp/bifrost/$(basename "$sample_file")" test-runner \
    python scripts/issue_890_load.py "$@"
kill -0 "$sampler_pid"
