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
export BIFROST_LAB_CLIENT_IMAGE="${BIFROST_LAB_CLIENT_IMAGE:-ghcr.io/midtown-technology-group/bifrost-client@sha256:7e93ce04f21e4e0f24339b936bdd0252ff23436eade4f0bcf4180e4cc41b0b1a}"
export BIFROST_LAB_RENDERER_IMAGE="${BIFROST_LAB_RENDERER_IMAGE:-ghcr.io/mtg-thomas/bifrost-doc-renderer@sha256:6cf3dc6cf78466818690796c2f94d4c6e9c17a485ba2def7aa76343113442be3}"
COMPOSE_PROJECT_NAME="$(compute_project_name .)"
export COMPOSE_PROJECT_NAME
export LOG_DIR="/tmp/bifrost-$COMPOSE_PROJECT_NAME"
export BIFROST_LAB_UID="$(id -u)"
export BIFROST_LAB_GID="$(id -g)"
export BIFROST_LAB_OTEL_DIR="$LOG_DIR/issue-890-otel-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BIFROST_LAB_OTEL_DIR"
sampler_pid=""
cleanup() {
    if [[ -n "$sampler_pid" ]]; then
        kill "$sampler_pid" 2>/dev/null || true
        wait "$sampler_pid" 2>/dev/null || true
    fi
    docker compose -f docker-compose.test.yml -f scripts/issue-890-compose.yml --profile client rm -sf client renderer otel-collector
}
trap cleanup EXIT

./test.sh stack up
./test.sh stack reset
docker compose -f docker-compose.test.yml -f scripts/issue-890-compose.yml \
    up -d --no-deps --wait otel-collector
docker compose -f docker-compose.test.yml -f scripts/issue-890-compose.yml --profile e2e --profile client \
    up -d --no-deps --no-build --wait api scheduler worker client renderer
# Let scheduler startup jobs finish before measuring a steady-state request curve.
sleep 10
sample_file="$LOG_DIR/issue-890-resources-$(date -u +%Y%m%dT%H%M%SZ).jsonl"
python3 api/scripts/issue_890_sample.py --project "$COMPOSE_PROJECT_NAME" > "$sample_file" &
sampler_pid=$!
echo "Resource samples: $sample_file"
docker compose -f docker-compose.test.yml -f scripts/issue-890-compose.yml --profile test run --rm --no-deps \
    -e "BIFROST_BENCH_SOURCE_SHA=$(git rev-parse HEAD)" \
    -e "BIFROST_BENCH_API_PROCESSES=${BIFROST_LAB_API_PROCESSES:-1}" \
    -e "BIFROST_BENCH_CLIENT_IMAGE=$BIFROST_LAB_CLIENT_IMAGE" \
    -e "BIFROST_BENCH_RENDERER_IMAGE=$BIFROST_LAB_RENDERER_IMAGE" \
    -e "BIFROST_BENCH_RESOURCE_FILE=/tmp/bifrost/$(basename "$sample_file")" \
    -e "BIFROST_BENCH_OTEL_FILE=/tmp/bifrost/$(basename "$BIFROST_LAB_OTEL_DIR")/metrics.jsonl" test-runner \
    python scripts/issue_890_load.py "$@"
kill -0 "$sampler_pid"
sleep 16
test -s "$BIFROST_LAB_OTEL_DIR/metrics.jsonl"
grep -q 'bifrost.event_loop.lag' "$BIFROST_LAB_OTEL_DIR/metrics.jsonl"
echo "Event-loop lag metrics: $BIFROST_LAB_OTEL_DIR/metrics.jsonl"
