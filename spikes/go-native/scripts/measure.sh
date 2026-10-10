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
carrier_tag=''
cleanup() {
  original=$?
  trap - EXIT
  if [[ -n "$carrier_tag" ]]; then
    if ! docker image rm "$carrier_tag" > "$evidence_dir/carrier-image-cleanup.txt" 2>&1; then
      original=1
    fi
  fi
  chmod -R u+w "$scratch"
  rm -rf "$scratch"
  exit "$original"
}
trap cleanup EXIT
mkdir -p "$scratch/modules" "$scratch/compiler" "$scratch/tests" "$scratch/out" "$scratch/results" "$scratch/warm-source"
mkdir -p "$scratch/independent-compiler"
cp -a "$spike_root/." "$scratch/warm-source/"
task_uid=$(id -u)
task_gid=$(id -g)
if [ "$task_uid" -eq 0 ]; then
  echo 'The spike builder requires a nonroot supported-lane identity.' >&2
  exit 2
fi

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
  sh -c 'start=$(date +%s%N); go mod download; go mod verify; end=$(date +%s%N); echo "module_download_ns=$((end-start))"' \
  > "$evidence_dir/module-download.txt"

# Tenant tests cannot see final output, signing identity, Git credentials or the
# build compiler cache. Their entire writable state is disposed after testing.
# Go executes compiled tests from /tmp, so this dedicated test scratch is exec.
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user "$task_uid:$task_gid" --pids-limit 256 --memory 3g --cpus 2 \
  --tmpfs /tmp:rw,exec,nosuid,nodev,size=1g \
  --mount "type=bind,src=$spike_root,dst=/src,readonly" \
  --mount "type=bind,src=$scratch/modules,dst=/modules,readonly" \
  --workdir /src "$toolchain_image" env -i PATH=/usr/local/go/bin:/usr/bin:/bin \
  HOME=/tmp GOTOOLCHAIN=local GOWORK=off CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
  GOMODCACHE=/modules GOCACHE=/tmp/cache GOPROXY=off GOSUMDB=off GOFLAGS=-mod=readonly \
  sh -c 'test -z "$(gofmt -l .)"; go vet ./...; go test -count=1 -json ./...' \
  > "$evidence_dir/tests.jsonl"

build() {
  local source_dir=$1 label=$2
  local compiler_dir=${3:-$scratch/compiler}
  docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
    --user "$task_uid:$task_gid" --pids-limit 256 --memory 3g --cpus 2 \
    --tmpfs /tmp:rw,nosuid,nodev,size=1g \
    --mount "type=bind,src=$source_dir,dst=/src,readonly" \
    --mount "type=bind,src=$scratch/modules,dst=/modules,readonly" \
    --mount "type=bind,src=$compiler_dir,dst=/compiler" \
    --mount "type=bind,src=$scratch/out,dst=/out" \
    --workdir /src "$toolchain_image" env -i PATH=/usr/local/go/bin:/usr/bin:/bin \
    HOME=/tmp GOTOOLCHAIN=local GOWORK=off CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    GOMODCACHE=/modules GOCACHE=/compiler GOPROXY=off GOSUMDB=off GOFLAGS=-mod=readonly \
    sh -c 'start=$(date +%s%N); go build -trimpath -buildvcs=false -o /out/workflow ./cmd/workflow; end=$(date +%s%N); echo "compile_ns=$((end-start))"; go version -m /out/workflow; go list -m -json all; go mod graph > /out/module-graph.txt; go version' \
    > "$evidence_dir/$label.txt"
}
build "$spike_root" cold
cp "$scratch/out/workflow" "$evidence_dir/workflow"
cp "$scratch/out/module-graph.txt" "$evidence_dir/module-graph.txt"
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
python3 - "$scratch/warm-source" "$evidence_dir/warm-source.json" <<'PY'
import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1])
files=[{"path":str(p.relative_to(root)),"sha256":hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(root.rglob('*')) if p.is_file()]
pathlib.Path(sys.argv[2]).write_text(json.dumps(files,sort_keys=True,separators=(',',':'))+'\n')
PY
cp "$scratch/out/workflow" "$evidence_dir/workflow-warm-edit"
sha256sum "$scratch/out/workflow" > "$evidence_dir/warm-artifact.sha256"
build "$spike_root" restored-warm
cmp "$scratch/out/workflow" "$evidence_dir/workflow"
build "$spike_root" independent-cold "$scratch/independent-compiler"
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
  sh -c 'go build -trimpath -buildvcs=false -o /out/launcher ./cmd/launcher; go build -trimpath -buildvcs=false -o /out/adapter ./cmd/adapter; go build -trimpath -buildvcs=false -o /out/protocolprobe ./cmd/protocolprobe; go build -trimpath -buildvcs=false -o /out/probe ./cmd/probe; go build -trimpath -buildvcs=false -o /out/controlcheck ./cmd/controlcheck; go build -trimpath -buildvcs=false -o /out/schema ./cmd/schema; /out/schema /src/internal/readiness/readiness.go Input > /out/generated-input.json; /out/schema /src/internal/readiness/readiness.go Output > /out/generated-output.json'
cp "$scratch/out/generated-input.json" "$scratch/out/generated-output.json" "$evidence_dir/"
cp "$scratch/out/launcher" "$evidence_dir/launcher"
# First-party guardian carrier, distinct from the accepted workload archive.
# No SDK credential, application source, compiler, DB access or shell in its root.
mkdir -p "$scratch/carrier-context"
cp "$scratch/out/launcher" "$scratch/carrier-context/launcher"
carrier_candidate="bifrost-go-native-carrier:${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"
if docker image inspect "$carrier_candidate" >/dev/null 2>&1; then
  echo 'Task carrier tag already exists; refusing ownership reassignment.' >&2
  exit 2
fi
carrier_tag=$carrier_candidate
docker build --file "$spike_root/launcher.Dockerfile" --tag "$carrier_tag" \
  --iidfile "$evidence_dir/carrier-image-id.txt" "$scratch/carrier-context" \
  > "$evidence_dir/carrier-image-build.txt" 2>&1
docker image inspect "$carrier_tag" > "$evidence_dir/carrier-image-inspect.json"
docker image save --output "$evidence_dir/carrier-image.tar" "$carrier_tag"
cp "$scratch/out/adapter" "$evidence_dir/adapter"
cp "$scratch/out/protocolprobe" "$evidence_dir/protocolprobe"
cp "$scratch/out/probe" "$evidence_dir/probe"
cp "$scratch/out/controlcheck" "$evidence_dir/controlcheck"

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
# Retain the original measurement path above. Independently exercise sealed
# materialization/exec/cancellation with the exact original accepted child bytes.
# This is a local synthetic capability fixture, never owner release authority.
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user "$task_uid:$task_gid" --pids-limit 64 --memory 128m --cpus 2 \
  --tmpfs /tmp:rw,nosuid,nodev,size=16m \
  --mount "type=bind,src=$scratch/out/probe,dst=/probe,readonly" \
  --mount "type=bind,src=$evidence_dir/workflow,dst=/workflow,readonly" \
  --mount "type=bind,src=$scratch/results,dst=/out" \
  --entrypoint /probe bifrost-go-spike-runtime --artifact /workflow --output /out/sealed-execution.json \
  --sealed-artifact-sha256 160917deeb94275f31ca9ddee2dacae00fb079fdf54cb60e206c8def261f9c34
cp "$scratch/results/sealed-execution.json" "$evidence_dir/sealed-execution.json"
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user "$task_uid:$task_gid" --pids-limit 64 --memory 128m --cpus 2 \
  --tmpfs /tmp:rw,nosuid,nodev,size=16m \
  --mount "type=bind,src=$scratch/out/probe,dst=/probe,readonly" \
  --mount "type=bind,src=$evidence_dir/workflow-warm-edit,dst=/workflow,readonly" \
  --mount "type=bind,src=$scratch/results,dst=/out" \
  --entrypoint /probe bifrost-go-spike-runtime --artifact /workflow --output /out/warm-execution.json --descending
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

# Independent local supervisor uses the published contract with the actual
# compiled adapter and unchanged child. All Start/receipt decisions here are
# synthetic: this does not count as Rust admission, issuance or durability.
common_probe_status=0
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user "$task_uid:$task_gid" --pids-limit 64 --memory 128m --cpus 2 \
  --tmpfs /tmp:rw,nosuid,nodev,size=16m \
  --mount "type=bind,src=$scratch/out/protocolprobe,dst=/protocolprobe,readonly" \
  --mount "type=bind,src=$scratch/out/adapter,dst=/adapter,readonly" \
  --mount "type=bind,src=$evidence_dir/workflow,dst=/workflow,readonly" \
  --mount "type=bind,src=$evidence_dir/descriptor.json,dst=/fixtures/build-evidence.json,readonly" \
  --mount "type=bind,src=$evidence_dir/module-graph.txt,dst=/fixtures/module-graph.txt,readonly" \
  --mount "type=bind,src=$spike_root/schemas/input.json,dst=/fixtures/input-schema.json,readonly" \
  --mount "type=bind,src=$spike_root/schemas/output.json,dst=/fixtures/output-schema.json,readonly" \
  --mount "type=bind,src=$spike_root/executionprofile/testdata/structural-vectors.json,dst=/fixtures/vectors.json,readonly" \
  --mount "type=bind,src=$scratch/results,dst=/out" \
  --entrypoint /protocolprobe bifrost-go-spike-runtime \
  --adapter /adapter --workflow /workflow --evidence /fixtures/build-evidence.json \
  --graph /fixtures/module-graph.txt --input-schema /fixtures/input-schema.json \
  --output-schema /fixtures/output-schema.json --vectors /fixtures/vectors.json \
  --output /out/common-protocol-execution.json || common_probe_status=$?
if [ -f "$scratch/results/common-protocol-execution.json" ]; then
  cp "$scratch/results/common-protocol-execution.json" "$evidence_dir/common-protocol-execution.json"
fi
test "$common_probe_status" -eq 0

# Experimental attestation keys exist only in this trusted verifier's temporary
# directory, created after all source/test/runtime containers have exited. They
# are never mounted into a builder or workload and never authorize production.
openssl genpkey -algorithm ED25519 -out "$scratch/attestation.key" 2>/dev/null
openssl pkey -in "$scratch/attestation.key" -pubout -out "$evidence_dir/attestation-public.pem"
openssl pkeyutl -sign -rawin -inkey "$scratch/attestation.key" \
  -in "$evidence_dir/descriptor.json" -out "$evidence_dir/descriptor.sig"
openssl pkeyutl -verify -rawin -pubin -inkey "$evidence_dir/attestation-public.pem" \
  -in "$evidence_dir/descriptor.json" -sigfile "$evidence_dir/descriptor.sig" \
  > "$evidence_dir/attestation-verification.txt"

# Trusted build-plane packaging, after tenant containers and producer attestation.
# Never called by runtime admission. Disable Python bytecode so inputs stay fixed.
python3 -B -m unittest discover -s "$spike_root/scripts" -p 'test_native_bundle.py' \
  > "$evidence_dir/native-bundle-tests.txt" 2>&1
python3 -B "$spike_root/scripts/native_bundle.py" "$spike_root" "$evidence_dir" \
  > "$evidence_dir/native-bundle-assembly.json"
openssl pkeyutl -sign -rawin -inkey "$scratch/attestation.key" \
  -in "$evidence_dir/native-bundle-descriptor.json" -out "$evidence_dir/native-bundle-descriptor.sig"
openssl pkeyutl -verify -rawin -pubin -inkey "$evidence_dir/attestation-public.pem" \
  -in "$evidence_dir/native-bundle-descriptor.json" -sigfile "$evidence_dir/native-bundle-descriptor.sig" \
  > "$evidence_dir/native-bundle-attestation-verification.txt"
