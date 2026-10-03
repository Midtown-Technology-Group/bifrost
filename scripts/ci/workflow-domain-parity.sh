#!/usr/bin/env bash
# Test-only Python/PG versus Rust field-plan gate. Never run on the Proxmox host.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
source scripts/lib/test_helpers.sh
export COMPOSE_FILE=docker-compose.test.yml
export COMPOSE_PROJECT_NAME
COMPOSE_PROJECT_NAME="$(compute_project_name .)"
export LOG_DIR="/tmp/bifrost-$COMPOSE_PROJECT_NAME"
evidence="$LOG_DIR/workflow-domain-parity"
builder="$COMPOSE_PROJECT_NAME-workflow-domain-builder"
checks_image="$COMPOSE_PROJECT_NAME-workflow-domain-checks"
parity_started=0

# Refuse custody of pre-existing resources or evidence. Inspection failure fails.
test -z "$(git status --porcelain --untracked-files=all)"
initial_containers="$(docker ps -aq --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")"
initial_volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")"
initial_networks="$(docker network ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")"
test -z "$initial_containers$initial_volumes$initial_networks"
test ! -e "$evidence"
mkdir -p "$evidence"
# The unchanged runner entrypoint recursively chowns its mounted /tmp/bifrost.
# Both CI host and container must retain write access to synthetic evidence.
# This is trusted isolated CI, not an immutable or adversarial-user mount.
chmod 1777 "$evidence"
mkdir "$evidence/observations"
chmod 1777 "$evidence/observations"
candidate="$(git rev-parse HEAD)"
git rev-parse HEAD HEAD^{tree} > "$evidence/source.txt"

cleanup() {
    local result=$? cleanup_status=0 containers volumes networks inspection_status=0
    trap - EXIT
    set +e
    if [ "$parity_started" -eq 1 ]; then
        custody_after="$(sha256sum "$evidence/driver" "$evidence/receipt.json")" || cleanup_status=1
        printf '%s\n' "$custody_after" > "$evidence/custody-after.txt" || cleanup_status=1
        if [ "$custody_after" != "$custody_before" ]; then
            cleanup_status=1
            printf 'Driver or receipt changed during the parity invocation\n' >&2
        fi
        stat -c '%n uid=%u gid=%g mode=%a' "$evidence" "$evidence/driver" "$evidence/receipt.json" \
            > "$evidence/custody-ownership-after.txt" || cleanup_status=1
        runner_image_after="$(docker image inspect bifrost-test-api-dev:latest --format '{{.Id}}')" || cleanup_status=1
        printf '%s\n' "$runner_image_after" > "$evidence/api-image-after.txt" || cleanup_status=1
        if [ "$runner_image_after" != "$api_image_before" ]; then
            cleanup_status=1
            printf 'Test-runner image changed during the parity invocation\n' >&2
        fi
        cp "$LOG_DIR/test-results.xml" "$evidence/test-results.xml" || cleanup_status=1
    fi
    if docker container inspect "$builder" >/dev/null 2>&1; then
        docker rm -f "$builder" >> "$evidence/cleanup.log" 2>&1 || cleanup_status=1
    fi
    ./test.sh stack down >> "$evidence/cleanup.log" 2>&1 || cleanup_status=1
    containers="$(docker ps -aq --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")" || inspection_status=1
    volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")" || inspection_status=1
    networks="$(docker network ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")" || inspection_status=1
    if [ "$inspection_status" -ne 0 ] || [ -n "$containers$volumes$networks" ]; then
        cleanup_status=1
    fi
    printf '%s\n' "$result" > "$evidence/gate-exit-status.txt" || cleanup_status=1
    printf 'project=%s\ncleanup_status=%s\ninspection_status=%s\ncontainers=%s\nvolumes=%s\nnetworks=%s\n' \
        "$COMPOSE_PROJECT_NAME" "$cleanup_status" "$inspection_status" "$containers" "$volumes" "$networks" \
        > "$evidence/project-resources.txt" || cleanup_status=1
    if [ "$result" -eq 0 ] && [ "$cleanup_status" -ne 0 ]; then
        result=1
    fi
    if ! printf '%s\n' "$result" > "$evidence/exit-status.txt"; then
        if [ "$result" -eq 0 ]; then
            result=1
        fi
        printf 'Could not retain final gate status\n' >&2
    fi
    exit "$result"
}
trap cleanup EXIT

# Required literal gate on this clean candidate; anonymous pin checks unchanged.
./test.sh pre-pr 2>&1 | tee "$evidence/pre-pr.log"
test "$(git rev-parse HEAD)" = "$candidate"
test -z "$(git status --porcelain --untracked-files=all)"

# The unchanged checks target runs fmt, Clippy, and the whole locked workspace.
docker build --target checks -t "$checks_image" -f core-rs/Dockerfile core-rs \
    2>&1 | tee "$evidence/rust-checks.log"
docker image inspect "$checks_image" --format '{{.Id}}' > "$evidence/rust-image.txt"
docker create --name "$builder" \
    --label "com.docker.compose.project=$COMPOSE_PROJECT_NAME" \
    "$checks_image" sh -eu -c '
        cargo test --locked --offline -p bifrost-domain --example workflow_domain_vectors
        cargo build --locked --offline -p bifrost-domain --example workflow_domain_vectors
        install -m 755 target/debug/examples/workflow_domain_vectors /tmp/workflow-domain-driver
    ' > "$evidence/builder-container.txt"
docker start -a "$builder" 2>&1 | tee "$evidence/driver-build.log"
test "$(docker inspect --format '{{.State.ExitCode}}' "$builder")" = 0
docker cp "$builder:/tmp/workflow-domain-driver" "$evidence/driver"
chmod 755 "$evidence/driver"
docker rm "$builder" > "$evidence/builder-removal.txt"

python3 - "$evidence" <<'PY'
import hashlib
import json
import subprocess
import sys
from pathlib import Path

evidence = Path(sys.argv[1])
paths = [
    "core-rs/crates/bifrost-domain/src/lib.rs",
    "core-rs/crates/bifrost-domain/src/workflow/mod.rs",
    "core-rs/crates/bifrost-domain/src/workflow/tests.rs",
    "api/src/services/execution/attempts.py",
    "api/src/jobs/consumers/workflow_execution.py",
    "api/src/repositories/executions.py",
    "api/src/models/orm/executions.py",
    "api/tests/parity/workflow_domain_harness.py",
    "api/tests/parity/test_workflow_domain.py",
    "core-rs/crates/bifrost-domain/examples/workflow_domain_vectors.rs",
    "core-rs/crates/bifrost-domain/Cargo.toml",
    "core-rs/Cargo.lock",
    "core-rs/Dockerfile",
    "scripts/ci/workflow-domain-parity.sh",
    ".github/workflows/workflow-domain-parity.yml",
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


receipt = {
    "schema": "bifrost.test.workflow-domain-receipt/v1",
    "candidate_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "candidate_tree": subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], text=True).strip(),
    "driver_sha256": digest(evidence / "driver"),
    "fixture_sha256": digest(Path("api/tests/parity/fixtures/workflow-domain-v1.json")),
    "source_sha256": {path: digest(Path(path)) for path in paths},
}
(evidence / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
PY

# Bind these bytes outside runner-writable files for independent final readback.
custody_before="$(sha256sum "$evidence/driver" "$evidence/receipt.json")"
printf '%s\n' "$custody_before" > "$evidence/custody-before.txt"

# No schema or test-runner image alteration. Loader/ABI is exercised in that image.
./test.sh stack up 2>&1 | tee "$evidence/stack-up.log"
api_image_before="$(docker image inspect bifrost-test-api-dev:latest --format '{{.Id}}')"
printf '%s\n' "$api_image_before" > "$evidence/api-image-before.txt"
# test.sh can have generated a different suite's JUnit during pre-pr.
rm -f "$LOG_DIR/test-results.xml"
parity_started=1
./test.sh tests/parity/test_workflow_domain.py -v 2>&1 | tee "$evidence/parity.log"
test "$(git rev-parse HEAD)" = "$candidate"
test -z "$(git status --porcelain --untracked-files=all)"
