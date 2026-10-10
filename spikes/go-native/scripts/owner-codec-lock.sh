#!/usr/bin/env bash
# One isolated first-party dependency proposal; not a build/runtime admission.
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
    docker image rm "$image" > "$evidence_dir/owner-codec-image-cleanup.txt" 2>&1 || true
  fi
  rm -rf "$scratch"
  exit "$original"
}
trap cleanup EXIT
mkdir -p "$scratch/context" "$scratch/cargo" "$scratch/source/peers/rust-execution" \
  "$scratch/source/executionprofile"
cp -R "$spike_root/owner-rs" "$scratch/source/owner-rs"
cp "$spike_root/peers/rust-execution/Cargo.toml" "$scratch/source/peers/rust-execution/"
cp -R "$spike_root/peers/rust-execution/src" "$scratch/source/peers/rust-execution/"
cp -R "$spike_root/executionprofile/schemas" "$scratch/source/executionprofile/"
# Modify only the isolated proposal, retain it for review before adoption. Exact
# existing codec pins and existing owner dependencies are preserved.
python3 - "$scratch/source/owner-rs/Cargo.toml" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
source = path.read_text()
assert source.count('[dependencies]\n') == 1
assert 'bifrost-execution-wire-spike' not in source
path.write_text(source.replace('[dependencies]\n', '[dependencies]\n'
    'bifrost-execution-wire-spike = { path = "../peers/rust-execution" }\n'
    'serde_json = "=1.0.151"\n'))
PY
cp "$scratch/source/owner-rs/Cargo.toml" "$evidence_dir/owner-codec-Cargo.toml"
docker build --iidfile "$scratch/image-id" -f "$spike_root/peers/rust-execution/Dockerfile" \
  "$scratch/context" > "$evidence_dir/owner-codec-tools-build.txt" 2>&1
image=$(cat "$scratch/image-id")
task_uid=$(id -u)
task_gid=$(id -g)
docker run --rm --network bridge --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user "$task_uid:$task_gid" --pids-limit 256 --memory 3g --cpus 2 \
  --tmpfs /tmp:rw,exec,nosuid,nodev,size=1g \
  --mount "type=bind,src=$scratch/source,dst=/spike" \
  --mount "type=bind,src=$scratch/cargo,dst=/cargo" \
  --workdir /spike/owner-rs "$image" env -i PATH=/usr/local/cargo/bin:/usr/bin:/bin \
  HOME=/tmp RUSTUP_HOME=/usr/local/rustup RUSTUP_TOOLCHAIN=1.98.1-x86_64-unknown-linux-gnu \
  CARGO_HOME=/cargo cargo update --package serde_json --precise 1.0.151 \
  > "$evidence_dir/owner-codec-resolution.txt" 2>&1
cp "$scratch/source/owner-rs/Cargo.lock" "$evidence_dir/owner-codec-Cargo.lock"
cmp "$evidence_dir/owner-codec-Cargo.toml" "$scratch/source/owner-rs/Cargo.toml"
cmp "$spike_root/owner-rs/src/lib.rs" "$scratch/source/owner-rs/src/lib.rs"
cmp "$spike_root/owner-rs/examples/session_observation.rs" \
  "$scratch/source/owner-rs/examples/session_observation.rs"
sha256sum "$evidence_dir/owner-codec-Cargo.toml" "$evidence_dir/owner-codec-Cargo.lock" \
  "$spike_root/scripts/owner-codec-lock.sh" "$spike_root/peers/rust-execution/Cargo.toml" \
  "$spike_root/peers/rust-execution/src/"*.rs "$spike_root/executionprofile/schemas/"*.json \
  > "$evidence_dir/owner-codec-input-hashes.txt"
printf '%s\n' 'Dependency resolution only. No application code/build scripts executed; no native runtime admission, protocol freeze or accepted writer. Proposed manifest/lock require review and new locked offline build evidence before adoption.' \
  > "$evidence_dir/owner-codec-scope.txt"
