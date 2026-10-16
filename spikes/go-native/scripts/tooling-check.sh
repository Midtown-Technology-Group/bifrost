#!/usr/bin/env bash
# Build/developer tools only, in the disposable hosted Linux Docker lane.
set -euo pipefail
spike_root=$(cd "$(dirname "$0")/.." && pwd)
evidence_dir=${1:?absolute evidence directory required}
case "$evidence_dir" in /*) ;; *) exit 2;; esac
mkdir -p "$evidence_dir"
task_uid=$(id -u)
task_gid=$(id -g)
if [[ "$task_uid" -eq 0 ]]; then
  echo 'Use the nonroot hosted tooling lane.' >&2
  exit 2
fi
toolchain_image=golang:1.27.2-bookworm@sha256:5cf287a799e6b94384bad13d16b14904c531f51ba65792237e122ce42b392f61
scratch=$(mktemp -d)
cleanup() {
  original=$?
  trap - EXIT
  chmod -R u+w "$scratch"
  rm -rf "$scratch"
  exit "$original"
}
trap cleanup EXIT
mkdir -p "$scratch/modules" "$scratch/fixture" "$scratch/results"
cat > "$scratch/fixture/input.go" <<'GO'
package workflow
type Input struct { Name string `json:"name"` }
GO
cat > "$scratch/fixture/output.go" <<'GO'
package workflow
type Output struct { Ready bool `json:"ready"` }
GO
cat > "$scratch/fixture/workflow.go" <<'GO'
package workflow
func init() { panic("metadata discovery must never execute workload initialization") }
GO

git -C "$spike_root" rev-parse HEAD > "$evidence_dir/source.txt"
printf '%s\n' "$toolchain_image" > "$evidence_dir/toolchain-image.txt"
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user "$task_uid:$task_gid" --pids-limit 256 --memory 2g --cpus 2 \
  --tmpfs /tmp:rw,nosuid,nodev,size=512m \
  --mount "type=bind,src=$spike_root,dst=/src,readonly" \
  --mount "type=bind,src=$scratch/modules,dst=/modules" --workdir /src \
  "$toolchain_image" env -i PATH=/usr/local/go/bin:/usr/bin:/bin HOME=/tmp \
  GOTOOLCHAIN=local GOWORK=off CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
  GOMODCACHE=/modules GOCACHE=/tmp/cache GOPROXY=https://proxy.golang.org \
  GOSUMDB=sum.golang.org GONOPROXY=none GONOSUMDB=none \
  sh -ec 'go mod download; go mod verify' > "$evidence_dir/modules.txt"

docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user "$task_uid:$task_gid" --pids-limit 256 --memory 3g --cpus 2 \
  --tmpfs /tmp:rw,exec,nosuid,nodev,size=1g \
  --mount "type=bind,src=$spike_root,dst=/src,readonly" \
  --mount "type=bind,src=$scratch/modules,dst=/modules,readonly" \
  --mount "type=bind,src=$scratch/fixture,dst=/fixture,readonly" \
  --mount "type=bind,src=$scratch/results,dst=/results" --workdir /src \
  "$toolchain_image" env -i PATH=/usr/local/go/bin:/usr/bin:/bin HOME=/tmp \
  GOTOOLCHAIN=local GOWORK=off CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
  GOMODCACHE=/modules GOCACHE=/tmp/cache GOPROXY=off GOSUMDB=off GOFLAGS=-mod=readonly \
  sh -ec 'test -z "$(gofmt -l .)"; go vet ./...; go test -count=1 -json ./...;
    go build -trimpath -buildvcs=false -o /tmp/schema ./cmd/schema;
    /tmp/schema /fixture Input > /results/input.json;
    /tmp/schema /fixture/output.go Output > /results/output.json' \
  > "$evidence_dir/go-tests.jsonl"
cp "$scratch/results/input.json" "$scratch/results/output.json" "$evidence_dir/"
python3 -B -m unittest discover -s "$spike_root/scripts" -p test_native_bundle.py \
  > "$evidence_dir/bundle-tests.txt" 2>&1
python3 - "$evidence_dir" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
for name, field, kind in (("input", "name", "string"), ("output", "ready", "boolean")):
    value = json.loads((root / (name + ".json")).read_text())
    assert value["properties"] == {field: {"type": kind}}
    assert value["required"] == [field]
    assert value["additionalProperties"] is False
PY
