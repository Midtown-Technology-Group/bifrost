#!/usr/bin/env bash
# Synthetic codec proof only: no workload, credentials, SQL or custody claim.
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$TASK_ROOT"
EXCHANGE_DIR="$(mktemp -d)"
trap 'rm -rf "$EXCHANGE_DIR"' EXIT
# The uid-1000 Python container reads only synthetic fixtures and peer outputs.
chmod 755 "$EXCHANGE_DIR"
python_peer() {
    docker run --rm --network none --user 1000:1000 --entrypoint python \
        -v "$TASK_ROOT/api/src:/app/src:ro" \
        -v "$TASK_ROOT/api/tests/runtime_protocol:/app/tests/runtime_protocol:ro" \
        -v "$TASK_ROOT/api/pytest.ini:/app/pytest.ini:ro" \
        -v "$TASK_ROOT/core-rs/crates/bifrost-contracts/tests/fixtures/runtime/v1:/contracts/runtime/v1:ro" \
        -v "$EXCHANGE_DIR:/exchange:ro" \
        -e BIFROST_RUNTIME_VECTORS=/contracts/runtime/v1/control-vectors.json \
        bifrost-test-api-dev:latest "$@"
}
docker build --target checks -t bifrost-runtime-control-checks -f core-rs/Dockerfile core-rs
python_peer -m pytest --confcutdir=tests/runtime_protocol tests/runtime_protocol -q --no-cov
docker run --rm --network none bifrost-runtime-control-checks \
    cargo run --offline --locked -p bifrost-contracts --example runtime_control_vectors -- emit > "$EXCHANGE_DIR/rust.json"
python_peer -m tests.runtime_protocol.interchange validate /exchange/rust.json
python_peer -m tests.runtime_protocol.interchange emit > "$EXCHANGE_DIR/python.json"
docker run --rm --network none -v "$EXCHANGE_DIR:/exchange:ro" bifrost-runtime-control-checks \
    cargo run --offline --locked -p bifrost-contracts --example runtime_control_vectors -- validate /exchange/python.json
echo 'Partial runtime control profile: Python/Rust tests and both encoder-to-peer-decoder exchanges passed.'
