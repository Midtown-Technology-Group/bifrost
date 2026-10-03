#!/usr/bin/env bash
# Supported CI source gate; never execute on the physical Proxmox host.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
source scripts/lib/test_helpers.sh
mode="${1:?format or verify required}"
case "$mode" in format|verify) ;; *) exit 2 ;; esac
test -z "$(git status --porcelain --untracked-files=all)"
test "$(git rev-parse HEAD)" = "${GITHUB_SHA:?exact hosted candidate required}"
git -c credential.helper= -c http.extraheader= fetch origin main
git merge-base --is-ancestor origin/main HEAD
export COMPOSE_FILE=docker-compose.test.yml
export COMPOSE_PROJECT_NAME="$(compute_project_name .)"
export LOG_DIR="/tmp/bifrost-$COMPOSE_PROJECT_NAME"
evidence="${RUNNER_TEMP:?private hosted evidence root required}/workflow-sql-source"
formatter="$COMPOSE_PROJECT_NAME-sql-formatter"
image="$COMPOSE_PROJECT_NAME-sql-tools"
test -z "$(docker ps -aq --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")"
test -z "$(docker volume ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")"
test -z "$(docker network ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")"
test -z "$(docker ps -aq --filter "name=^/$formatter$")"
test ! -e "$evidence"
umask 077
mkdir -p "$evidence"
printf 'WORKFLOW_SQL_SOURCE_EVIDENCE=%s\n' "$evidence" >> "${GITHUB_ENV:?artifact path required}"
formatter_created=0
tests_started=0
targeted_started=0
image_id=
cleanup() {
    original=$?
    trap - EXIT
    set +e
    cleanup_status=0
    if test "$targeted_started" = 1; then
        cp "$LOG_DIR/test-results.xml" "$evidence/test-results.xml" || cleanup_status=1
    fi
    if test "$formatter_created" = 1; then
        facts="$(docker inspect "$formatter" --format '{{.Name}}|{{index .Config.Labels "com.docker.compose.project"}}|{{.Image}}')" || cleanup_status=1
        if test "$facts" = "/$formatter|$COMPOSE_PROJECT_NAME|$image_id"; then
            docker rm -f "$formatter" > "$evidence/formatter-removal.txt" 2>&1 || cleanup_status=1
        else
            cleanup_status=1
        fi
    fi
    if test "$tests_started" = 1; then
        ./test.sh stack down > "$evidence/cleanup.log" 2>&1 || cleanup_status=1
    fi
    inspection_status=0
    containers="$(docker ps -aq --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")" || inspection_status=1
    volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")" || inspection_status=1
    networks="$(docker network ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")" || inspection_status=1
    test -z "$containers$volumes$networks" || inspection_status=1
    printf 'cleanup_status=%s\ninspection_status=%s\ncontainers=%s\nvolumes=%s\nnetworks=%s\n' "$cleanup_status" "$inspection_status" "$containers" "$volumes" "$networks" > "$evidence/project-resources.txt" || inspection_status=1
    if test "$original" = 0 && test "$cleanup_status$inspection_status" != 00; then original=1; fi
    if ! printf '%s\n' "$original" > "$evidence/exit-status.txt"; then
        if test "$original" = 0; then original=1; fi
    fi
    exit "$original"
}
trap cleanup EXIT
git rev-parse HEAD HEAD^{tree} > "$evidence/source.txt"
printf '%s\n' "$mode" > "$evidence/mode.txt"
sha256sum core-rs/crates/bifrost-db/src/workflow_parity.rs core-rs/crates/bifrost-db/src/lib.rs core-rs/crates/bifrost-db/Cargo.toml core-rs/Cargo.lock api/tests/parity/test_workflow_sql.py scripts/ci/workflow-sql-source.sh .github/workflows/workflow-sql-source.yml > "$evidence/source-hashes.txt"
if test "$mode" = format; then
    docker build --target toolchain -t "$image" -f core-rs/Dockerfile core-rs > "$evidence/toolchain-build.log" 2>&1
    image_id="$(docker image inspect "$image" --format '{{.Id}}')"
    printf '%s\n' "$image_id" > "$evidence/formatter-image.txt"
    formatter_created=1
    docker create --name "$formatter" --label "com.docker.compose.project=$COMPOSE_PROJECT_NAME" --network none -v "$PWD/core-rs:/workspace/core-rs" "$image" cargo fmt --all > "$evidence/formatter-container.txt"
    docker start -a "$formatter" > "$evidence/formatter.log" 2>&1
    test "$(docker inspect "$formatter" --format '{{.State.ExitCode}}')" = 0
    git diff --name-only > "$evidence/format-paths.txt"
    while IFS= read -r path; do
        case "$path" in core-rs/crates/bifrost-db/src/workflow_parity.rs|core-rs/crates/bifrost-db/src/lib.rs) ;; *) exit 1 ;; esac
    done < "$evidence/format-paths.txt"
    git diff --binary > "$evidence/format.patch"
    # Formatting modifies this disposable checkout. No candidate checks or SQL
    # acceptance is inferred from this mode; root reviews/applies the patch.
else
    tests_started=1
    ./test.sh pre-pr > "$evidence/pre-pr.log" 2>&1
    test -z "$(git status --porcelain --untracked-files=all)"
    docker build --target checks -t "$image" -f core-rs/Dockerfile core-rs > "$evidence/rust-checks.log" 2>&1
    image_id="$(docker image inspect "$image" --format '{{.Id}}')"
    printf '%s\n' "$image_id" > "$evidence/checks-image.txt"
    # Nondefault SQL unit tests run explicitly; they require no live DB.
    formatter_created=1
    docker create --name "$formatter" --label "com.docker.compose.project=$COMPOSE_PROJECT_NAME" --network none "$image" cargo test --locked --offline -p bifrost-db --features workflow-sql-parity --lib > "$evidence/unit-container.txt"
    docker start -a "$formatter" > "$evidence/sql-unit.log" 2>&1
    test "$(docker inspect "$formatter" --format '{{.State.ExitCode}}')" = 0
    rm -f "$LOG_DIR/test-results.xml"
    targeted_started=1
    ./test.sh tests/parity/test_workflow_sql.py -v > "$evidence/sql-order.log" 2>&1
    test -z "$(git status --porcelain --untracked-files=all)"
fi
