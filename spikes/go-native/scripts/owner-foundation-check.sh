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
mkdir -p "$scratch/context" "$scratch/cargo" "$scratch/target" "$scratch/source"
cp -R "$spike_root/owner-rs/." "$scratch/source/"
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
    --mount "type=bind,src=$scratch/source,dst=/src" \
    --mount "type=bind,src=$scratch/cargo,dst=/cargo" \
    --mount "type=bind,src=$scratch/target,dst=/target" \
    --workdir /src "$image" env -i PATH=/usr/local/cargo/bin:/usr/bin:/bin \
    HOME=/tmp RUSTUP_HOME=/usr/local/rustup RUSTUP_TOOLCHAIN=1.98.1-x86_64-unknown-linux-gnu \
    CARGO_HOME=/cargo CARGO_TARGET_DIR=/target "$@"
}
rust none rustc --version > "$evidence_dir/owner-toolchain.txt"
if [[ ! -f "$scratch/source/Cargo.lock" ]]; then
  rust bridge cargo generate-lockfile > "$evidence_dir/owner-lock-bootstrap.txt" 2>&1
fi
cp "$scratch/source/Cargo.lock" "$evidence_dir/owner-Cargo.lock"
rust bridge cargo fetch --locked > "$evidence_dir/owner-fetch.txt" 2>&1
rust none cargo fmt --all -- --check > "$evidence_dir/owner-fmt.txt" 2>&1
rust none cargo clippy --locked --offline --all-targets -- -D warnings > "$evidence_dir/owner-clippy.txt" 2>&1
rust none cargo test --locked --offline > "$evidence_dir/owner-tests.txt" 2>&1
rust none cargo metadata --locked --offline --format-version 1 > "$evidence_dir/owner-dependency-graph.json"
# Dependency bootstrap may create a lockfile, never modify source/declarations.
cmp "$spike_root/owner-rs/Cargo.toml" "$scratch/source/Cargo.toml"
cmp "$spike_root/owner-rs/src/lib.rs" "$scratch/source/src/lib.rs"
sha256sum "$spike_root/owner-rs/Cargo.toml" "$spike_root/owner-rs/src/lib.rs" \
  "$evidence_dir/owner-Cargo.lock" > "$evidence_dir/owner-source-hashes.txt"
printf '%s\n' 'Rust owner observation foundation only: locked offline checks; no PostgreSQL transaction, lifecycle writer, workload launch or runtime acceptance proved.' \
  > "$evidence_dir/owner-scope.txt"
