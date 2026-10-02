#!/usr/bin/env bash
# Supported disposable Linux Docker lane only. No production or Rust admission.
set -euo pipefail
spike_root=$(cd "$(dirname "$0")/.." && pwd)
evidence_dir=${1:?absolute evidence output directory required}
case "$evidence_dir" in /*) ;; *) exit 2;; esac
mkdir -p "$evidence_dir"
toolchain_image=golang:1.27.1-bookworm@sha256:966278043a40889499db9b0cd196fc789c37c385d41bd9a10cb1e7764af60cdc
scratch=$(mktemp -d)
chmod 755 "$scratch"
cleanup() { chmod -R u+w "$scratch"; rm -rf "$scratch"; }
trap cleanup EXIT
mkdir -p "$scratch/modules" "$scratch/compiler" "$scratch/tests" "$scratch/out" "$scratch/results" "$scratch/warm-source"
cp -a "$spike_root/." "$scratch/warm-source/"
task_uid=$(id -u)
task_gid=$(id -g)

# A fixed official proxy/checksum service is the only fetch path. No credentials,
# direct VCS fallback, toolchain downloads, application code or hooks run here.
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user "$task_uid:$task_gid" --pids-limit 256 --memory 2g --cpus 2 \
  --tmpfs /tmp:rw,nosuid,nodev,size=512m \
  --mount "type=bind,src=$spike_root,dst=/src,readonly" \
  --mount "type=bind,src=$scratch/modules,dst=/modules" \
  --workdir /src "$toolchain_image" env -i PATH=/usr/local/go/bin:/usr/bin:/bin \
  HOME=/tmp GOTOOLCHAIN=local GOWORK=off CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
  GOMODCACHE=/modules GOCACHE=/tmp/cache GOPROXY=https://proxy.golang.org \
  GOSUMDB=sum.golang.org GONOPROXY=none GONOSUMDB=none \
  sh -c 'start=$(date +%s%N); go mod download; end=$(date +%s%N); echo "module_download_ns=$((end-start))"' \
  > "$evidence_dir/module-download.txt"

# Tenant tests cannot see final output, signing identity, Git credentials or the
# build compiler cache. Their entire writable state is disposed after testing.
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user "$task_uid:$task_gid" --pids-limit 256 --memory 3g --cpus 2 \
  --tmpfs /tmp:rw,nosuid,nodev,size=1g \
  --mount "type=bind,src=$spike_root,dst=/src,readonly" \
  --mount "type=bind,src=$scratch/modules,dst=/modules,readonly" \
  --workdir /src "$toolchain_image" env -i PATH=/usr/local/go/bin:/usr/bin:/bin \
  HOME=/tmp GOTOOLCHAIN=local GOWORK=off CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
  GOMODCACHE=/modules GOCACHE=/tmp/cache GOPROXY=off GOSUMDB=off GOFLAGS=-mod=readonly \
  sh -c 'test -z "$(gofmt -l .)"; go vet ./...; go test -count=1 -json ./...' \
  > "$evidence_dir/tests.jsonl"

build() {
  local source_dir=$1 label=$2
  docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
    --user "$task_uid:$task_gid" --pids-limit 256 --memory 3g --cpus 2 \
    --tmpfs /tmp:rw,nosuid,nodev,size=1g \
    --mount "type=bind,src=$source_dir,dst=/src,readonly" \
    --mount "type=bind,src=$scratch/modules,dst=/modules,readonly" \
    --mount "type=bind,src=$scratch/compiler,dst=/compiler" \
    --mount "type=bind,src=$scratch/out,dst=/out" \
    --workdir /src "$toolchain_image" env -i PATH=/usr/local/go/bin:/usr/bin:/bin \
    HOME=/tmp GOTOOLCHAIN=local GOWORK=off CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    GOMODCACHE=/modules GOCACHE=/compiler GOPROXY=off GOSUMDB=off GOFLAGS=-mod=readonly \
    sh -c 'start=$(date +%s%N); go build -trimpath -buildvcs=false -o /out/workflow ./cmd/workflow; end=$(date +%s%N); echo "compile_ns=$((end-start))"; go version -m /out/workflow; go list -m -json all; go version' \
    > "$evidence_dir/$label.txt"
}
build "$spike_root" cold
cp "$scratch/out/workflow" "$evidence_dir/workflow"
sha256sum "$evidence_dir/workflow" > "$evidence_dir/artifact.sha256"

# A real source edit changes executable behavior/output ordering, not comments.
edit_start_ns=$(date +%s%N)
python3 - "$scratch/warm-source/internal/readiness/readiness.go" <<'PY'
import pathlib,sys
p=pathlib.Path(sys.argv[1]); s=p.read_text()
assert 'sort.Strings(missing)' in s
p.write_text(s.replace('sort.Strings(missing)', 'sort.Sort(sort.Reverse(sort.StringSlice(missing)))'))
PY
build "$scratch/warm-source" warm-edit
edit_end_ns=$(date +%s%N)
echo "edit_to_artifact_ns=$((edit_end_ns-edit_start_ns))" > "$evidence_dir/edit-loop.txt"
cp "$scratch/out/workflow" "$evidence_dir/workflow-warm-edit"
sha256sum "$scratch/out/workflow" > "$evidence_dir/warm-artifact.sha256"
build "$spike_root" restored-warm
cmp "$scratch/out/workflow" "$evidence_dir/workflow"

# Prepare the measurement harness separately; it never compiles a workflow at run.
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user "$task_uid:$task_gid" --pids-limit 256 --memory 3g --cpus 2 \
  --tmpfs /tmp:rw,nosuid,nodev,size=1g \
  --mount "type=bind,src=$spike_root,dst=/src,readonly" \
  --mount "type=bind,src=$scratch/compiler,dst=/compiler" \
  --mount "type=bind,src=$scratch/modules,dst=/modules,readonly" \
  --mount "type=bind,src=$scratch/out,dst=/out" \
  --workdir /src "$toolchain_image" env -i PATH=/usr/local/go/bin:/usr/bin:/bin \
  HOME=/tmp GOTOOLCHAIN=local GOWORK=off CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
  GOMODCACHE=/modules GOCACHE=/compiler GOPROXY=off GOSUMDB=off GOFLAGS=-mod=readonly \
  go build -trimpath -buildvcs=false -o /out/probe ./cmd/probe

# Empty root with only the two immutable binaries mounted: no compiler, shell,
# secrets, module cache or external network. /out is only fixture measurement data.
docker build --file "$spike_root/runtime.Dockerfile" --tag bifrost-go-spike-runtime "$spike_root"
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user "$task_uid:$task_gid" --pids-limit 64 --memory 128m --cpus 2 \
  --tmpfs /tmp:rw,nosuid,nodev,size=16m \
  --mount "type=bind,src=$scratch/out/probe,dst=/probe,readonly" \
  --mount "type=bind,src=$evidence_dir/workflow,dst=/workflow,readonly" \
  --mount "type=bind,src=$scratch/results,dst=/out" \
  --entrypoint /probe bifrost-go-spike-runtime --artifact /workflow --output /out/execution.json
cp "$scratch/results/execution.json" "$evidence_dir/execution.json"
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user "$task_uid:$task_gid" --pids-limit 64 --memory 128m --cpus 2 \
  --tmpfs /tmp:rw,nosuid,nodev,size=16m \
  --mount "type=bind,src=$scratch/out/probe,dst=/probe,readonly" \
  --mount "type=bind,src=$evidence_dir/workflow-warm-edit,dst=/workflow,readonly" \
  --mount "type=bind,src=$scratch/results,dst=/out" \
  --entrypoint /probe bifrost-go-spike-runtime --artifact /workflow --output /out/warm-execution.json
cp "$scratch/results/warm-execution.json" "$evidence_dir/warm-execution.json"

docker build --file "$spike_root/builder.Dockerfile" --tag bifrost-go-spike-scanner "$spike_root"
docker image inspect bifrost-go-spike-scanner --format '{{.Id}}' > "$evidence_dir/scanner-image.txt"
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user "$task_uid:$task_gid" --pids-limit 256 --memory 3g --cpus 2 \
  --tmpfs /tmp:rw,nosuid,nodev,size=1g \
  --mount "type=bind,src=$spike_root,dst=/src,readonly" \
  --mount "type=bind,src=$scratch/modules,dst=/modules,readonly" \
  --workdir /src bifrost-go-spike-scanner env -i PATH=/usr/local/go/bin:/usr/bin:/bin \
  HOME=/tmp GOTOOLCHAIN=local GOWORK=off CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
  GOMODCACHE=/modules GOCACHE=/tmp/cache GOPROXY=off GOSUMDB=off GOFLAGS=-mod=readonly \
  /opt/tools/govulncheck -json ./... > "$evidence_dir/govulncheck.jsonl"

python3 "$spike_root/scripts/describe.py" "$spike_root" "$evidence_dir" "$toolchain_image"
