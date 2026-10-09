#!/usr/bin/env bash
# Trusted first-party codec checks only; supported hosted CI / dedicated test VM.
set -euo pipefail
spike_root=$(cd "$(dirname "$0")/.." && pwd)
evidence_dir=$(cd "$1" && pwd)
test "$(id -u)" != 0
scratch=$(mktemp -d)
image=''
cleanup() {
  if [[ -n "$image" ]]; then docker image rm "$image" > "$evidence_dir/rust-tools-cleanup.txt" 2>&1 || true; fi
  rm -rf "$scratch"
}
trap cleanup EXIT
mkdir -p "$scratch/context" "$scratch/cargo" "$scratch/target"
# No tenant source, secrets or general repository context enters image creation.
docker build --iidfile "$scratch/image-id" -f "$spike_root/peers/rust-execution/Dockerfile" \
  "$scratch/context" > "$evidence_dir/rust-tools-build.txt" 2>&1
image=$(cat "$scratch/image-id")
printf '%s\n' "$image" > "$evidence_dir/rust-tools-image.txt"
task_uid=$(id -u)
task_gid=$(id -g)
rust() {
  local network=$1
  shift
  docker run --rm --network "$network" --read-only --cap-drop ALL --security-opt no-new-privileges \
    --user "$task_uid:$task_gid" --pids-limit 256 --memory 3g --cpus 2 \
    --tmpfs /tmp:rw,exec,nosuid,nodev,size=1g \
    --mount "type=bind,src=$spike_root,dst=/src,readonly" \
    --mount "type=bind,src=$scratch/cargo,dst=/cargo" \
    --mount "type=bind,src=$scratch/target,dst=/target" \
    --mount "type=bind,src=$evidence_dir,dst=/evidence" \
    --workdir /src/peers/rust-execution "$image" env -i PATH=/usr/local/cargo/bin:/usr/bin:/bin \
    HOME=/tmp RUSTUP_HOME=/usr/local/rustup RUSTUP_TOOLCHAIN=1.98.1-x86_64-unknown-linux-gnu \
    CARGO_HOME=/cargo CARGO_TARGET_DIR=/target "$@"
}
rust none rustc --version > "$evidence_dir/rust-execution-toolchain.txt"
rust none rustup component list --installed > "$evidence_dir/rust-installed-components.txt"
rust bridge cargo fetch --locked > "$evidence_dir/rust-execution-fetch.txt" 2>&1
rust none cargo fmt --all -- --check > "$evidence_dir/rust-execution-fmt.txt" 2>&1
rust none cargo clippy --locked --offline --all-targets -- -D warnings > "$evidence_dir/rust-execution-clippy.txt" 2>&1
rust none cargo test --locked --offline > "$evidence_dir/rust-execution-tests.txt" 2>&1
rust none cargo build --locked --offline --bin bifrost-execution-wire-spike > "$evidence_dir/rust-execution-build.txt" 2>&1
cp "$scratch/target/debug/bifrost-execution-wire-spike" "$evidence_dir/rust-executioncheck"
cp "$spike_root/peers/rust-execution/Cargo.lock" "$evidence_dir/rust-execution-Cargo.lock"
cp "$spike_root/peers/rust-execution/reference-provenance.json" "$evidence_dir/rust-dependency-provenance.json"
