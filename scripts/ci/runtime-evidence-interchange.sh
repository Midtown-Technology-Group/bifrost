#!/usr/bin/env bash
# Synthetic exact-byte experiment only; no workload/source/custody authority.
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$TASK_ROOT"
EXCHANGE_DIR="$(mktemp -d)"
trap 'rm -rf "$EXCHANGE_DIR"' EXIT
chmod 755 "$EXCHANGE_DIR"
python_oracle() {
    docker run --rm --network none --user 1000:1000 --entrypoint python \
        -v "$TASK_ROOT/scripts/ci/runtime-evidence-oracle.py:/oracle.py:ro" \
        -v "$TASK_ROOT/contracts/runtime/v1/evidence/evidence-vectors.json:/evidence-vectors.json:ro" \
        -v "$EXCHANGE_DIR:/exchange:ro" \
        bifrost-test-api-dev:latest /oracle.py "$@"
}
docker build --target checks \
    --build-context runtime-evidence=contracts/runtime/v1/evidence \
    -t bifrost-runtime-evidence-checks -f core-rs/Dockerfile core-rs
git rev-parse HEAD
docker image inspect bifrost-test-api-dev:latest --format '{{.Id}} {{json .RepoDigests}}'
docker image inspect bifrost-runtime-evidence-checks --format '{{.Id}}'
docker run --rm --network none bifrost-runtime-evidence-checks \
    cargo run --offline --locked -p bifrost-contracts \
    --example runtime_evidence_vectors -- emit > "$EXCHANGE_DIR/fixtures.json"
chmod 644 "$EXCHANGE_DIR/fixtures.json"
python_oracle validate-fixtures /exchange/fixtures.json /evidence-vectors.json
python_oracle emit > "$EXCHANGE_DIR/input.ndjson"
chmod 644 "$EXCHANGE_DIR/input.ndjson"
docker run --rm -i --network none bifrost-runtime-evidence-checks \
    cargo run --offline --locked -p bifrost-contracts \
    --example runtime_evidence_vectors -- encode \
    < "$EXCHANGE_DIR/input.ndjson" > "$EXCHANGE_DIR/rust.ndjson"
chmod 644 "$EXCHANGE_DIR/rust.ndjson"
python_oracle validate /exchange/rust.ndjson

# A coherent changed UTF-8/hex pair must fail independently, not merely parse.
printf '%s\n' '{"id":"numeric-0","outcome":"encoded","utf8":"{\"workflow_value\":1.0}","hex":"7b22776f726b666c6f775f76616c7565223a312e307d"}' \
    > "$EXCHANGE_DIR/drift.ndjson"
chmod 644 "$EXCHANGE_DIR/drift.ndjson"
if python_oracle validate /exchange/drift.ndjson > "$EXCHANGE_DIR/drift.log" 2>&1; then
    echo 'Independent byte oracle failed to detect deliberately introduced drift.' >&2
    exit 1
fi
if ! grep -Fxq 'exact-byte drift: numeric-0 bits=0000000000000000' "$EXCHANGE_DIR/drift.log"; then
    echo 'Drift probe failed for an unexpected reason.' >&2
    exit 1
fi
echo 'Tests-only sampled evidence byte campaign and deliberate byte-drift rejection passed.'
