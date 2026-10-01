#!/usr/bin/env bash
# Exercise actual scoped dispatch without Docker or a database.
set -euo pipefail
source ./test.sh help >/dev/null
stages=()
run_pre_pr_stage() { stages+=("$*"); }
pre_pr_plan_lane() {
    case "$1" in
        api_quality|api_unit|api_e2e|mcp_conformance|client_e2e) echo comprehensive ;;
        client_quality|client_unit) echo affected ;;
        *) echo skip ;;
    esac
}
pre_pr_plan_targets() {
    case "$1" in
        impacted) echo src/leaf.ts ;;
        unit_tests) echo src/leaf.test.ts ;;
    esac
}
run_scoped_pre_pr
[[ "${stages[*]}" == 'client client_quality_checks src/leaf.ts quality quality_api generated generated_api_checks client-unit client_unit_targets src/leaf.test.ts' ]]
# An empty affected target list must fail, not fall through to all tests.
pre_pr_plan_targets() { :; }
if run_scoped_pre_pr; then
    echo 'FAIL: empty affected targets accepted' >&2
    exit 1
fi
# Docs-only plans must not build an image or boot the stack.
stages=()
pre_pr_plan_lane() { echo skip; }
run_scoped_pre_pr
[[ "${#stages[@]}" == 0 ]]
echo 'PASS: scoped lanes defer only broad tests, check generated API, and reject empty targets'

# Full verification must build before freezing images, then execute every lane
# against those exact images without leaking the setting to later invocations.
stages=()
unset BIFROST_SKIP_BUILD
run_pre_pr_stage() { stages+=("$1:${BIFROST_SKIP_BUILD:-0}"); }
run_full_pre_pr
[[ "${stages[*]}" == 'client:0 stack:0 quality:1 generated:1 unit:1 e2e:1 browser:1 image:1' ]]
[[ -z "${BIFROST_SKIP_BUILD+x}" ]]
echo 'PASS: full lanes build once and freeze backend images without omitting tests'

# A conformance invocation after browser verification must reconcile the API's
# authority before sending requests. Stop at reset_state to avoid Docker here.
set +e
authority=$(BIFROST_TEST_PUBLIC_URL=http://localhost:3000 bash -Eeuo pipefail -c '
    source ./test.sh help >/dev/null
    require_stack_up() { :; }
    reset_state() { echo "$BIFROST_TEST_PUBLIC_URL"; return 91; }
    mcp_conformance
' ./test.sh)
status=$?
set -e
[[ "$status" == 91 && "$authority" == http://api:8000 ]]
echo 'PASS: MCP conformance reconciles backend authority after browser lanes'
