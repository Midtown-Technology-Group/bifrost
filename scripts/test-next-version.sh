#!/usr/bin/env bash
# Test harness for scripts/next-version.py
#
# Pure and offline: feeds JSON PR arrays straight to the decider.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NEXT="$SCRIPT_DIR/next-version.py"

pass=0
fail=0

# run_test <name> <json> <expected_stdout> <expected_exit> [extra-args]
run_test() {
  local name="$1" json="$2" want_stdout="$3" want_exit="$4" args="${5:-}"
  local got_stdout got_exit
  local -a argv=()
  if [[ -n "$args" ]]; then
    read -r -a argv <<< "$args"
  fi
  set +e
  got_stdout="$(python3 "$NEXT" --json "$json" "${argv[@]}" 2>/dev/null)"
  got_exit=$?
  set -e

  if [[ "$got_stdout" == "$want_stdout" && "$got_exit" == "$want_exit" ]]; then
    echo "PASS: $name"
    pass=$((pass + 1))
  else
    echo "FAIL: $name"
    echo "  want stdout='$want_stdout' exit=$want_exit"
    echo "  got  stdout='$got_stdout' exit=$got_exit"
    fail=$((fail + 1))
  fi
}

run_test "conventional fix is a patch" \
  '[{"title":"fix(chat): preserve runtime","labels":[]}]' \
  '2.1.1' 0 '--base 2.1.0'

run_test "conventional feat is a minor" \
  '[{"title":"feat(api): add export","labels":[]}]' \
  '2.2.0' 0 '--base 2.1.0'

run_test "bang marks a breaking change" \
  '[{"title":"feat!: drop legacy auth","labels":[]}]' \
  '3.0.0' 0 '--base 2.1.0'

run_test "BREAKING marker is a major" \
  '[{"title":"fix: BREAKING remove endpoint","labels":[]}]' \
  '3.0.0' 0 '--base 2.1.0'

run_test "semver:major label wins over a fix title" \
  '[{"title":"fix: small","labels":["semver:major"]}]' \
  '3.0.0' 0 '--base 2.1.0'

run_test "semver:minor label lifts a chore" \
  '[{"title":"chore(deps): bump","labels":["semver:minor"]}]' \
  '2.2.0' 0 '--base 2.1.0'

run_test "semver:patch label on a feat stays a patch" \
  '[{"title":"feat: something","labels":["semver:patch"]}]' \
  '2.1.1' 0 '--base 2.1.0'

run_test "highest bump across PRs wins" \
  '[{"title":"fix: a","labels":[]},{"title":"feat: b","labels":[]}]' \
  '2.2.0' 0 '--base 2.1.0'

run_test "undecidable title falls back to patch" \
  '[{"title":"Update README","labels":[]}]' \
  '2.1.1' 0 '--base 2.1.0'

run_test "default is configurable" \
  '[{"title":"Update README","labels":[]}]' \
  '2.2.0' 0 '--base 2.1.0 --default minor'

run_test "semver:patch beats a breaking title (label-first)" \
  '[{"title":"feat!: remove API","labels":["semver:patch"]}]' \
  '2.1.1' 0 '--base 2.1.0'

run_test "semver:minor beats a breaking title (label-first)" \
  '[{"title":"feat!: remove API","labels":["semver:minor"]}]' \
  '2.2.0' 0 '--base 2.1.0'

run_test "no PRs means nothing to release" \
  '[]' \
  '' 3 '--base 2.1.0'

run_test "--since drops PRs at or before the tag boundary" \
  '[{"title":"fix: a","labels":[],"mergedAt":"2026-09-03T00:00:00Z"},{"title":"feat: b","labels":[],"mergedAt":"2026-09-01T00:00:00Z"}]' \
  '2.1.1' 0 '--base 2.1.0 --since 2026-09-02T00:00:00Z'

run_test "malformed base is rejected" \
  '[{"title":"fix: a","labels":[]}]' \
  '' 2 '--base two'

echo
echo "passed: $pass  failed: $fail"
[[ "$fail" == "0" ]]
