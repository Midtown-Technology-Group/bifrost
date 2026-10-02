#!/usr/bin/env bash
# Trusted synthetic contract checks, only in supported CI / disposable test VM.
set -euo pipefail
spike_root=$(cd "$(dirname "$0")/.." && pwd)
reference=$(cd "$1" && pwd)
evidence_dir=$(cd "$2" && pwd)
test "$(id -u)" != 0
test "$(git -C "$reference" rev-parse HEAD)" = 9f35278f90ba3757318ac9f07895bda9957330de
vectors=$reference/core-rs/crates/bifrost-contracts/tests/fixtures/runtime/v1/control-vectors.json
cmp "$vectors" "$spike_root/runtimecontrol/testdata/control-vectors.json"
cmp "$reference/contracts/runtime/v1/control.schema.json" "$spike_root/runtimecontrol/testdata/control.schema.json"
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
mkdir -p "$scratch/cargo" "$scratch/target"
task_uid=$(id -u)
task_gid=$(id -g)
image=rust:1.98.1-slim-bookworm@sha256:ff521445a372125ed4f76e1453a1f8098f2d05332d1601d30db1c1f62757e730
rust() {
  local network=$1
  shift
  docker run --rm --network "$network" --read-only --cap-drop ALL --security-opt no-new-privileges \
    --user "$task_uid:$task_gid" --pids-limit 256 --memory 3g --cpus 2 \
    --tmpfs /tmp:rw,exec,nosuid,nodev,size=1g \
    --mount "type=bind,src=$reference/core-rs,dst=/src,readonly" \
    --mount "type=bind,src=$scratch/cargo,dst=/cargo" \
    --mount "type=bind,src=$scratch/target,dst=/target" \
    --mount "type=bind,src=$evidence_dir,dst=/evidence" \
    --workdir /src "$image" env -i PATH=/usr/local/cargo/bin:/usr/bin:/bin \
    HOME=/tmp RUSTUP_HOME=/usr/local/rustup RUSTUP_TOOLCHAIN=1.98.1-x86_64-unknown-linux-gnu \
    CARGO_HOME=/cargo CARGO_TARGET_DIR=/target "$@"
}
# First-party crate dependencies only. No Go application or authority credentials.
rust none rustc --version > "$evidence_dir/rust-toolchain.txt"
rust bridge cargo fetch --locked > "$evidence_dir/rust-fetch.txt" 2>&1
rust none cargo test --offline --locked -p bifrost-contracts > "$evidence_dir/rust-contract-tests.txt" 2>&1
rust none cargo run --offline --locked -p bifrost-contracts --example runtime_control_vectors -- emit > "$evidence_dir/rust-control-exchange.json" 2> "$evidence_dir/rust-exchange-build.txt"
export PYTHONPATH=$reference/api
export BIFROST_RUNTIME_VECTORS=$vectors
python3 "$reference/api/tests/runtime_protocol/interchange.py" emit > "$evidence_dir/python-control-exchange.json"
go_check() {
  docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
    --user "$task_uid:$task_gid" --pids-limit 64 --memory 128m --cpus 2 \
    --mount "type=bind,src=$evidence_dir/controlcheck,dst=/controlcheck,readonly" \
    --mount "type=bind,src=$vectors,dst=/vectors.json,readonly" \
    --mount "type=bind,src=$evidence_dir,dst=/evidence,readonly" \
    --entrypoint /controlcheck bifrost-go-spike-runtime /vectors.json "$@"
}
go_check emit > "$evidence_dir/go-control-exchange.json"
go_check validate /evidence/python-control-exchange.json > "$evidence_dir/go-validates-python.txt"
go_check validate /evidence/rust-control-exchange.json > "$evidence_dir/go-validates-rust.txt"
python3 "$reference/api/tests/runtime_protocol/interchange.py" validate "$evidence_dir/go-control-exchange.json" > "$evidence_dir/python-validates-go.txt"
rust none cargo run --offline --locked -p bifrost-contracts --example runtime_control_vectors -- validate /evidence/go-control-exchange.json > "$evidence_dir/rust-validates-go.txt" 2>&1
git -C "$reference" rev-parse HEAD > "$evidence_dir/control-reference-sha.txt"
