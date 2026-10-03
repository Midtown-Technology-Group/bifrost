#!/usr/bin/env bash
# Supported hosted-container lane only; never invoke on the physical host.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
source scripts/lib/test_helpers.sh
unset BIFROST_PROJECT_PREFIX
project="$(compute_project_name .)-writer-primer"
export PRIMER_RESULTS="/tmp/bifrost-$project/writer-primer"
compose=(docker compose -p "$project" -f docker-compose.writer-primer.yml)
pool_image=edoburu/pgbouncer@sha256:9c78945868a6a142c7fc40ccd843bbe5a606df163c7ffce4de70e0d628d696a2
pg_image=pgvector/pgvector@sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a
paths=(docker-compose.writer-primer.yml scripts/ci/writer-primer/pgbouncer.ini scripts/ci/writer-primer/userlist.txt scripts/ci/writer-primer/provision.sql scripts/ci/writer-primer/probe.py scripts/ci/writer-primer.sh .github/workflows/writer-principal-primer.yml)
test -z "$(git status --porcelain --untracked-files=all)"
test "$(git rev-parse HEAD)" = "${GITHUB_SHA:?hosted exact candidate required}"
# Exact public repository; no checkout credential persistence or token injection.
git -c credential.helper= -c http.extraheader= fetch origin main
git merge-base --is-ancestor origin/main HEAD
admission_name="$project-pool-admission"
test -z "$(docker ps -aq --no-trunc --filter "name=^/$admission_name$")"
for service in pool migrate provision probe; do
  test -z "$(docker ps -aq --no-trunc --filter "name=^/$project-$service-oneoff$")"
done
for kind in container volume network; do
  case "$kind" in
    container) found="$(docker ps -aq --filter "label=com.docker.compose.project=$project")" ;;
    volume) found="$(docker volume ls -q --filter "label=com.docker.compose.project=$project")" ;;
    network) found="$(docker network ls -q --filter "label=com.docker.compose.project=$project")" ;;
  esac
  test -z "$found"
done
test ! -e "$PRIMER_RESULTS"
mkdir -p "$PRIMER_RESULTS"
chmod 777 "$PRIMER_RESULTS" # Existing UID1000 test image; trusted synthetic output.
printf 'WRITER_PRIMER_RESULTS=%s\n' "$PRIMER_RESULTS" >> "${GITHUB_ENV:?hosted artifact path required}"
private="$RUNNER_TEMP/writer-primer-private"
mkdir -m 700 "$private"
admission_cidfile="$private/pool-admission.cid"
admission_started=0
pool_image_id=
pg_image_id=
api_image_id=
declare -A oneoff_started=([pool]=0 [migrate]=0 [provision]=0 [probe]=0)
cleanup() {
  original=$?
  trap - EXIT
  set +e
  cleanup_status=0
  admission_disposal=not_started
  # timeout may kill Docker's client without killing its standalone container.
  # A private CID plus exact creation name/labels/image identifies only ours.
  if test "$admission_started" = 1; then
    admission_disposal=unverified
    cid=
    if test -s "$admission_cidfile"; then
      if ! cid="$(cat "$admission_cidfile")"; then cleanup_status=1; cid=; fi
      if ! [[ "$cid" =~ ^[0-9a-f]{64}$ ]]; then cleanup_status=1; cid=; fi
    else
      # Creation may precede the CID-file write. Initial exact-name absence,
      # started flag and verified name/labels/image cover only that creation.
      if ! cid="$(docker ps -aq --no-trunc --filter "name=^/$admission_name$")"; then cleanup_status=1; cid=; fi
      if ! [[ "$cid" =~ ^[0-9a-f]{64}$ ]]; then cleanup_status=1; cid=; fi
    fi
    if test -n "$cid"; then
      present_status=0
      present="$(docker ps -aq --no-trunc --filter "id=$cid")" || present_status=1
      if test "$present_status" != 0; then
        cleanup_status=1
      elif test -n "$present"; then
        facts_status=0
        facts="$(docker container inspect "$cid" --format '{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "bifrost.writer-primer.admission"}}|{{.Image}}|{{.Name}}')" || facts_status=1
        if test "$facts_status" = 0 && test "$present" = "$cid" && test "$facts" = "$project|$GITHUB_SHA|$pool_image_id|/$admission_name"; then
          if timeout 20 docker rm -f "$cid" > "$private/admission-removal.log" 2>&1; then
            admission_disposal=verified_removed
          else
            cleanup_status=1
          fi
        else
          cleanup_status=1 # Never remove an unknown/mismatched container.
        fi
      else
        admission_disposal=verified_absent
      fi
    fi
  fi
  printf 'admission_disposal=%s\n' "$admission_disposal" > "$PRIMER_RESULTS/admission-disposal.txt" || cleanup_status=1
  # Compose down excludes run one-offs. Only verified task creations may be
  # removed; an unknown/mismatched one-off is retained and blocks success.
  oneoff_status=0
  oneoff_cids="$(docker ps -aq --no-trunc --filter "label=com.docker.compose.project=$project" --filter 'label=com.docker.compose.oneoff=True')" || oneoff_status=1
  if test "$oneoff_status" != 0; then cleanup_status=1; fi
  for cid in $oneoff_cids; do
    if ! [[ "$cid" =~ ^[0-9a-f]{64}$ ]]; then cleanup_status=1; continue; fi
    facts_status=0
    facts="$(docker container inspect "$cid" --format '{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.oneoff"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{index .Config.Labels "bifrost.writer-primer.candidate"}}|{{.Image}}|{{.Name}}')" || facts_status=1
    service=
    expected_image=
    for admitted in pool migrate provision probe; do
      case "$admitted" in
        pool) image="$pool_image_id" ;;
        provision) image="$pg_image_id" ;;
        migrate|probe) image="$api_image_id" ;;
      esac
      if test "$facts_status" = 0 && test "${oneoff_started[$admitted]}" = 1 && [[ "$image" =~ ^sha256:[0-9a-f]{64}$ ]] && test "$facts" = "$project|True|$admitted|$GITHUB_SHA|$image|/$project-$admitted-oneoff"; then
        service="$admitted"
        expected_image="$image"
      fi
    done
    if test -n "$service" && test -n "$expected_image"; then
      timeout 20 docker rm -f "$cid" > "$private/$service-oneoff-removal.log" 2>&1 || cleanup_status=1
    else
      cleanup_status=1 # No deletion on missing/unknown/mismatched custody.
    fi
  done
  # No orphan sweep: unknown/mismatched standalone resources remain failures.
  timeout 90 "${compose[@]}" down -v > "$private/cleanup.log" 2>&1 || cleanup_status=1
  inspection=0
  containers="$(docker ps -aq --filter "label=com.docker.compose.project=$project")" || inspection=1
  volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=$project")" || inspection=1
  networks="$(docker network ls -q --filter "label=com.docker.compose.project=$project")" || inspection=1
  test -z "$containers$volumes$networks" || inspection=1
  printf 'project=%s\ncleanup_status=%s\ninspection_status=%s\ncontainers=%s\nvolumes=%s\nnetworks=%s\n' "$project" "$cleanup_status" "$inspection" "$containers" "$volumes" "$networks" > "$PRIMER_RESULTS/project-resources.txt" || inspection=1
  after="$(sha256sum "${paths[@]}")" || inspection=1
  test "$after" = "$source_hashes" || inspection=1
  if test "$original" = 0 && test "$cleanup_status$inspection" != 00; then original=1; fi
  printf '%s\n' "$original" > "$PRIMER_RESULTS/exit-status.txt" || original=1
  exit "$original"
}
source_hashes="$(sha256sum "${paths[@]}")"
trap cleanup EXIT
printf '%s\n' "$source_hashes" > "$PRIMER_RESULTS/source-hashes.txt"
git rev-parse HEAD HEAD^{tree} > "$PRIMER_RESULTS/source.txt"
# FIRST: no PG startup/DB/role can precede immutable pool admission.
timeout 120 docker pull --platform linux/amd64 "$pool_image" > "$private/pool-pull.log" 2>&1
docker image inspect "$pool_image" --format '{{.Id}} {{.Os}}/{{.Architecture}} {{json .RepoDigests}} {{json .Config.Entrypoint}} {{json .Config.Cmd}} {{.Config.User}}' > "$PRIMER_RESULTS/pool-image.txt"
test "$(docker image inspect "$pool_image" --format '{{.Os}}/{{.Architecture}} {{json .Config.Entrypoint}} {{json .Config.Cmd}} {{.Config.User}}')" = 'linux/amd64 ["/entrypoint.sh"] ["/usr/bin/pgbouncer","/etc/pgbouncer/pgbouncer.ini"] postgres'
pool_image_id="$(docker image inspect "$pool_image" --format '{{.Id}}')"
[[ "$pool_image_id" =~ ^sha256:[0-9a-f]{64}$ ]]
admission_started=1
timeout 20 docker run --rm --platform linux/amd64 --name "$admission_name" --cidfile "$admission_cidfile" --network none --label "com.docker.compose.project=$project" --label "bifrost.writer-primer.admission=$GITHUB_SHA" --entrypoint /bin/sh "$pool_image" -c 'test -x /usr/bin/pgbouncer && test -d /etc/pgbouncer && sha256sum /entrypoint.sh && /usr/bin/pgbouncer --version' > "$PRIMER_RESULTS/pool-admission.txt"
test "$(head -n 1 "$PRIMER_RESULTS/pool-admission.txt")" = '9d9d23849f0180d7fb25263dca3870955c39e0fcf0211529b10238f280143333  /entrypoint.sh'
grep -qx 'PgBouncer 1.26.0' "$PRIMER_RESULTS/pool-admission.txt"
oneoff_started[pool]=1
timeout 20 "${compose[@]}" run --rm --no-deps --name "$project-pool-oneoff" --entrypoint /bin/sh pool -c 'test -r /etc/pgbouncer/pgbouncer.ini && test -r /etc/pgbouncer/userlist.txt && sha256sum /etc/pgbouncer/pgbouncer.ini /etc/pgbouncer/userlist.txt' > "$PRIMER_RESULTS/pool-config-admission.txt"
test "$(sed -n '1s/ .*//p' "$PRIMER_RESULTS/pool-config-admission.txt")" = "$(sha256sum scripts/ci/writer-primer/pgbouncer.ini | cut -d' ' -f1)"
test "$(sed -n '2s/ .*//p' "$PRIMER_RESULTS/pool-config-admission.txt")" = "$(sha256sum scripts/ci/writer-primer/userlist.txt | cut -d' ' -f1)"
timeout 120 docker pull --platform linux/amd64 "$pg_image" > "$private/pg-pull.log" 2>&1
docker image inspect "$pg_image" --format '{{.Id}} {{.Os}}/{{.Architecture}} {{json .RepoDigests}}' > "$PRIMER_RESULTS/pg-image.txt"
test "$(docker image inspect "$pg_image" --format '{{.Os}}/{{.Architecture}}')" = linux/amd64
pg_image_id="$(docker image inspect "$pg_image" --format '{{.Id}}')"
[[ "$pg_image_id" =~ ^sha256:[0-9a-f]{64}$ ]]
# API built from this exact clean candidate, never a mutable external shortcut.
timeout 1200 "${compose[@]}" build migrate > "$private/api-build.log" 2>&1
docker image inspect bifrost-writer-primer-api:local --format '{{.Id}}' > "$PRIMER_RESULTS/api-image-before.txt"
api_image_id="$(cat "$PRIMER_RESULTS/api-image-before.txt")"
[[ "$api_image_id" =~ ^sha256:[0-9a-f]{64}$ ]]
timeout 90 "${compose[@]}" up -d --wait --wait-timeout 60 postgres > "$private/postgres-up.log" 2>&1
oneoff_started[migrate]=1
timeout 360 "${compose[@]}" run --rm --no-deps --name "$project-migrate-oneoff" migrate > "$private/migrate.log" 2>&1
oneoff_started[provision]=1
timeout 30 "${compose[@]}" run --rm --no-deps --name "$project-provision-oneoff" provision > "$private/provision.log" 2>&1
timeout 90 "${compose[@]}" up -d --wait --wait-timeout 60 pool > "$private/pool-up.log" 2>&1
oneoff_started[probe]=1
timeout 135 "${compose[@]}" run --rm --no-deps --name "$project-probe-oneoff" probe > "$private/probe.log" 2>&1
docker image inspect bifrost-writer-primer-api:local --format '{{.Id}}' > "$PRIMER_RESULTS/api-image-after.txt"
cmp "$PRIMER_RESULTS/api-image-before.txt" "$PRIMER_RESULTS/api-image-after.txt"
# Source/candidate and actual collected bytes are associated by a host receipt.
python - <<'PY'
import hashlib
import json
import os
from pathlib import Path
import subprocess

root = Path(os.environ['PRIMER_RESULTS'])
probe = root / 'probe.json'
if probe.stat().st_size > 2 * 1024 * 1024:
    raise SystemExit('primer receipt cap failed')
value = json.loads(probe.read_bytes())
if value['schema'] != 'bifrost.test.writer-primer/v1' or len(value['samples']) != 44:
    raise SystemExit('primer receipt shape failed')
source = subprocess.check_output(['git', 'rev-parse', 'HEAD', 'HEAD^{tree}'], text=True).splitlines()
hashes = {}
for line in (root / 'source-hashes.txt').read_text().splitlines():
    digest, path = line.split('  ', 1)
    hashes[path] = digest
head = value['samples'][0]['tables'][6]['rows']
if head != [['20261001_solution_src_account']]:
    raise SystemExit('primer migration revision failed')
receipt = {
    'schema': 'bifrost.test.writer-primer-custody/v1',
    'candidate_sha': source[0], 'candidate_tree': source[1],
    'source_sha256': hashes,
    'probe_sha256': hashlib.sha256(probe.read_bytes()).hexdigest(),
    'migration_head': head[0][0],
    'requested_pool_reference': 'edoburu/pgbouncer@sha256:9c78945868a6a142c7fc40ccd843bbe5a606df163c7ffce4de70e0d628d696a2',
    'requested_pg_reference': 'pgvector/pgvector@sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a',
    'requested_platform': 'linux/amd64',
    'pool_image': (root / 'pool-image.txt').read_text().strip(),
    'pg_image': (root / 'pg-image.txt').read_text().strip(),
    'api_image': (root / 'api-image-before.txt').read_text().strip(),
}
with (root / 'receipt.json').open('x') as output:
    json.dump(receipt, output, sort_keys=True)
PY
printf 'PASS: restricted authenticated principal/pool primer only\n'
