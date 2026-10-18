#!/usr/bin/env bash
# Invoked by test.sh after its worktree command lock and normal stack_up.
set -euo pipefail
umask 077

rehearsal_repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$rehearsal_repo"
source "$rehearsal_repo/scripts/lib/test_helpers.sh"
if [ "${BIFROST_RBAC_BACKUP_LOCKED:-}" != 1 ]; then
    echo 'Run ./test.sh rbac-backup-rehearsal so the existing test-stack lock is held.' >&2
    exit 2
fi
if [ "$(realpath "${COMPOSE_FILE:?}")" != "$rehearsal_repo/docker-compose.test.yml" ] || \
   [ "${COMPOSE_PROJECT_NAME:?}" != "$(BIFROST_PROJECT_PREFIX=bifrost-test compute_project_name .)" ]; then
    echo 'RBAC backup rehearsal requires this worktree’s canonical isolated test stack.' >&2
    exit 2
fi
if [ "$(realpath "${LOG_DIR:?}")" != "/tmp/bifrost-$COMPOSE_PROJECT_NAME" ]; then
    echo 'Unexpected test results directory; refusing rehearsal artifact writes.' >&2
    exit 2
fi
if [ -n "$(git status --porcelain --untracked-files=all)" ]; then
    echo 'RBAC backup evidence requires a clean committed candidate.' >&2
    exit 2
fi

rehearsal_head="$(git rev-parse HEAD)"
rehearsal_uid="$(id -u)"
rehearsal_gid="$(id -g)"
[[ "$rehearsal_uid" =~ ^[1-9][0-9]*$ ]]
[[ "$rehearsal_gid" =~ ^[0-9]+$ ]]
rehearsal_run="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
rehearsal_root="$LOG_DIR/rbac-backup-$rehearsal_run"
mkdir -m 0777 "$rehearsal_root"
compose=(docker compose -f "$COMPOSE_FILE")
active_variant=''
active_directory=''
active_step='prepare'
recovery_archive_fd=''
variants=(clean delivery_started_historical_stamp withdrawn_builder_residue)

phase() {
    # A non-root host identity bypasses entrypoint's recursive chown of the
    # shared results mount. Private state/archive remain owned by this caller.
    "${compose[@]}" --profile test run --rm --no-deps \
        --user "$rehearsal_uid:$rehearsal_gid" test-runner \
        python /repo/scripts/rehearse-mtg-rbac-backup.py "$1" \
        --directory "/tmp/bifrost/rbac-backup-$rehearsal_run/$active_variant" \
        --run-id "$rehearsal_run" --variant "$active_variant" "${@:2}"
}

finish() {
    local result=$?
    trap - EXIT INT TERM
    if [ -n "$active_variant" ]; then
        if ! phase cleanup --failed-host-step "$active_step" --failed-host-exit-code "$result" \
            >> "$active_directory/cleanup.log" 2>&1; then
            result=1
            echo "Synthetic DB cleanup was not verified; inspect $active_directory/receipt.json" >&2
        fi
    fi
    if [ -n "$recovery_archive_fd" ]; then
        exec {recovery_archive_fd}<&-
    fi
    if [ "$result" -ne 0 ]; then
        # Only literal per-run artifact paths; keep receipts/ownership ledger.
        for variant in "${variants[@]}"; do
            rm -f -- "$rehearsal_root/$variant/fork-head.dump"
        done
        echo "RBAC backup rehearsal failed. Evidence: $rehearsal_root" >&2
    fi
    exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Never pull host credentials or place fixture credentials in argv/logs.
# The already-running postgres service owns the matching pg16 client tools.
tool_versions="$("${compose[@]}" exec -T postgres sh -eu -c '
    export PGUSER="${POSTGRES_USER:?}" PGPASSWORD="${POSTGRES_PASSWORD:?}"
    export PGDATABASE=postgres PGHOST=127.0.0.1 PGPORT=5432
    pg_dump --version
    pg_restore --version
    psql --no-password --tuples-only --no-align --command="SHOW server_version_num"
')"
postgres_container="$("${compose[@]}" ps -q postgres)"
postgres_image="$(docker inspect --format '{{.Image}}' "$postgres_container")"
runner_image="$(docker image inspect bifrost-test-api-dev:latest --format '{{.Id}}')"
python3 - "$rehearsal_root/host-provenance.json" "$rehearsal_head" "$rehearsal_repo" \
    "$COMPOSE_PROJECT_NAME" "$postgres_container" "$postgres_image" "$runner_image" "$tool_versions" \
    "$rehearsal_uid" "$rehearsal_gid" <<'PY'
import hashlib
import json
import pathlib
import re
import sys
destination, head, worktree, project, container, postgres_image, runner_image, versions, uid, gid = sys.argv[1:]
lines = versions.splitlines()
assert len(lines) == 3 and lines[2].isdigit(), 'Unexpected PostgreSQL tool identity response'
server_major = int(lines[2]) // 10000
for value in lines[:2]:
    match = re.search(r'\(PostgreSQL\) (\d+)\.', value)
    assert match and int(match.group(1)) == server_major == 16, 'Matching PostgreSQL 16 tools required'
root = pathlib.Path(worktree)
sources = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in (
    'test.sh', 'scripts/lib/test_helpers.sh', 'scripts/rehearse-mtg-rbac-backup.sh', 'scripts/rehearse-mtg-rbac-backup.py',
    'scripts/rehearse-mtg-rbac-old-readers.sh', 'scripts/rehearse-mtg-rbac-old-readers.py')}
data = dict(git_head=head, clean_candidate=True, worktree=worktree, compose_project=project,
            postgres_container=container, postgres_image_id=postgres_image, runner_image_id=runner_image,
            tool_versions=lines, harness_source_sha256=sources, synthetic_data_only=True,
            phase_user_uid=int(uid), phase_user_gid=int(gid), phase_entrypoint_recursive_chown=False)
pathlib.Path(destination).write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
pathlib.Path(destination).chmod(0o644)
PY

for active_variant in "${variants[@]}"; do
    active_directory="$rehearsal_root/$active_variant"
    mkdir -m 0777 "$active_directory"
    source_database="bifrost_r2b_rehearsal_$(python3 -c 'import uuid; print(uuid.uuid4().hex[:12])')"
    restored_database="bifrost_r2b_rehearsal_$(python3 -c 'import uuid; print(uuid.uuid4().hex[:12])')"
    # Validate before either name reaches a subprocess/SQL phase.
    [[ "$source_database" =~ ^bifrost_r2b_rehearsal_[0-9a-f]{12}$ ]]
    [[ "$restored_database" =~ ^bifrost_r2b_rehearsal_[0-9a-f]{12}$ ]]
    echo "RBAC backup rehearsal: $active_variant (synthetic databases only)"
    active_step=prepare
    phase prepare --source "$source_database" --restored "$restored_database" \
        > "$active_directory/prepare.log" 2>&1
    archive="$active_directory/fork-head.dump"
    dump_started_ns="$(date +%s%N)"
    active_step=pg_dump
    "${compose[@]}" exec -T postgres sh -eu -c '
        export PGUSER="${POSTGRES_USER:?}" PGPASSWORD="${POSTGRES_PASSWORD:?}"
        export PGDATABASE="$1" PGHOST=127.0.0.1 PGPORT=5432
        exec pg_dump --no-password --format=custom
    ' sh "$source_database" > "$archive" 2> "$active_directory/pg_dump.log"
    dump_elapsed_ns="$(($(date +%s%N) - dump_started_ns))"
    dump_sha256="$(sha256sum "$archive" | cut -d ' ' -f 1)"
    # Retain one read descriptor for recovery. Both the host and every runner
    # phase use this caller's non-root UID; the archive stays 0600 throughout.
    exec {recovery_archive_fd}<"$archive"
    # Target was created empty and ownership-tagged by prepare. No --create,
    # no template clone and no application/shared database is a restore target.
    restore_started_ns="$(date +%s%N)"
    active_step=pg_restore
    "${compose[@]}" exec -T postgres sh -eu -c '
        export PGUSER="${POSTGRES_USER:?}" PGPASSWORD="${POSTGRES_PASSWORD:?}"
        export PGDATABASE="$1" PGHOST=127.0.0.1 PGPORT=5432
        exec pg_restore --no-password --dbname "$PGDATABASE" --exit-on-error --single-transaction --clean --if-exists
    ' sh "$restored_database" < "$archive" > "$active_directory/pg_restore.log" 2>&1
    restore_elapsed_ns="$(($(date +%s%N) - restore_started_ns))"
    active_step=restored
    phase restored --dump-sha256 "$dump_sha256" --dump-elapsed-ns "$dump_elapsed_ns" \
        --restore-elapsed-ns "$restore_elapsed_ns" > "$active_directory/restored.log" 2>&1
    active_step=interrupt
    phase interrupt > "$active_directory/interruption.log" 2>&1
    active_step=reset_target
    phase reset_target > "$active_directory/reset-target.log" 2>&1
    recovery_started_ns="$(date +%s%N)"
    active_step=recovery_pg_restore
    "${compose[@]}" exec -T postgres sh -eu -c '
        export PGUSER="${POSTGRES_USER:?}" PGPASSWORD="${POSTGRES_PASSWORD:?}"
        export PGDATABASE="$1" PGHOST=127.0.0.1 PGPORT=5432
        exec pg_restore --no-password --dbname "$PGDATABASE" --exit-on-error --single-transaction --clean --if-exists
    ' sh "$restored_database" <&"$recovery_archive_fd" > "$active_directory/recovery-pg_restore.log" 2>&1
    recovery_elapsed_ns="$(($(date +%s%N) - recovery_started_ns))"
    exec {recovery_archive_fd}<&-
    recovery_archive_fd=''
    active_step=recovered
    phase recovered --dump-sha256 "$dump_sha256" --dump-elapsed-ns "$dump_elapsed_ns" \
        --restore-elapsed-ns "$recovery_elapsed_ns" > "$active_directory/recovered.log" 2>&1
    active_step=old_readers
    bash "$rehearsal_repo/scripts/rehearse-mtg-rbac-old-readers.sh" \
        --database "$restored_database" --directory "$active_directory" \
        --run-id "$rehearsal_run" --variant "$active_variant" > "$active_directory/old-reader.log" 2>&1
    phase old_readers > "$active_directory/old-readers-verified.log" 2>&1
    active_step=upgrade
    phase upgrade > "$active_directory/upgrade.log" 2>&1
    active_step=cleanup
    phase cleanup > "$active_directory/cleanup.log" 2>&1
    active_variant=''
    active_directory=''
done

[ "$(git rev-parse HEAD)" = "$rehearsal_head" ]
[ -z "$(git status --porcelain --untracked-files=all)" ]
[ "$(docker image inspect bifrost-test-api-dev:latest --format '{{.Id}}')" = "$runner_image" ]
[ "$(docker inspect --format '{{.Image}}' "$postgres_container")" = "$postgres_image" ]
python3 - "$rehearsal_root" <<'PY'
import json
import pathlib
import sys
root = pathlib.Path(sys.argv[1])
data = json.loads((root / 'host-provenance.json').read_text())
data['variants'] = {}
for variant in ('clean', 'delivery_started_historical_stamp', 'withdrawn_builder_residue'):
    receipt = json.loads((root / variant / 'receipt.json').read_text())
    assert receipt['status'] == 'passed' and receipt['database_cleanup_verified'], 'Incomplete rehearsal proof'
    data['variants'][variant] = receipt
data['status'] = 'passed'
(root / 'proof.json').write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
(root / 'proof.json').chmod(0o644)
print('Verified fork-head dump/restore/full-graph proof: ' + str(root / 'proof.json'))
PY
