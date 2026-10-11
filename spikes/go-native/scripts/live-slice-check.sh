#!/usr/bin/env bash
# Dedicated hosted disposable stack. Accepted producer inputs precede execution;
# there is no compilation, dependency installation or production dispatch here.
set -euo pipefail
test "$(id -u)" != 0
native_dir=$(cd "$1" && pwd)
owner_dir=$(cd "$2" && pwd)
evidence_dir=$(cd "$3" && pwd)
export COMPOSE_FILE=docker-compose.runtime-live-slice.yml
source scripts/lib/test_helpers.sh
slice_project=$(compute_project_name .)
export LOG_DIR="/tmp/bifrost-$slice_project"
export BIFROST_LIVE_SOURCE_SHA=$GITHUB_SHA
export BIFROST_LIVE_PRODUCER_RUN=$GITHUB_RUN_ID
slice_root="$LOG_DIR/live-slice"
test ! -e "$slice_root"
for kind in container volume network; do
  case "$kind" in
    container) found=$(docker ps -aq --filter "label=com.docker.compose.project=$slice_project") ;;
    volume) found=$(docker volume ls -q --filter "label=com.docker.compose.project=$slice_project") ;;
    network) found=$(docker network ls -q --filter "label=com.docker.compose.project=$slice_project") ;;
  esac
  test -z "$found"
done
mkdir -p "$LOG_DIR"
sudo install -d -m 700 -o 1000 -g 1000 "$slice_root" "$slice_root/native"
for entry in descriptor.json native-bundle-descriptor.json native-bundle.tar workflow; do
  sudo install -m 400 -o 1000 -g 1000 "$native_dir/$entry" "$slice_root/native/$entry"
done
sudo install -m 500 -o 1000 -g 1000 "$owner_dir/runtime-live-owner" "$slice_root/runtime-live-owner"
carrier_tag="bifrost-go-native-carrier:${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
if docker image inspect "$carrier_tag" >/dev/null 2>&1; then exit 1; fi
carrier_loaded=0
fixture_pid=''
driver_pid=''
cleanup_slice() {
  original=$?
  trap - EXIT
  cleanup=0
  if [[ -n "$driver_pid" ]]; then
    # The owned drive process's EXIT trap performs original-spec stop-only drain.
    kill -TERM "$driver_pid" 2>/dev/null || true
    wait "$driver_pid" 2>/dev/null || true
  fi
  if [[ -n "$fixture_pid" ]]; then
    kill -TERM "$fixture_pid" 2>/dev/null || true
    wait "$fixture_pid" 2>/dev/null || true
  fi
  if [[ ! -f "$evidence_dir/live-results.xml" && -f "$LOG_DIR/test-results.xml" ]]; then
    cp "$LOG_DIR/test-results.xml" "$evidence_dir/live-results.xml"
  fi
  # Explicit public evidence allowlist. Never retain issuer material/TLS keys.
  for case_number in 0 1; do
    mkdir -p "$evidence_dir/$case_number"
    for entry in owner-ingress.json owner-start.json owner-result.json owner-exited.json stop-only-recovery.json; do
      if sudo test -f "$slice_root/$case_number/$entry"; then
        sudo cp "$slice_root/$case_number/$entry" "$evidence_dir/$case_number/$entry" || cleanup=1
      fi
    done
    if sudo test -f "$slice_root/$case_number/owner-config.json"; then
      sudo jq 'del(.claim_token)' "$slice_root/$case_number/owner-config.json" > "$evidence_dir/$case_number/owner-metadata.json" || cleanup=1
      if session=$(sudo jq -er '.prepare.body.binding.session_id' "$slice_root/$case_number/owner-config.json"); then
        remaining=$(docker container ls -aq --no-trunc --filter "name=^/bifrost-guardian-$session$") || cleanup=1
        printf '%s=%s\n' "$session" "$remaining" >> "$evidence_dir/guardian-inventory.txt"
        test -z "$remaining" || cleanup=1
      else
        cleanup=1
      fi
    fi
  done
  if sudo test -f "$slice_root/live-proof.json"; then sudo cp "$slice_root/live-proof.json" "$evidence_dir/live-proof.json"; fi
  sudo chown -R "$(id -u):$(id -g)" "$evidence_dir" || cleanup=1
  bash test.sh stack down > "$evidence_dir/stack-cleanup.log" 2>&1 || cleanup=1
  for kind in container volume network; do
    case "$kind" in
      container) found=$(docker ps -aq --filter "label=com.docker.compose.project=$slice_project") ;;
      volume) found=$(docker volume ls -q --filter "label=com.docker.compose.project=$slice_project") ;;
      network) found=$(docker network ls -q --filter "label=com.docker.compose.project=$slice_project") ;;
    esac
    printf '%s=%s\n' "$kind" "$found" >> "$evidence_dir/stack-cleanup.log"
    test -z "$found" || cleanup=1
  done
  if [[ "$carrier_loaded" = 1 ]]; then docker image rm "$carrier_tag" > "$evidence_dir/image-cleanup.txt" 2>&1 || cleanup=1; fi
  # On residual custody preserve private staging for the hosted runner's failure
  # disposition. Never claim cleanup, delete mounted source or launch again.
  if [[ "$cleanup" = 0 ]]; then sudo rm -rf -- "$slice_root"; else exit 1; fi
  exit "$original"
}
trap cleanup_slice EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
python3 - "$native_dir" <<'PY'
import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]); d=json.loads((root/'descriptor.json').read_bytes())
assert hashlib.sha256((root/'carrier-image.tar').read_bytes()).hexdigest()==d['guardian_carrier']['image_archive_sha256']
assert (root/'carrier-image-id.txt').read_text().strip()==d['guardian_carrier']['image_id']
PY
docker image load --input "$native_dir/carrier-image.tar" > "$evidence_dir/image-load.txt"
carrier_loaded=1
test "$(docker image inspect --format '{{.Id}}' "$carrier_tag")" = "$(cat "$native_dir/carrier-image-id.txt")"
git rev-parse HEAD > "$evidence_dir/source.txt"
sha256sum "$owner_dir/runtime-live-owner" "$native_dir/native-bundle.tar" > "$evidence_dir/accepted-inputs.sha256"
bash test.sh stack up > "$evidence_dir/stack.log" 2>&1
docker compose -p "$slice_project" -f "$COMPOSE_FILE" up -d --wait --wait-timeout 60 writer-guard-pool
pool_container=$(docker compose -p "$slice_project" -f "$COMPOSE_FILE" ps -q writer-guard-pool)
pool_ip=$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$pool_container")
test -n "$pool_ip"
docker_gid=$(stat -c %g /var/run/docker.sock)
# Build the trusted fixture before starting bounded runtime observation. The
# ordinary test command still performs its existing cached build/checks.
docker compose -p "$slice_project" -f "$COMPOSE_FILE" build test-runner > "$evidence_dir/fixture-build.log" 2>&1
bash test.sh tests/e2e/platform/test_runtime_live_go_slice.py --durations=0 > "$evidence_dir/live-tests.log" 2>&1 &
fixture_pid=$!
# The ordinary test command resets the disposable database/services before
# pytest. Observe that same live fixture process until it publishes its exact
# first launch request; only then start the driver's runtime observation clock.
# The hosted job bounds bootstrap; no transaction, owner or spawn is retried.
while ! sudo test -f "$slice_root/0/launch-ready.json"; do
  if ! kill -0 "$fixture_pid" 2>/dev/null; then
    wait "$fixture_pid"
    exit 1
  fi
  sleep 0.05
done
sudo env -i PATH=/usr/bin:/bin /usr/bin/setpriv --reuid 1000 --regid 1000 --groups "$docker_gid" \
  bash spikes/go-native/scripts/live-owner-drive.sh "$slice_root" "$slice_root/runtime-live-owner" "$pool_ip" \
  > "$evidence_dir/owner-drive.log" 2>&1 &
driver_pid=$!
wait "$driver_pid"
driver_pid=''
wait "$fixture_pid"
fixture_pid=''
cp "$LOG_DIR/test-results.xml" "$evidence_dir/live-results.xml"
# Required broad unit and API quality checks in the supported disposable lane.
bash test.sh unit --durations=0 > "$evidence_dir/unit.log" 2>&1
cp "$LOG_DIR/test-results.xml" "$evidence_dir/unit-results.xml"
bash test.sh quality api > "$evidence_dir/quality.log" 2>&1
