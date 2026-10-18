#!/usr/bin/env bash
# Actual frozen-fork reader proof. Invoked only by the locked synthetic backup lane.
set -euo pipefail
umask 077

old_sha=5085cc467ffb1ed35de9c9ca48890ab0d929e1d0
old_repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
old_database=''
old_directory=''
old_run=''
old_variant=''
while [ "$#" -gt 0 ]; do
    case "$1" in
        --database) old_database="${2:?}"; shift 2 ;;
        --directory) old_directory="${2:?}"; shift 2 ;;
        --run-id) old_run="${2:?}"; shift 2 ;;
        --variant) old_variant="${2:?}"; shift 2 ;;
        *) echo 'Unexpected old-reader argument.' >&2; exit 2 ;;
    esac
done
[ "$(uname -s)" = Linux ]
[ "${BIFROST_RBAC_BACKUP_LOCKED:-}" = 1 ]
[[ "$old_run" =~ ^[0-9a-f]{32}$ ]]
[[ "$old_database" =~ ^bifrost_r2b_rehearsal_[0-9a-f]{12}$ ]]
case "$old_variant" in clean|delivery_started_historical_stamp|withdrawn_builder_residue) ;; *) exit 2 ;; esac
[ "$(realpath "${COMPOSE_FILE:?}")" = "$old_repo/docker-compose.test.yml" ]
[ "$(realpath "${LOG_DIR:?}")" = "/tmp/bifrost-${COMPOSE_PROJECT_NAME:?}" ]
old_directory="$(realpath "${old_directory:?}")"
[ "$old_directory" = "$LOG_DIR/rbac-backup-$old_run/$old_variant" ]
[ -f "$old_directory/old-reader-fixture.json" ]
git -C "$old_repo" cat-file -e "$old_sha^{commit}"

old_workspace="$(mktemp -d "/tmp/bifrost-old-reader-$old_run-$old_variant.XXXXXXXX")"
old_suffix="${old_workspace##*.}"
old_tag="bifrost-rbac-old-reader:$old_run-$old_variant-${old_suffix,,}"
old_container="bifrost-rbac-old-reader-$old_run-$old_variant-${old_suffix,,}"
old_network="${COMPOSE_PROJECT_NAME}_default"
old_postgres=''

finish_old_reader() {
    local result=$? container_removed=false image_removed=false temporary_source_removed=false envfile_removed=false
    trap - EXIT INT TERM
    if docker container inspect "$old_container" >/dev/null 2>&1; then
        docker container rm --force "$old_container" >/dev/null 2>&1 || result=1
    fi
    if remaining_containers="$(docker container ls --all --filter "name=$old_container" --format '{{.Names}}')"; then
        if [ -z "$remaining_containers" ]; then container_removed=true; fi
    else
        result=1
    fi
    if docker image inspect "$old_tag" >/dev/null 2>&1; then
        docker image rm "$old_tag" >/dev/null 2>&1 || result=1
    fi
    if remaining_images="$(docker image ls --filter "reference=$old_tag" --format '{{.ID}}')"; then
        if [ -z "$remaining_images" ]; then image_removed=true; fi
    else
        result=1
    fi
    # The literal prefix, run, variant and mktemp suffix must still match before deletion.
    if [[ "$old_workspace" =~ ^/tmp/bifrost-old-reader-$old_run-$old_variant\.[A-Za-z0-9]{8}$ ]]; then
        rm -rf -- "$old_workspace" || result=1
    else
        result=1
    fi
    if [ ! -e "$old_workspace" ]; then temporary_source_removed=true; fi
    if [ ! -e "$old_workspace/old-reader.env" ]; then envfile_removed=true; fi
    if [ "$container_removed$image_removed$temporary_source_removed$envfile_removed" != truetruetruetrue ]; then result=1; fi
    python3 - "$old_directory/old-reader-cleanup.json" "$result" "$container_removed" "$image_removed" \
        "$temporary_source_removed" "$envfile_removed" <<'PY'
import json
import pathlib
import sys
destination, result, container, image, source, envfile = sys.argv[1:]
data = dict(status='passed' if result == '0' else 'failed',
            container_removed=container == 'true', image_removed=image == 'true',
            temporary_source_removed=source == 'true', envfile_removed=envfile == 'true')
path = pathlib.Path(destination)
path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
path.chmod(0o644)
PY
    exit "$result"
}
trap finish_old_reader EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir "$old_workspace/source"
git -C "$old_repo" archive --format=tar "$old_sha" --output "$old_workspace/source.tar"
tar -xf "$old_workspace/source.tar" -C "$old_workspace/source"
# CI's archive owner may differ from the container's non-root UID. Only frozen
# source is made readable; credentials stay in a separate 0600 temporary file.
chmod -R a+rX "$old_workspace/source/api"
python3 - "$old_workspace" "$old_sha" <<'PY'
import hashlib
import json
import pathlib
import sys
root = pathlib.Path(sys.argv[1])
source = root / 'source'
files = {path.relative_to(source / 'api').as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
         for path in sorted((source / 'api').rglob('*.py'))}
data = dict(source_sha=sys.argv[2], source_archive_sha256=hashlib.sha256((root / 'source.tar').read_bytes()).hexdigest(),
            api_python_sha256=files, dependency_inputs_sha256={
                name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                for name in ('api/Dockerfile.dev', 'pyproject.toml', 'requirements.lock', 'requirements-pyright.lock')})
manifest = root / 'source-manifest.json'
manifest.write_text(json.dumps(data, sort_keys=True) + '\n')
manifest.chmod(0o644)
PY

# Build only the literal old dependency stage: no current source or dependency substitution.
docker build --target builder --file "$old_workspace/source/api/Dockerfile.dev" \
    --label "mtg.rbac-old-source=$old_sha" --label "mtg.rbac-old-run=$old_run" \
    --tag "$old_tag" "$old_workspace/source" > "$old_directory/old-reader-build.log" 2>&1
old_image="$(docker image inspect "$old_tag" --format '{{.Id}}')"
old_postgres="$(docker compose -f "$COMPOSE_FILE" ps -q postgres)"
[ -n "$old_postgres" ]
docker network inspect "$old_network" >/dev/null

# Inspect only the canonical synthetic test PostgreSQL container. Credentials go
# directly into a private temporary envfile, never argv, terminal output or receipts.
docker container inspect "$old_postgres" | python3 -c '
import json, pathlib, sys
from urllib.parse import quote
workspace, database, project, network = sys.argv[1:]
inspection = json.load(sys.stdin)
assert len(inspection) == 1
container = inspection[0]
labels = container["Config"]["Labels"]
assert labels["com.docker.compose.project"] == project
assert labels["com.docker.compose.service"] == "postgres"
assert network in container["NetworkSettings"]["Networks"]
environment = dict(item.split("=", 1) for item in container["Config"]["Env"] if "=" in item)
url = "postgresql+asyncpg://" + quote(environment["POSTGRES_USER"], safe="") + ":" + quote(environment["POSTGRES_PASSWORD"], safe="") + "@postgres:5432/" + database
path = pathlib.Path(workspace) / "old-reader.env"
path.write_text("BIFROST_OLD_DATABASE_URL=" + url + "\nBIFROST_OLD_READER_CONTAINER=1\nBIFROST_SECRET_KEY=" + "0" * 64 + "\n")
path.chmod(0o600)
' "$old_workspace" "$old_database" "$COMPOSE_PROJECT_NAME" "$old_network"

python3 - "$old_directory/old-reader-provenance.json" "$old_workspace/source-manifest.json" \
    "$old_image" "$old_tag" "$old_container" "$old_network" "$old_postgres" "$old_repo" <<'PY'
import hashlib
import json
import pathlib
import sys
destination, manifest, image, tag, container, network, postgres, repo = sys.argv[1:]
data = json.loads(pathlib.Path(manifest).read_text())
data.pop('api_python_sha256')
data.update(status='prepared', image_id=image, image_tag=tag, container_name=container, network=network,
            postgres_container=postgres, frozen_dependency_stage='api/Dockerfile.dev:builder',
            source_bind_read_only=True, host_ports_published=False, synthetic_data_only=True)
data['helper_source_sha256'] = {
    name: hashlib.sha256((pathlib.Path(repo) / name).read_bytes()).hexdigest()
    for name in ('scripts/rehearse-mtg-rbac-old-readers.py', 'scripts/rehearse-mtg-rbac-old-readers.sh')}
path = pathlib.Path(destination)
path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
path.chmod(0o644)
PY

docker run --rm --name "$old_container" --network "$old_network" \
    --read-only --cap-drop ALL --security-opt no-new-privileges --user 1000:1000 \
    --tmpfs /tmp:rw,nosuid,nodev,size=64m --env-file "$old_workspace/old-reader.env" \
    --env PYTHONPATH=/app --env PYTHONDONTWRITEBYTECODE=1 --workdir /app \
    --mount "type=bind,src=$old_workspace/source/api,dst=/app,readonly" \
    --mount "type=bind,src=$old_repo/scripts/rehearse-mtg-rbac-old-readers.py,dst=/harness/reader.py,readonly" \
    --mount "type=bind,src=$old_directory/old-reader-fixture.json,dst=/harness/fixture.json,readonly" \
    --mount "type=bind,src=$old_workspace/source-manifest.json,dst=/harness/source-manifest.json,readonly" \
    --entrypoint python "$old_image" -B /harness/reader.py \
    --fixture /harness/fixture.json --source-manifest /harness/source-manifest.json \
    > "$old_directory/old-reader-result.json" 2> "$old_directory/old-reader-container.log"
chmod 0644 "$old_directory/old-reader-result.json" "$old_directory/old-reader-build.log"
python3 - "$old_directory/old-reader-result.json" "$old_sha" "$old_directory/old-reader-provenance.json" <<'PY'
import json
import pathlib
import sys
result = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert result['status'] == 'passed' and result['source_commit'] == sys.argv[2]
assert result['transaction_read_only'] is True
provenance_path = pathlib.Path(sys.argv[3])
provenance = json.loads(provenance_path.read_text())
provenance['status'] = 'passed'
provenance_path.write_text(json.dumps(provenance, indent=2, sort_keys=True) + '\n')
print('Verified frozen-fork imported-reader recovery proof (synthetic DB, read-only).')
PY
