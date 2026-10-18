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
run_pre_pr_stage() {
    stages+=("$1:${BIFROST_SKIP_BUILD:-0}")
    [[ "$1" != stack || "$2" == prepare_full_pre_pr_stack ]]
}
run_full_pre_pr
[[ "${stages[*]}" == 'client:0 stack:0 quality:1 generated:1 unit:1 e2e:1 browser:1 image:1' ]]
[[ -z "${BIFROST_SKIP_BUILD+x}" ]]
echo 'PASS: full lanes build once and freeze backend images without omitting tests'

# An already-running stack returns early, so building must precede stack_up.
preparation=()
docker() { preparation+=("$*"); }
stack_up() { preparation+=("stack:${BIFROST_SKIP_BUILD:-0}"); }
prepare_full_pre_pr_stack
[[ "${preparation[*]}" == "compose -f $COMPOSE_FILE build api stack:1" ]]
[[ -z "${BIFROST_SKIP_BUILD+x}" ]]
echo 'PASS: full stack preparation builds even when the stack is already running'

# A conformance invocation after browser verification must reconcile the API's
# authority before sending requests. Stop at reset_state to avoid Docker here.
set +e
authority=$(BIFROST_TEST_PUBLIC_URL=http://localhost:3000 bash -Eeuo pipefail -c '
    source ./test.sh help >/dev/null
    require_stack_up() { :; }
    prepare_mcp_conformance_image() { :; }
    reset_state() { echo "$BIFROST_TEST_PUBLIC_URL"; return 91; }
    mcp_conformance
' ./test.sh)
status=$?
set -e
[[ "$status" == 91 && "$authority" == http://api:8000 ]]
echo 'PASS: MCP conformance reconciles backend authority after browser lanes'

# Unchanged action inputs need structural SHA checks locally; changed inputs
# still resolve readable versions. An unknown comparison fails closed.
(
    scanner_state="$(mktemp -d)"
    trap 'rm -rf "$scanner_state"' EXIT
    TEST_LOCK_DIR="$scanner_state"
    bootstrap_log="$scanner_state/bootstrap.log"
    python3() {
        [[ "$1" == -m && "$2" == venv && "$#" == 3 ]]
        mkdir -p "$3/bin"
        printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$*" >> %q\n' \
            "$bootstrap_log" > "$3/bin/python"
        chmod +x "$3/bin/python"
    }

    scanner_python="$(action_pin_scanner_python)"
    [[ "$scanner_python" == "$TEST_LOCK_DIR"/action-pin-scanner-*/bin/python ]]
    [[ -f "${scanner_python%/bin/python}/.ready" ]]
    grep -F -- \
        '-m pip install --quiet --disable-pip-version-check --no-deps --only-binary :all: --require-hashes -r .github/requirements/action-pin-scanner.lock' \
        "$bootstrap_log"
    grep -F -- '-c import yaml; assert yaml.__version__ == "6.0.3"' "$bootstrap_log"
)
echo 'PASS: Action pin scanner bootstraps a wheel-only hash-locked clean-host environment'

for failed_step in venv install validate marker; do
    (
        scanner_state="$(mktemp -d)"
        trap 'rm -rf "$scanner_state"' EXIT
        TEST_LOCK_DIR="$scanner_state"
        export ACTION_PIN_TEST_FAILURE="$failed_step"
        python3() {
            if [[ "$ACTION_PIN_TEST_FAILURE" == venv ]]; then
                return 41
            fi
            mkdir -p "$3/bin"
            cat > "$3/bin/python" <<'EOF'
#!/usr/bin/env bash
if [[ "$ACTION_PIN_TEST_FAILURE" == install && "$1" == -m ]]; then
    exit 42
fi
if [[ "$ACTION_PIN_TEST_FAILURE" == validate && "$1" == -c ]]; then
    exit 43
fi
EOF
            chmod +x "$3/bin/python"
        }
        touch() {
            if [[ "$ACTION_PIN_TEST_FAILURE" == marker ]]; then
                return 44
            fi
            command touch "$@"
        }

        if action_pin_scanner_python >/dev/null; then
            exit 1
        fi
        [[ ! -e "$TEST_LOCK_DIR"/action-pin-scanner-*/.ready ]]
    )
done
echo 'PASS: Action pin scanner bootstrap failures never create a ready marker'

(
    pin_calls=()
    pin_diff_status=0
    pin_python_status=0
    action_pin_scanner_python() { printf '%s\n' python3; }
    git() {
        [[ "$*" == 'diff --quiet origin/main HEAD -- .github/workflows .github/actions api/scripts/check_github_action_pins.py' ]] || exit 99
        return "$pin_diff_status"
    }
    python3() { pin_calls+=("$*"); return "$pin_python_status"; }
    candidate_action_pin_checks
    [[ "${pin_calls[*]}" == api/scripts/check_github_action_pins.py ]]
    pin_calls=()
    pin_diff_status=1
    candidate_action_pin_checks
    [[ "${pin_calls[*]}" == 'api/scripts/check_github_action_pins.py --verify-versions' ]]
    pin_calls=()
    pin_diff_status=128
    if candidate_action_pin_checks; then exit 1; fi
    [[ "${#pin_calls[@]}" == 0 ]]
    pin_diff_status=0
    pin_python_status=7
    if candidate_action_pin_checks; then exit 1; fi
)
echo 'PASS: Action pin checks retain full SHAs, verify changed versions and fail closed'


# Fresh runners have no conformance image. Prepare it before the stage snapshot;
# preparation failure must prevent starting the stage, not produce stale proof.
(
    stages=()
    pre_pr_plan_lane() {
        case "$1" in mcp_conformance) echo affected ;; *) echo skip ;; esac
    }
    run_pre_pr_stage() { stages+=("stage:$*"); }
    require_stack_up() { :; }
    docker() { stages+=("build:$*"); }
    run_scoped_pre_pr
    [[ "${stages[*]}" == "stage:stack stack_up build:compose -f $COMPOSE_FILE --profile test build mcp-conformance stage:mcp run_mcp_conformance" ]]
)
echo 'PASS: MCP image preparation precedes the immutable conformance stage'

set +e
failure=$(bash -Eeuo pipefail -c '
    source ./test.sh help >/dev/null
    pre_pr_plan_lane() {
        case "$1" in mcp_conformance) echo affected ;; *) echo skip ;; esac
    }
    run_pre_pr_stage() { [[ "$1" != mcp ]] || echo unexpected-mcp-stage; }
    prepare_mcp_conformance_image() { return 73; }
    run_scoped_pre_pr
' ./test.sh)
status=$?
set -e
[[ "$status" == 73 && "$failure" != *unexpected-mcp-stage* ]]
echo 'PASS: failed MCP preparation blocks conformance evidence'

# Runner options must survive the public wrapper without splitting or globbing.
(
    prepared=false
    prepare_mcp_conformance_image() { prepared=true; }
    run_mcp_conformance() {
        [[ "$prepared" == true ]]
        [[ "$#" == 3 && "$1" == --filter && "$2" == "tools list" && "$3" == "*.json" ]]
    }
    mcp_conformance --filter "tools list" "*.json"
)
echo 'PASS: MCP wrapper preserves caller argument boundaries and preparation order'
