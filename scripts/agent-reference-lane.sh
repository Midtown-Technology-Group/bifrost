#!/usr/bin/env bash
# Explicit, disposable C1-R lane. Never source optional host/sibling credentials.
set -euo pipefail
lane_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$lane_root"
lane_renderer="$lane_root/scripts/render-agent-reference-compose.py"

if { [ "${1:-}" = "--release-inner" ] && [ "$#" = 2 ]; } || \
    { [ "${1:-}" = "--inspect-inner" ] && [ "$#" = 3 ]; }; then
    : "${BIFROST_AGENT_REFERENCE_INNER:?private lane context required}"
    python3 "$lane_renderer" check-inner --context "$BIFROST_AGENT_REFERENCE_INNER" \
        --root "$lane_root" -- stack up
    lane_context="$BIFROST_AGENT_REFERENCE_INNER"
    lane_checked_source="$(git rev-parse HEAD)"
    lane_runner_id="$2"
    lane_verify_args=()
    lane_phase=before_pytest
    if [ "$1" = "--inspect-inner" ]; then
        lane_phase=after_pytest
        lane_verify_args=(--receipt "$lane_context/custody/release.json" --exit-code "$3")
    elif [ -e "$lane_context/custody/release.json" ]; then
        echo "ERROR: runner already released." >&2
        exit 1
    fi
    lane_containers="$(docker ps -aq --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")"
    lane_networks="$(docker network ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")"
    lane_volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")"
    [ -n "$lane_containers" ] && [ -n "$lane_networks" ] && [ -n "$lane_volumes" ]
    read -ra lane_container_ids <<< "${lane_containers//$'\n'/ }"
    read -ra lane_network_ids <<< "${lane_networks//$'\n'/ }"
    read -ra lane_volume_ids <<< "${lane_volumes//$'\n'/ }"
    # Raw Docker metadata stays only in the private mode0700 context.
    docker inspect "${lane_container_ids[@]}" > "$lane_context/containers.json"
    docker network inspect "${lane_network_ids[@]}" > "$lane_context/networks.json"
    docker volume inspect "${lane_volume_ids[@]}" > "$lane_context/volumes.json"
    python3 "$lane_renderer" verify --config "$COMPOSE_FILE" \
        --containers "$lane_context/containers.json" --networks "$lane_context/networks.json" --volumes "$lane_context/volumes.json" \
        --images "$lane_context/images.json" --binding "$lane_context/custody/binding.json" \
        --source "$lane_checked_source" \
        --runner-id "$lane_runner_id" "${lane_verify_args[@]}" \
        --project "$COMPOSE_PROJECT_NAME" --output "$lane_context/custody.json"
    python3 - "$lane_context" "$lane_context/custody-records.json" "$lane_phase" <<'PY'
import json
import sys
from pathlib import Path
context, destination = map(Path, sys.argv[1:3])
observations = json.loads(destination.read_text()) if destination.exists() else []
record = json.loads((context / "custody.json").read_text())
record["source"] = json.loads((context / "owner.json").read_text())["source"]
record["phase"] = sys.argv[3]
binding = json.loads((context / "custody/binding.json").read_text())
record["invocation"] = "capacity-reference" if "tests/e2e/platform/agent_reference_cases.py" in binding["argv"] else "fixture-units"
if record["phase"] == "after_pytest":
    containers = json.loads((context / "containers.json").read_text())
    record["runner_exit_code"] = next(c["State"]["ExitCode"] for c in containers
        if c["Config"]["Labels"]["com.docker.compose.service"] == "test-runner")
observations.append(record)
destination.write_text(json.dumps(observations, indent=2, sort_keys=True) + "\n")
PY
    if [ "$lane_phase" = before_pytest ]; then
        python3 "$lane_renderer" release-run --context "$lane_context" --runner-id "$lane_runner_id"
    fi
    exit 0
fi

if [ "$#" = 0 ]; then
    for lane_override in COMPOSE_FILE COMPOSE_PROJECT_NAME BIFROST_PROJECT_PREFIX BIFROST_SKIP_BUILD BIFROST_AGENT_REFERENCE_INNER; do
        if [ -n "${!lane_override:-}" ]; then
            echo "ERROR: agent-reference refuses ambient lane overrides." >&2
            exit 2
        fi
    done
    exec env -i PATH="$PATH" HOME="$HOME" BIFROST_AGENT_REFERENCE_CLEAN=1 \
        "$lane_root/scripts/agent-reference-lane.sh" --run-owned
fi
if [ "$#" != 1 ] || [ "$1" != "--run-owned" ] || [ "${BIFROST_AGENT_REFERENCE_CLEAN:-}" != 1 ]; then
    echo "Usage: ./test.sh agent-reference (no arguments or overrides)" >&2
    exit 2
fi
# The private entry is not an alternate route for ambient Docker/secret settings.
for lane_env in $(compgen -e); do
    case "$lane_env" in
        PATH|HOME|PWD|OLDPWD|SHLVL|_|BIFROST_AGENT_REFERENCE_CLEAN) ;;
        *) echo "ERROR: private lane environment was not sanitized." >&2; exit 2 ;;
    esac
done

# shellcheck source=scripts/lib/test_helpers.sh
source "$lane_root/scripts/lib/test_helpers.sh"
# Frozen after env -i: ordinary stack commands own a different project namespace.
export BIFROST_PROJECT_PREFIX=bifrost-agent-reference
export COMPOSE_PROJECT_NAME
COMPOSE_PROJECT_NAME="$(compute_project_name .)"
lane_source="$(git rev-parse HEAD)"
if [ -n "$(git status --porcelain)" ]; then
    echo "ERROR: agent-reference requires a clean committed source candidate." >&2
    exit 1
fi
for lane_input in api/tests/e2e/platform/agent_reference_cases.py \
    api/tests/unit/test_agent_reference_fixture.py api/tests/unit/test_agent_reference_lane.py \
    api/scripts/agent_reference_runner.py \
    test-fixtures/agent-reference/provenance.json; do
    if [ ! -f "$lane_input" ] || [ -L "$lane_input" ]; then
        echo "ERROR: required agent-reference input missing or symlinked: $lane_input" >&2
        exit 1
    fi
done

lane_lock_dir="$(git rev-parse --path-format=absolute --git-path bifrost-test-locks)"
mkdir -p "$lane_lock_dir"
exec {lane_lock_fd}>"$lane_lock_dir/agent-reference.lock"
flock -n "$lane_lock_fd" || { echo "ERROR: agent-reference already owns this worktree." >&2; exit 1; }

lane_require_empty() {
    local lane_container_result lane_network_result lane_volume_result
    lane_container_result="$(docker ps -aq --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")" || return 1
    lane_network_result="$(docker network ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")" || return 1
    lane_volume_result="$(docker volume ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")" || return 1
    if [ -n "$lane_container_result$lane_network_result$lane_volume_result" ]; then
        echo "ERROR: exact project has pre-existing/remaining resources; no reset authorized." >&2
        return 1
    fi
}
lane_require_empty
lane_context="$(mktemp -d "$lane_lock_dir/agent-reference.XXXXXXXX")"
chmod 700 "$lane_context"
mkdir "$lane_context/custody"
chmod 755 "$lane_context/custody"
export BIFROST_AGENT_REFERENCE_INNER="$lane_context"
export COMPOSE_FILE="$lane_context/compose.json" BIFROST_SKIP_BUILD=1
export LOG_DIR="/tmp/bifrost-$COMPOSE_PROJECT_NAME"
lane_evidence_dir="/tmp/bifrost-agent-reference-evidence-${COMPOSE_PROJECT_NAME}"
if [ -e "$LOG_DIR" ] || [ -L "$LOG_DIR" ] || [ -e "$lane_evidence_dir" ] || [ -L "$lane_evidence_dir" ]; then
    echo "ERROR: pre-existing result/fixture directory requires its owner's disposition." >&2
    rm -rf "$lane_context"
    exit 1
fi
mkdir -p "$LOG_DIR/solution-repo-fixtures"
mkdir -m 700 "$lane_evidence_dir"
python3 - "$lane_context" "$lane_root" "$COMPOSE_PROJECT_NAME" "$lane_source" <<'PY'
import json
import sys
from pathlib import Path
context, root, project, source = sys.argv[1:]
Path(context, "owner.json").write_text(json.dumps({"root": root, "project": project, "source": source}) + "\n")
PY

lane_started=0
lane_cleanup() {
    local lane_status=$? lane_cleanup_status=0
    trap - EXIT INT TERM
    set +e
    if [ "$lane_started" = 1 ]; then
        # Case code owns domain settlement. On interruption no terminal rows are
        # fabricated: stop real consumers/API, then dispose the entire owned stack.
        docker compose -f "$COMPOSE_FILE" stop worker scheduler api api-replica >/dev/null || lane_cleanup_status=1
        "$lane_root/test.sh" stack down || lane_cleanup_status=1
        lane_require_empty || lane_cleanup_status=1
    fi
    if [ "$lane_started" = 1 ] && [ "$lane_cleanup_status" = 0 ]; then
        python3 - "$lane_context/cleanup-evidence.json" "$COMPOSE_PROJECT_NAME" "$lane_source" <<'PY'
import json
import sys
from pathlib import Path
path, project, source = sys.argv[1:]
Path(path).write_text(json.dumps({"project": project, "source": source, "verified_empty_resources": True,
    "domain_settlement": "case-owned; not established by infrastructure teardown"}) + "\n")
PY
        if [ "$?" != 0 ]; then lane_cleanup_status=1; fi
        # The finite evidence directory has never been mounted in a container.
        python3 "$lane_renderer" publish --context "$lane_context" --destination "$lane_evidence_dir" \
            --source "$lane_source" --project "$COMPOSE_PROJECT_NAME" || lane_cleanup_status=1
    fi
    if [ "$lane_cleanup_status" = 0 ]; then
        rm -rf "$lane_context" || lane_cleanup_status=1
    fi
    if [ "$lane_cleanup_status" != 0 ]; then
        echo "ERROR: cleanup unverified; private context retained at $lane_context" >&2
    fi
    if [ "$lane_status" = 0 ] && [ "$lane_cleanup_status" != 0 ]; then
        lane_status=1
    fi
    exit "$lane_status"
}
trap lane_cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

docker compose --env-file /dev/null -f "$lane_root/docker-compose.test.yml" \
    --profile e2e --profile test config --format json > "$lane_context/base.json"
python3 "$lane_renderer" render --root "$lane_root" --project "$COMPOSE_PROJECT_NAME" \
    --log "$LOG_DIR" --source "$lane_source" --context "$lane_context" \
    --input "$lane_context/base.json" --output "$COMPOSE_FILE"
# Build/pull may need network; complete them before creating the internal network.
docker compose -f "$COMPOSE_FILE" build api
docker compose -f "$COMPOSE_FILE" pull postgres pgbouncer rabbitmq redis seaweedfs
lane_image_tags="$(python3 - "$COMPOSE_FILE" <<'PY'
import json
import sys
from pathlib import Path
print("\n".join(sorted({s["image"] for s in json.loads(Path(sys.argv[1]).read_text())["services"].values()})))
PY
)"
mapfile -t lane_images <<< "$lane_image_tags"
docker image inspect "${lane_images[@]}" > "$lane_context/prebuild-images.json"
python3 "$lane_renderer" pin-images --config "$COMPOSE_FILE" \
    --input "$lane_context/prebuild-images.json" --output "$lane_context/images.json"
chmod 444 "$lane_context/images.json"
python3 - "$lane_context" "$lane_context/source-evidence.json" <<'PY'
import json
import sys
from pathlib import Path
context, destination = map(Path, sys.argv[1:])
owner = json.loads((context / "owner.json").read_text())
pins = json.loads((context / "images.json").read_text())
destination.write_text(json.dumps({"source": owner["source"], "project": owner["project"],
    "prebuild_image_ids": {service: pin["id"] for service, pin in pins.items()}}, sort_keys=True) + "\n")
PY
lane_require_empty
printf 'Agent reference source: %s\nProject: %s\n' "$lane_source" "$COMPOSE_PROJECT_NAME"
lane_started=1
"$lane_root/test.sh" stack up
"$lane_root/test.sh" tests/unit/test_agent_reference_fixture.py tests/unit/test_agent_reference_lane.py -v
"$lane_root/test.sh" tests/e2e/platform/agent_reference_cases.py -v
