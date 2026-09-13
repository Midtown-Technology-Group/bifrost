#!/usr/bin/env bash
# Prove conflicting runner/lifecycle commands fail before touching Docker.
set -euo pipefail
repo_dir="$(git rev-parse --show-toplevel)"
source "$repo_dir/scripts/lib/test_helpers.sh"
lock_dir="$(git rev-parse --path-format=absolute --git-path bifrost-test-locks)"
lock_path="$lock_dir/test-stack.lock"
mkdir -p "$(dirname "$lock_path")"
exec {held_lock}>"$lock_path"
owns_lock=0
if flock -n "$held_lock"; then owns_lock=1; fi
for command in 'client e2e' 'stack reset' 'unit' 'pre-pr'; do
    read -r -a arguments <<< "$command"
    if output=$(bash "$repo_dir/test.sh" "${arguments[@]}" 2>&1); then
        echo "FAIL: $command acquired an already owned stack" >&2
        exit 1
    fi
    [[ "$output" == *"another test command owns this worktree's test stack"* ]] || {
        echo "FAIL: $command did not reject the held lock" >&2
        exit 1
    }
done
bash "$repo_dir/test.sh" --help >/dev/null
exec {held_lock}>&-
# The owning shell releasing its descriptor must allow the next invocation.
if [ "$owns_lock" = 1 ]; then flock -n "$lock_path" true; fi
echo 'PASS: shared stack lock rejects browser, backend, lifecycle and pre-PR overlap'

# Container-produced files in the shared result directory must not prevent a
# subsequent host command from opening its lock (the CI cleanup regression).
(
    scratch_repo="$(mktemp -d)"
    scratch_results=""
    cleanup() {
        rm -rf -- "$scratch_repo"
        if [ -n "$scratch_results" ]; then rm -rf -- "$scratch_results"; fi
    }
    trap cleanup EXIT
    git init -q "$scratch_repo"
    mkdir -p "$scratch_repo/scripts/lib"
    cp "$repo_dir/test.sh" "$scratch_repo/test.sh"
    cp "$repo_dir/scripts/lib/test_helpers.sh" "$scratch_repo/scripts/lib/test_helpers.sh"
    scratch_project="$(compute_project_name "$scratch_repo")"
    scratch_results="/tmp/bifrost-$scratch_project"
    mkdir -p "$scratch_results"
    chmod 1777 "$scratch_results"
    touch "$scratch_results/test-stack.lock"
    chmod 000 "$scratch_results/test-stack.lock"
    scratch_lock_dir="$(git -C "$scratch_repo" rev-parse --path-format=absolute --git-path bifrost-test-locks)"
    mkdir -p "$scratch_lock_dir"
    exec {scratch_lock}>"$scratch_lock_dir/test-stack.lock"
    flock -n "$scratch_lock"
    if output=$(bash "$scratch_repo/test.sh" stack down 2>&1); then
        echo "FAIL: scratch lifecycle command acquired an owned stack" >&2
        exit 1
    fi
    [[ "$output" == *"another test command owns this worktree's test stack"* ]]
    exec {scratch_lock}>&-
    if output=$(bash "$scratch_repo/test.sh" stack invalid-command 2>&1); then
        echo "FAIL: invalid lifecycle command succeeded" >&2
        exit 1
    fi
    [[ "$output" == *"Unknown stack subcommand: invalid-command"* ]]
    [[ "$output" != *"Permission denied"* ]]
)
echo 'PASS: container-writable result permissions do not control host stack locks'
