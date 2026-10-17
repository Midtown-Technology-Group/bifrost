#!/usr/bin/env bash
# Ephemeral TLS trust belongs only to the two isolated test processes.
set -euo pipefail
image=${1:?usage: telemetry-tls.sh TOOLCHAIN_IMAGE}
fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' EXIT
chmod 755 "$fixture"
for chain in trusted untrusted; do
  openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 1 \
    -subj "/CN=BiFrost disposable $chain test CA" \
    -keyout "$fixture/$chain-ca.key" -out "$fixture/$chain-ca.pem" >/dev/null 2>&1
  openssl req -new -newkey rsa:2048 -nodes -sha256 -subj /CN=localhost \
    -keyout "$fixture/$chain-server.key" -out "$fixture/$chain-server.csr" >/dev/null 2>&1
  cat > "$fixture/$chain.ext" <<'EOF'
subjectAltName=DNS:localhost
extendedKeyUsage=serverAuth
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
EOF
  openssl x509 -req -sha256 -days 1 -in "$fixture/$chain-server.csr" \
    -CA "$fixture/$chain-ca.pem" -CAkey "$fixture/$chain-ca.key" -CAcreateserial \
    -extfile "$fixture/$chain.ext" -out "$fixture/$chain-server.pem" >/dev/null 2>&1
done
# Remove CA signing keys before the fixtures enter any test container.
rm "$fixture/trusted-ca.key" "$fixture/untrusted-ca.key"
for chain in trusted untrusted; do
  docker run --rm -v "$fixture:/tls:ro" \
    -e SSL_CERT_FILE=/tls/trusted-ca.pem -e SSL_CERT_DIR=/tls/empty \
    -e BIFROST_RUST_TLS_CERT="/tls/$chain-server.pem" \
    -e BIFROST_RUST_TLS_KEY="/tls/$chain-server.key" \
    -e BIFROST_RUST_TLS_EXPECT="$chain" \
    "$image" cargo test --locked -p bifrost-core --features telemetry-tls --test telemetry_tls
done
