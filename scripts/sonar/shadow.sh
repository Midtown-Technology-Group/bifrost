#!/usr/bin/env bash
# Opt-in manual pilot only. Never a replacement for authoritative Sonar evidence.
set -euo pipefail

shadow_context() {
  mkdir -p .sonar-evidence/raw
  git ls-files -z > .sonar-evidence/raw/tracked-files.nul
  local image
  image="$(docker image inspect bifrost-test-api-dev:latest --format '{{.Id}}')"
  python3 scripts/sonar/shadow.py context --image "$image"
}

shadow_prepare() {
  source scripts/lib/test_helpers.sh
  export COMPOSE_PROJECT_NAME="$(compute_project_name .)"
  export LOG_DIR="/tmp/bifrost-$COMPOSE_PROJECT_NAME"
  mkdir -p "$LOG_DIR"
  shadow_context
  export COMPOSE_FILE="$RUNNER_TEMP/coverage-shadow-compose.yml"
  docker compose --profile '*' -f docker-compose.test.yml -f docker-compose.sonar.yml config > "$COMPOSE_FILE"
  printf 'COMPOSE_FILE=%s\nSONAR_LOG_DIR=%s\n' "$COMPOSE_FILE" "$LOG_DIR" >> "$GITHUB_ENV"
}

shadow_python() {
  docker compose -f "$COMPOSE_FILE" --profile test run --rm --no-deps --workdir /repo \
    test-runner python /repo/scripts/sonar/shadow.py "$@"
}

shadow_export() {
  local lane="$1"
  source scripts/lib/test_helpers.sh
  export COMPOSE_PROJECT_NAME="$(compute_project_name .)"
  export LOG_DIR="/tmp/bifrost-$COMPOSE_PROJECT_NAME"
  docker compose -f "$COMPOSE_FILE" ps -a --format '{{.Service}} {{.ID}}' > "$LOG_DIR/sonar-runtime-producers.txt"
  docker compose -f "$COMPOSE_FILE" stop api api-replica worker scheduler scheduler-fixtures
  # Snapshot every report before validation and before any volume cleanup.
  shadow_python snapshot --lane "$lane"
  if [ "$lane" != baseline ]; then
    docker compose -f "$COMPOSE_FILE" --profile test run --rm --no-deps --workdir /repo \
      test-runner python /app/scripts/combine_sonar_coverage.py
    docker compose -f "$COMPOSE_FILE" --profile test run --rm --no-deps --workdir /repo \
      test-runner python /repo/api/scripts/filter_sonar_coverage.py
    docker compose -f "$COMPOSE_FILE" --profile test run --rm --no-deps --workdir /repo \
      test-runner coverage xml --rcfile=/app/.coveragerc.sonar-report \
        --data-file=/bifrost-results/.coverage.sonar-tracked -o /bifrost-results/coverage-sonar.xml
  fi
  shadow_python seal --lane "$lane"
}

shadow_tools() {
  source scripts/lib/test_helpers.sh
  export COMPOSE_PROJECT_NAME="$(compute_project_name .)"
  export LOG_DIR="/tmp/bifrost-$COMPOSE_PROJECT_NAME"
  local targets=() file filename
  while IFS= read -r -d '' file; do
    filename="${file##*/}"
    if [[ "$filename" == test_*.py || "$filename" == *_test.py ]]; then targets+=("$file"); fi
  done < <(git ls-files -z -- scripts)
  test "${#targets[@]}" -gt 0
  docker compose -f "$COMPOSE_FILE" --profile test run --rm --no-deps \
    --user "$(id -u):$(id -g)" --workdir /repo --env PYTHONPATH=/repo test-runner \
    pytest "${targets[@]}" -o 'python_files=test_*.py *_test.py' -o cache_dir=/tmp/shadow-pytest-cache \
      --cov --cov-branch --cov-config=/repo/api/.coveragerc.sonar --cov-report= \
      --junitxml=/tmp/bifrost/junit-tools.xml
}

case "${1:-}" in
  prepare) shadow_prepare ;;
  baseline-context) shadow_context ;;
  export)
    case "${2:-}" in baseline|unit|supplement|e2e-[1-4]) shadow_export "$2" ;; *) exit 2 ;; esac ;;
  tools) shadow_tools ;;
  own)
    case "${2:-}" in baseline|unit|supplement|e2e-[1-4]) ;;
      *) exit 2 ;; esac
    target="$SONAR_LOG_DIR/coverage-shadow-$2"
    if [ -d "$target" ] && [ ! -L "$target" ]; then
      sudo chown -R --no-dereference "$(id -u):$(id -g)" "$target"
    fi ;;
  *) echo 'Usage: shadow.sh prepare|baseline-context|tools|export LANE' >&2; exit 2 ;;
esac
