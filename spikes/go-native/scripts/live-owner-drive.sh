#!/usr/bin/env bash
# Trusted hosted first-party launcher only. The SDK fixture never receives the
# core credential; the tenant receives neither fixture nor owner credentials.
set -euo pipefail
test "$(id -u)" = 1000
slice_root=$1
owner_binary=$2
pool_ip=$3
owner_pid=''
owner_input=''
owner_output=''
case_dir=''
cleanup_owner() {
  original=$?
  trap - EXIT
  if [[ -n "$owner_pid" ]]; then
    kill "$owner_pid" 2>/dev/null || true
    wait "$owner_pid" 2>/dev/null || true
  fi
  if [[ -n "$case_dir" && -f "$case_dir/execution/journal/launch-intent.json" ]]; then
    # Only the original retained spec/nonce can be drained, never recreated.
    session=$(jq -er '.prepare.body.binding.session_id' "$case_dir/owner-config.json")
    remaining=$(docker container ls -aq --no-trunc --filter "name=^/bifrost-guardian-$session$")
    if [[ -n "$remaining" ]]; then
      env -i PATH=/usr/bin:/bin "$owner_binary" --drain "$case_dir/owner-config.json" \
        > "$case_dir/stop-only-recovery.json" || exit 1
    fi
  fi
  exit "$original"
}
trap cleanup_owner EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
wait_file() {
  local path=$1
  local deadline=$((SECONDS + 45))
  while [[ ! -f "$path" ]]; do
    test "$SECONDS" -lt "$deadline"
    sleep 0.02
  done
  test "$(stat -c %s "$path")" -le 65536
}
receive_event() {
  local event=$1
  local target=$2
  local message
  IFS= read -r -t 45 message <&"$owner_output"
  test "${#message}" -le 65536
  printf '%s\n' "$message" > "$target.new"
  test "$(jq -er '.event' "$target.new")" = "$event"
  mv "$target.new" "$target"
}
for case_number in 0 1; do
  case_dir="$slice_root/$case_number"
  wait_file "$case_dir/launch-ready.json"
  coproc ORIGINAL_OWNER { exec env -i PATH=/usr/bin:/bin \
    BIFROST_ISOLATED_OWNER_TEST=1 \
    "BIFROST_OWNER_TEST_DATABASE_URL=postgresql://wex_core:synthetic_primer_core@$pool_ip/bifrost_test" \
    "$owner_binary" "$case_dir/owner-config.json"; }
  owner_pid=$ORIGINAL_OWNER_PID
  exec {owner_input}>&"${ORIGINAL_OWNER[1]}"
  exec {owner_output}<&"${ORIGINAL_OWNER[0]}"
  receive_event ingress_required "$case_dir/owner-ingress.json"
  wait_file "$case_dir/ingress-ready.json"
  cat "$case_dir/ingress-ready.json" >&"$owner_input"
  printf '\n' >&"$owner_input"
  receive_event start_committed "$case_dir/owner-start.json"
  wait_file "$case_dir/provision-ready.json"
  cat "$case_dir/provision-ready.json" >&"$owner_input"
  printf '\n' >&"$owner_input"
  receive_event slice_result "$case_dir/owner-result.json"
  exec {owner_input}>&-
  exec {owner_output}<&-
  wait "$owner_pid"
  printf '{"event":"owner_exited","pid":%s,"exit_code":0}\n' "$owner_pid" > "$case_dir/owner-exited.json.new"
  mv "$case_dir/owner-exited.json.new" "$case_dir/owner-exited.json"
  owner_pid=''
  wait_file "$case_dir/fixture-finished.json"
done
wait_file "$slice_root/live-proof.json"
