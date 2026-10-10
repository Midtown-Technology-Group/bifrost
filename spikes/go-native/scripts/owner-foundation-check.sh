#!/usr/bin/env bash
# First-party dependency bootstrap only; hosted disposable runner/test VM.
set -euo pipefail
spike_root=$(cd "$(dirname "$0")/.." && pwd)
evidence_dir=$(cd "$1" && pwd)
test "$(id -u)" != 0
scratch=$(mktemp -d)
image=''
cleanup() {
  original=$?
  trap - EXIT
  if [[ -n "$image" ]]; then
    docker image rm "$image" > "$evidence_dir/owner-image-cleanup.txt" 2>&1 || true
  fi
  rm -rf "$scratch"
  exit "$original"
}
trap cleanup EXIT
mkdir -p "$scratch/context" "$scratch/cargo" "$scratch/target" \
  "$scratch/source/owner-rs" "$scratch/source/peers/rust-execution" \
  "$scratch/source/executionprofile"
cp -R "$spike_root/owner-rs/." "$scratch/source/owner-rs/"
cp "$spike_root/peers/rust-execution/Cargo.toml" "$scratch/source/peers/rust-execution/"
cp -R "$spike_root/peers/rust-execution/src" "$scratch/source/peers/rust-execution/"
cp -R "$spike_root/executionprofile/schemas" "$scratch/source/executionprofile/"
docker build --iidfile "$scratch/image-id" -f "$spike_root/peers/rust-execution/Dockerfile" \
  "$scratch/context" > "$evidence_dir/owner-tools-build.txt" 2>&1
image=$(cat "$scratch/image-id")
printf '%s\n' "$image" > "$evidence_dir/owner-tools-image.txt"
task_uid=$(id -u)
task_gid=$(id -g)
rust() {
  local network=$1
  shift
  docker run --rm --network "$network" --read-only --cap-drop ALL --security-opt no-new-privileges \
    --user "$task_uid:$task_gid" --pids-limit 256 --memory 3g --cpus 2 \
    --tmpfs /tmp:rw,exec,nosuid,nodev,size=1g \
    --mount "type=bind,src=$scratch/source,dst=/spike" \
    --mount "type=bind,src=$scratch/cargo,dst=/cargo" \
    --mount "type=bind,src=$scratch/target,dst=/target" \
    --workdir /spike/owner-rs "$image" env -i PATH=/usr/local/cargo/bin:/usr/bin:/bin \
    HOME=/tmp RUSTUP_HOME=/usr/local/rustup RUSTUP_TOOLCHAIN=1.98.1-x86_64-unknown-linux-gnu \
    CARGO_HOME=/cargo CARGO_TARGET_DIR=/target "$@"
}
rust none rustc --version > "$evidence_dir/owner-toolchain.txt"
test -f "$scratch/source/owner-rs/Cargo.lock"
cp "$scratch/source/owner-rs/Cargo.lock" "$evidence_dir/owner-Cargo.lock"
rust bridge cargo fetch --locked > "$evidence_dir/owner-fetch.txt" 2>&1
rust none cargo fmt --all -- --check > "$evidence_dir/owner-fmt.txt" 2>&1
rust none cargo clippy --locked --offline --all-targets -- -D warnings > "$evidence_dir/owner-clippy.txt" 2>&1
rust none cargo test --locked --offline --all-targets > "$evidence_dir/owner-tests.txt" 2>&1
rust none cargo build --locked --offline --examples > "$evidence_dir/owner-build.txt" 2>&1
cp "$scratch/target/debug/examples/session_observation" "$evidence_dir/runtime-owner-observation"
cp "$scratch/target/debug/examples/result_report" "$evidence_dir/runtime-owner-result"
cp "$scratch/target/debug/examples/release_observation" "$evidence_dir/runtime-owner-release"
cp "$scratch/target/debug/examples/start_commit" "$evidence_dir/runtime-owner-start"
cp "$scratch/target/debug/examples/sdk_admission" "$evidence_dir/runtime-owner-sdk-admission"
cp "$scratch/target/debug/examples/provision_commit" "$evidence_dir/runtime-owner-provision"
cp "$scratch/target/debug/examples/admit_commit" "$evidence_dir/runtime-owner-admit"
rust none cargo metadata --locked --offline --format-version 1 > "$evidence_dir/owner-dependency-graph.json"
# Dependency bootstrap may create a lockfile, never modify source/declarations.
for input in owner-rs/Cargo.toml owner-rs/Cargo.lock owner-rs/src/*.rs \
  owner-rs/examples/*.rs peers/rust-execution/Cargo.toml peers/rust-execution/src/*.rs \
  executionprofile/schemas/*.json; do
  # Expand relative inputs from the trusted spike root, never application metadata.
  for source in "$spike_root"/$input; do
    relative=${source#"$spike_root"/}
    cmp "$source" "$scratch/source/$relative"
    sha256sum "$source" >> "$evidence_dir/owner-source-hashes.txt"
  done
done
printf '%s\n' 'Rust owner observation foundation only: locked offline checks; no PostgreSQL transaction, lifecycle writer, workload launch or runtime acceptance proved.' \
  > "$evidence_dir/owner-scope.txt"
cp "$scratch/target/debug/examples/bundle_verify" "$evidence_dir/runtime-native-bundle-verify"
