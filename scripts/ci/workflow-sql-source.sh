#!/usr/bin/env bash
# Supported CI source gate; never execute on the physical Proxmox host.
set -euo pipefail
credential_action() {
    python3 - "$1" <<'PY'
import json
import os
import stat
import sys
from pathlib import Path


def identity(info):
    return {"dev": info.st_dev, "ino": info.st_ino, "uid": info.st_uid}


def checked_directory(path, expected):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (
            identity(info) != expected or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ValueError("credential directory identity mismatch")
        return fd
    except BaseException as original:
        try:
            os.close(fd)
        finally:
            raise original


def remove_owned(directory, expected):
    try:
        fd = checked_directory(directory, expected["directory"])
    except FileNotFoundError:
        print("credential_absent")
        return
    try:
        try:
            info = os.stat("token", dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            info = None
        if info is not None:
            if not stat.S_ISREG(info.st_mode) or identity(info) != expected["file"]:
                raise ValueError("credential file identity mismatch")
            os.unlink("token", dir_fd=fd)
        if os.listdir(fd):
            raise ValueError("credential directory is not empty")
        # Verify the path still refers to our open directory before removing it.
        if identity(os.lstat(directory)) != expected["directory"]:
            raise ValueError("credential directory identity mismatch")
        os.rmdir(directory)
        if os.path.lexists(directory):
            raise ValueError("credential disposal failed")
        print("credential_disposed")
    finally:
        os.close(fd)


try:
    action = sys.argv[1]
    directory = Path(os.environ["RUNNER_TEMP"]) / (
        "workflow-sql-action-credential-"
        + os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"]
    )
    if action == "materialize":
        token = os.environ["GITHUB_TOKEN"].encode("ascii")
        if not 0 < len(token) <= 4096 or any(byte <= 32 or byte >= 127 for byte in token):
            raise ValueError("invalid action credential")
        os.mkdir(directory, 0o700)
        expected = {"directory": identity(os.lstat(directory)), "file": None}
        complete = False
        fd = -1
        file_fd = -1
        try:
            fd = checked_directory(directory, expected["directory"])
            file_fd = os.open(
                "token", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600, dir_fd=fd,
            )
            expected["file"] = identity(os.fstat(file_fd))
            remaining = memoryview(token)
            while remaining:
                written = os.write(file_fd, remaining)
                if not written:
                    raise ValueError("credential write failed")
                remaining = remaining[written:]
            os.close(file_fd)
            file_fd = -1
            if stat.S_IMODE(os.stat("token", dir_fd=fd).st_mode) != 0o600:
                raise ValueError("credential file mode mismatch")
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write("path=" + str(directory / "token") + "\n")
                output.write("identity=" + json.dumps(expected, separators=(",", ":")) + "\n")
            complete = True
        finally:
            original = sys.exc_info()[1]
            cleanup_error = None
            # A close failure must not prevent the remaining closes/removal.
            for owned_fd in (file_fd, fd):
                if owned_fd >= 0:
                    try:
                        os.close(owned_fd)
                    except BaseException as error:
                        if cleanup_error is None:
                            cleanup_error = error
            if not complete:
                try:
                    remove_owned(directory, expected)
                except BaseException as error:
                    if cleanup_error is None:
                        cleanup_error = error
            if cleanup_error is not None:
                raise original if original is not None else cleanup_error
    elif action in {"validate", "cleanup"}:
        if os.environ["BIFROST_ACTION_PIN_TOKEN_FILE"] != str(directory / "token"):
            raise ValueError("credential path mismatch")
        expected = json.loads(os.environ["SQL_SOURCE_ACTION_CREDENTIAL_IDENTITY"])
        if action == "cleanup":
            remove_owned(directory, expected)
        else:
            fd = checked_directory(directory, expected["directory"])
            try:
                info = os.stat("token", dir_fd=fd, follow_symlinks=False)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or identity(info) != expected["file"]
                    or info.st_uid != os.getuid()
                    or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600
                ):
                    raise ValueError("credential file custody mismatch")
                print("credential_present_private")
            finally:
                os.close(fd)
    else:
        raise ValueError("invalid credential action")
except Exception:
    # Never expose credentials, their hash, or raw exception diagnostics.
    print("action credential custody failed", file=sys.stderr)
    sys.exit(1)
PY
}

case "${1:-}" in
    credential-materialize) credential_action materialize; exit ;;
    credential-cleanup) credential_action cleanup; exit ;;
esac
mode="${1:?format or verify required}"
case "$mode" in format|verify) ;; *) exit 2 ;; esac
credential_owned=0
dispose_credential() {
    if test "$credential_owned" = 1; then
        credential_action cleanup || return 1
        credential_owned=0
        unset BIFROST_ACTION_PIN_TOKEN_FILE SQL_SOURCE_ACTION_CREDENTIAL_IDENTITY
    fi
}
early_cleanup() {
    original=$?
    trap - EXIT
    set +e
    dispose_credential
    removed=$?
    if test "$original" = 0 && test "$removed" != 0; then original=1; fi
    exit "$original"
}
trap early_cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if test "$mode" = verify; then
    credential_owned=1
    credential_admission="$(credential_action validate)"
fi
cd "$(git rev-parse --show-toplevel)"
source scripts/lib/test_helpers.sh
test -z "$(git status --porcelain --untracked-files=all)"
test "$(git rev-parse HEAD)" = "${GITHUB_SHA:?exact hosted candidate required}"
git -c credential.helper= -c http.extraheader= fetch origin main
git merge-base --is-ancestor origin/main HEAD
export COMPOSE_FILE=docker-compose.test.yml
export COMPOSE_PROJECT_NAME="$(compute_project_name .)"
export LOG_DIR="/tmp/bifrost-$COMPOSE_PROJECT_NAME"
evidence="${RUNNER_TEMP:?private hosted evidence root required}/workflow-sql-source"
formatter="$COMPOSE_PROJECT_NAME-sql-formatter"
image="$COMPOSE_PROJECT_NAME-sql-tools"
test -z "$(docker ps -aq --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")"
test -z "$(docker volume ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")"
test -z "$(docker network ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")"
test -z "$(docker ps -aq --filter "name=^/$formatter$")"
test ! -e "$evidence"
umask 077
mkdir -p "$evidence"
printf 'WORKFLOW_SQL_SOURCE_EVIDENCE=%s\n' "$evidence" >> "${GITHUB_ENV:?artifact path required}"
formatter_created=0
tests_started=0
targeted_started=0
image_id=
cleanup() {
    original=$?
    trap - EXIT
    set +e
    cleanup_status=0
    dispose_credential >> "$evidence/credential-disposal.txt" 2>&1 || cleanup_status=1
    if test "$targeted_started" = 1; then
        cp "$LOG_DIR/test-results.xml" "$evidence/test-results.xml" || cleanup_status=1
    fi
    if test "$formatter_created" = 1; then
        facts="$(docker inspect "$formatter" --format '{{.Name}}|{{index .Config.Labels "com.docker.compose.project"}}|{{.Image}}')" || cleanup_status=1
        if test "$facts" = "/$formatter|$COMPOSE_PROJECT_NAME|$image_id"; then
            docker rm -f "$formatter" > "$evidence/formatter-removal.txt" 2>&1 || cleanup_status=1
        else
            cleanup_status=1
        fi
    fi
    if test "$tests_started" = 1; then
        ./test.sh stack down > "$evidence/cleanup.log" 2>&1 || cleanup_status=1
    fi
    inspection_status=0
    containers="$(docker ps -aq --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")" || inspection_status=1
    volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")" || inspection_status=1
    networks="$(docker network ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")" || inspection_status=1
    test -z "$containers$volumes$networks" || inspection_status=1
    printf 'cleanup_status=%s\ninspection_status=%s\ncontainers=%s\nvolumes=%s\nnetworks=%s\n' "$cleanup_status" "$inspection_status" "$containers" "$volumes" "$networks" > "$evidence/project-resources.txt" || inspection_status=1
    if test "$original" = 0 && test "$cleanup_status$inspection_status" != 00; then original=1; fi
    if ! printf '%s\n' "$original" > "$evidence/exit-status.txt"; then
        if test "$original" = 0; then original=1; fi
    fi
    exit "$original"
}
trap cleanup EXIT
git rev-parse HEAD HEAD^{tree} > "$evidence/source.txt"
printf '%s\n' "$mode" > "$evidence/mode.txt"
sha256sum core-rs/crates/bifrost-db/src/workflow_parity.rs core-rs/crates/bifrost-db/src/lib.rs core-rs/crates/bifrost-db/Cargo.toml core-rs/Cargo.lock api/tests/parity/test_workflow_sql.py api/scripts/check_github_action_pins.py api/tests/unit/test_github_action_pins.py scripts/ci/tests/test_workflow_sql_credential.py scripts/ci/workflow-sql-source.sh .github/workflows/workflow-sql-source.yml core-rs/crates/bifrost-db/src/workflow_numeric.rs .github/workflows/workflow-numeric-format.yml > "$evidence/source-hashes.txt"
if test "$mode" = format; then
    docker build --target toolchain -t "$image" -f core-rs/Dockerfile core-rs > "$evidence/toolchain-build.log" 2>&1
    image_id="$(docker image inspect "$image" --format '{{.Id}}')"
    printf '%s\n' "$image_id" > "$evidence/formatter-image.txt"
    formatter_created=1
    docker create --name "$formatter" --label "com.docker.compose.project=$COMPOSE_PROJECT_NAME" --network none -v "$PWD/core-rs:/workspace/core-rs" "$image" cargo fmt --all > "$evidence/formatter-container.txt"
    docker start -a "$formatter" > "$evidence/formatter.log" 2>&1
    test "$(docker inspect "$formatter" --format '{{.State.ExitCode}}')" = 0
    git diff --name-only > "$evidence/format-paths.txt"
    while IFS= read -r path; do
        case "$path" in core-rs/crates/bifrost-db/src/workflow_parity.rs|core-rs/crates/bifrost-db/src/lib.rs|core-rs/crates/bifrost-db/src/workflow_numeric.rs) ;; *) exit 1 ;; esac
    done < "$evidence/format-paths.txt"
    git diff --binary > "$evidence/format.patch"
    # Formatting modifies this disposable checkout. No candidate checks or SQL
    # acceptance is inferred from this mode; root reviews/applies the patch.
else
    printf '%s\n' "$credential_admission" > "$evidence/credential-admission.txt"
    tests_started=1
    ./test.sh pre-pr > "$evidence/pre-pr.log" 2>&1
    dispose_credential > "$evidence/credential-disposal.txt" 2>&1
    test -z "$(git status --porcelain --untracked-files=all)"
    docker build --target checks -t "$image" -f core-rs/Dockerfile core-rs > "$evidence/rust-checks.log" 2>&1
    image_id="$(docker image inspect "$image" --format '{{.Id}}')"
    printf '%s\n' "$image_id" > "$evidence/checks-image.txt"
    # Nondefault SQL unit tests run explicitly; they require no live DB.
    formatter_created=1
    docker create --name "$formatter" --label "com.docker.compose.project=$COMPOSE_PROJECT_NAME" --network none "$image" cargo test --locked --offline -p bifrost-db --features workflow-sql-parity --lib > "$evidence/unit-container.txt"
    docker start -a "$formatter" > "$evidence/sql-unit.log" 2>&1
    test "$(docker inspect "$formatter" --format '{{.State.ExitCode}}')" = 0
    rm -f "$LOG_DIR/test-results.xml"
    targeted_started=1
    ./test.sh tests/unit/test_github_action_pins.py tests/parity/test_workflow_sql.py -v > "$evidence/sql-order.log" 2>&1
    test -z "$(git status --porcelain --untracked-files=all)"
fi
