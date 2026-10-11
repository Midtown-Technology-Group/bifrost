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
proxy_pid=''
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
  if [[ -n "$proxy_pid" ]]; then
    kill -TERM "$proxy_pid" 2>/dev/null || true
    wait "$proxy_pid" || exit 1
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
for case_number in 0 1 2 3 4 5 6 7; do
  case_dir="$slice_root/$case_number"
  wait_file "$case_dir/launch-ready.json"
  session=$(jq -er '.prepare.body.binding.session_id' "$case_dir/owner-config.json")
  database_target=$pool_ip
  database_suffix=''
  if [[ "$case_number" = 3 || "$case_number" = 4 || "$case_number" = 5 || "$case_number" = 7 ]]; then
    session=$(jq -er '.prepare.body.binding.session_id' "$case_dir/owner-config.json")
    env -i PATH=/usr/bin:/bin BIFROST_ISOLATED_COMMIT_ACK_TEST=1 \
      python3 "$slice_root/commit-ack-proxy.py" "$pool_ip" "$case_dir" "$session" \
      > "$case_dir/commit-ack-proxy.log" 2>&1 &
    proxy_pid=$!
    # Observe the original helper, not just a file: a failed launch must retain
    # its first-party diagnostics instead of becoming an owner ingress timeout.
    deadline=$((SECONDS + 45))
    while [[ ! -f "$case_dir/commit-ack-proxy-ready.json" ]]; do
      if ! kill -0 "$proxy_pid" 2>/dev/null; then
        cat "$case_dir/commit-ack-proxy.log" >&2
        wait "$proxy_pid"
        exit 1
      fi
      test "$SECONDS" -lt "$deadline"
      sleep 0.02
    done
    wait_file "$case_dir/commit-ack-proxy-ready.json"
    database_target="127.0.0.1:$(jq -er '.port' "$case_dir/commit-ack-proxy-ready.json")"
    database_suffix='?sslmode=disable'
  fi
  coproc ORIGINAL_OWNER { exec env -i PATH=/usr/bin:/bin \
    BIFROST_ISOLATED_OWNER_TEST=1 \
    "BIFROST_OWNER_TEST_DATABASE_URL=postgresql://wex_core:synthetic_primer_core@$database_target/bifrost_test$database_suffix" \
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
  if [[ "$case_number" = 5 ]]; then
    receive_event release_commit_required "$case_dir/release-commit-required.json"
    wait_file "$case_dir/release-ready.json"
    cat "$case_dir/release-ready.json" >&"$owner_input"
    printf '\n' >&"$owner_input"
    receive_event release_commit_unknown "$case_dir/release-commit-unknown.json"
    wait_file "$case_dir/abort-release-ready.json"
    cat "$case_dir/abort-release-ready.json" >&"$owner_input"
    printf '\n' >&"$owner_input"
  fi
  if [[ "$case_number" = 2 || "$case_number" = 6 || "$case_number" = 7 ]]; then
    receive_event sdk_admitted "$case_dir/owner-sdk-admitted.json"
    wait_file "$case_dir/cancel-ready.json"
    cat "$case_dir/cancel-ready.json" >&"$owner_input"
    printf '\n' >&"$owner_input"
  fi
  if [[ "$case_number" = 4 || "$case_number" = 6 ]]; then
    recovery_mode=--recover-closed-result
    recovery_event=closed_result_recovery_required
    if [[ "$case_number" = 6 ]]; then
      recovery_mode=--recover-closed-cancel
      recovery_event=closed_cancel_recovery_required
    fi
    receive_event "$recovery_event" "$case_dir/owner-crash-required.json"
    test "$(jq -er '.owner_pid' "$case_dir/owner-crash-required.json")" = "$owner_pid"
    # Real closed SQL state is insufficient while the original owner is alive.
    # This read-only attempt must deny before any recovery intent or OS effect.
    if env -i PATH=/usr/bin:/bin BIFROST_ISOLATED_OWNER_TEST=1 \
      "BIFROST_OWNER_TEST_DATABASE_URL=postgresql://wex_core:synthetic_primer_core@$database_target/bifrost_test$database_suffix" \
      "$owner_binary" "$recovery_mode" "$case_dir/owner-config.json" \
      > "$case_dir/live-owner-recovery-denied.log" 2>&1; then exit 1; fi
    test ! -e "$case_dir/execution/journal/closed-recovery-intent.json"
    printf '{"event":"live_owner_recovery_denied","session_id":"%s","recovery_intent_created":false}\n' "$session" > "$case_dir/live-owner-recovery-denied.json.new"
    mv "$case_dir/live-owner-recovery-denied.json.new" "$case_dir/live-owner-recovery-denied.json"
    original_owner_pid=$owner_pid
    kill -KILL "$owner_pid"
    if wait "$owner_pid"; then exit 1; else original_exit=$?; fi
    test "$original_exit" = 137
    printf '{"event":"original_owner_crashed","pid":%s,"exit_code":137}\n' "$original_owner_pid" > "$case_dir/original-owner-crashed.json.new"
    mv "$case_dir/original-owner-crashed.json.new" "$case_dir/original-owner-crashed.json"
    owner_pid=''
    exec {owner_input}>&-
    exec {owner_output}<&-
    wait_file "$case_dir/closed-recovery-ready.json"
    coproc CLOSED_RECOVERY { exec env -i PATH=/usr/bin:/bin \
      BIFROST_ISOLATED_OWNER_TEST=1 \
      "BIFROST_OWNER_TEST_DATABASE_URL=postgresql://wex_core:synthetic_primer_core@$database_target/bifrost_test$database_suffix" \
      "$owner_binary" "$recovery_mode" "$case_dir/owner-config.json"; }
    owner_pid=$CLOSED_RECOVERY_PID
    exec {owner_input}>&"${CLOSED_RECOVERY[1]}"
    exec {owner_output}<&"${CLOSED_RECOVERY[0]}"
    printf '{"event":"closed_recovery_started","pid":%s,"original_owner_pid":%s}\n' "$owner_pid" "$original_owner_pid" > "$case_dir/closed-recovery-started.json.new"
    mv "$case_dir/closed-recovery-started.json.new" "$case_dir/closed-recovery-started.json"
  fi
  receive_event resources_retired "$case_dir/owner-resources-retired.json"
  wait_file "$case_dir/settlement-ready.json"
  cat "$case_dir/settlement-ready.json" >&"$owner_input"
  printf '\n' >&"$owner_input"
  receive_event slice_result "$case_dir/owner-result.json"
  exec {owner_input}>&-
  exec {owner_output}<&-
  wait "$owner_pid"
  exit_event=owner_exited
  if [[ "$case_number" = 4 || "$case_number" = 6 ]]; then exit_event=closed_recovery_exited; fi
  printf '{"event":"%s","pid":%s,"exit_code":0}\n' "$exit_event" "$owner_pid" > "$case_dir/owner-exited.json.new"
  mv "$case_dir/owner-exited.json.new" "$case_dir/owner-exited.json"
  owner_pid=''
  if [[ -n "$proxy_pid" ]]; then
    kill -TERM "$proxy_pid"
    wait "$proxy_pid"
    proxy_pid=''
  fi
  wait_file "$case_dir/fixture-finished.json"
done
wait_file "$slice_root/live-proof.json"
