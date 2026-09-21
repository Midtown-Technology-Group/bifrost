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
