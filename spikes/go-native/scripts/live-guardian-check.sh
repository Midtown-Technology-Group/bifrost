#!/usr/bin/env bash
# Hosted CI only: first-party dormant guardian, no tenant Start or SDK material.
set -euo pipefail
test "$(id -u)" != 0
spike_root=$(cd "$(dirname "$0")/.." && pwd)
evidence_dir=$(cd "$1" && pwd)
owner_dir=$(cd "$2" && pwd)
guardian_dir="$evidence_dir/live-guardian"
mkdir -m 700 "$guardian_dir"
carrier_tag="bifrost-go-native-carrier:${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
carrier_id=$(cat "$evidence_dir/carrier-image-id.txt")
case "$carrier_id" in sha256:*) ;; *) exit 1 ;; esac
test "${#carrier_id}" = 71
# A prior resource is never adopted or removed by this task.
if docker image inspect "$carrier_tag" >/dev/null 2>&1; then exit 1; fi
guardian_loaded=0
cleanup_guardian() {
  guardian_original=$?
  trap - EXIT
  guardian_cleanup=0
  # The Rust probe owns stop-only recovery using its exact private intents.
  # This inventory never kills arbitrary label matches or reassigns custody.
  for guardian_intent in "$guardian_dir"/*/journal/launch-intent.json; do
    if [[ ! -f "$guardian_intent" ]]; then continue; fi
    guardian_session=$(basename "$(dirname "$(dirname "$guardian_intent")")")
    guardian_remaining=$(docker container ls -aq --no-trunc \
      --filter "label=bifrost.isolated.guardian.session=$guardian_session") || guardian_cleanup=1
    printf '%s=%s\n' "$guardian_session" "$guardian_remaining" >> "$guardian_dir/cleanup-inventory.txt"
    if [[ -n "$guardian_remaining" ]]; then guardian_cleanup=1; fi
  done
  if [[ "$guardian_loaded" = 1 ]]; then
    docker image rm "$carrier_tag" > "$guardian_dir/image-cleanup.txt" 2>&1 || guardian_cleanup=1
  fi
  if [[ "$guardian_cleanup" != 0 ]]; then exit 1; fi
  exit "$guardian_original"
}
trap cleanup_guardian EXIT
# Producer-generated export, pinned before materialization; no runtime pull.
python3 - "$evidence_dir" <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
descriptor = json.loads((root / "descriptor.json").read_text())
carrier = descriptor["guardian_carrier"]
assert hashlib.sha256((root / "carrier-image.tar").read_bytes()).hexdigest() == carrier["image_archive_sha256"]
assert (root / "carrier-image-id.txt").read_text().strip() == carrier["image_id"]
PY
docker image load --input "$evidence_dir/carrier-image.tar" > "$guardian_dir/image-load.txt"
guardian_loaded=1
test "$(docker image inspect --format '{{.Id}}' "$carrier_tag")" = "$carrier_id"
sha256sum "$owner_dir/runtime-live-guardian-probe" > "$guardian_dir/guardian-binary.sha256"
"$owner_dir/runtime-live-guardian-probe" "$evidence_dir/native-bundle.tar" \
  "$evidence_dir/native-bundle-descriptor.json" \
  "$spike_root/executionprofile/testdata/structural-vectors.json" \
  "$carrier_id" "$guardian_dir" > "$guardian_dir/proof.json"
