#!/usr/bin/env bash
set -euo pipefail

# Native request smoke for the production nginx template. This intentionally
# runs the same envsubst and nginx image used by the client production image.
# It requires Docker, curl, and Python 3 on a Linux host.

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work_dir="$(mktemp -d)"
nginx_name="bifrost-client-nginx-smoke-$$"
runtime_image="$(sed -n 's/^FROM \(nginx:alpine@sha256:[^ ]*\) AS production$/\1/p' "$repo_root/Dockerfile")"
host_gateway="$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}')"
backend_pid=""
renderer_pid=""

if [[ -z "$runtime_image" || -z "$host_gateway" ]]; then
  printf 'FAIL: could not derive the pinned nginx runtime image from Dockerfile\n' >&2
  exit 1
fi

for port in 18000 18001 18081; do
  if ss -ltn "sport = :$port" | tail -n +2 | grep -q .; then
    printf 'FAIL: required smoke port %s is already in use\n' "$port" >&2
    exit 1
  fi
done

cleanup() {
  local status=$?
  docker rm -f "$nginx_name" >/dev/null 2>&1 || true
  [[ -z "$backend_pid" ]] || kill "$backend_pid" >/dev/null 2>&1 || true
  [[ -z "$renderer_pid" ]] || kill "$renderer_pid" >/dev/null 2>&1 || true
  rm -rf "$work_dir"
  exit "$status"
}
trap cleanup EXIT

cat >"$work_dir/stub.py" <<'PY'
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

parser = argparse.ArgumentParser()
parser.add_argument("role", choices=("backend", "renderer"))
parser.add_argument("port", type=int)
args = parser.parse_args()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if args.role == "renderer":
            body = f"renderer:{self.path}"
        elif self.path.startswith("/auth/oauth/config"):
            body = (
                f"backend:{self.path};"
                f"xfp={self.headers.get('X-Forwarded-Proto')};"
                f"xfh={self.headers.get('X-Forwarded-Host')}"
            )
        else:
            body = f"backend:{self.path}"
        payload = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()
PY

mkdir -p "$work_dir/html"
printf '<!doctype html><html><body>spa</body></html>\n' >"$work_dir/html/index.html"

python3 "$work_dir/stub.py" backend 18000 >"$work_dir/backend.log" 2>&1 &
backend_pid=$!
python3 "$work_dir/stub.py" renderer 18001 >"$work_dir/renderer.log" 2>&1 &
renderer_pid=$!

for _ in $(seq 1 30); do
  if curl --silent --fail http://127.0.0.1:18000/health >/dev/null; then
    break
  fi
  sleep 0.1
done

assert_contains() {
  local actual=$1 expected=$2 message=$3
  if [[ "$actual" != *"$expected"* ]]; then
    printf 'FAIL: %s\nactual: %s\nexpected substring: %s\n' "$message" "$actual" "$expected" >&2
    exit 1
  fi
}

for scheme in http https; do
  docker run --rm -d --name "$nginx_name" \
    -p 127.0.0.1:18081:80 \
    -e BIFROST_API_UPSTREAM="$host_gateway:18000" \
    -e BIFROST_RENDERER_UPSTREAM="$host_gateway:18001" \
    -e BIFROST_RENDERER_RESOLVER=127.0.0.11 \
    -e BIFROST_CANONICAL_SCHEME="$scheme" \
    -e 'NGINX_ENVSUBST_FILTER=^(BIFROST_API_UPSTREAM|BIFROST_RENDERER_UPSTREAM|BIFROST_RENDERER_RESOLVER|BIFROST_CANONICAL_SCHEME)$' \
    -v "$repo_root/nginx.conf:/etc/nginx/templates/default.conf.template:ro" \
    -v "$work_dir/html:/usr/share/nginx/html:ro" \
    "$runtime_image" >/dev/null
  # The official image entrypoint rendered the template and retained native
  # nginx variables/captures while applying the Dockerfile's filter contract.
  docker exec "$nginx_name" grep -Fq 'proxy_set_header Host $host;' /etc/nginx/conf.d/default.conf
  docker exec "$nginx_name" grep -Fq 'try_files $uri $uri/ /index.html;' /etc/nginx/conf.d/default.conf
  docker exec "$nginx_name" grep -Fq "proxy_pass http://${host_gateway}:18001/\$3\$is_args\$args;" /etc/nginx/conf.d/default.conf
  for _ in $(seq 1 30); do
    if curl --silent --fail http://127.0.0.1:18081/health >/dev/null; then
      break
    fi
    sleep 0.1
  done

  health="$(curl --silent --fail http://127.0.0.1:18081/health)"
  assert_contains "$health" 'backend:/health' "$scheme health reaches API"
  if [[ "$health" == *'<html>'* || "$health" == *'spa'* ]]; then
    printf 'FAIL: %s health fell through to SPA\n' "$scheme" >&2
    exit 1
  fi
  assert_contains "$(curl --silent --fail http://127.0.0.1:18081/docs)" 'backend:/docs' "$scheme docs reaches API"
  assert_contains "$(curl --silent --fail http://127.0.0.1:18081/redoc)" 'backend:/redoc' "$scheme redoc reaches API"
  assert_contains "$(curl --silent --fail http://127.0.0.1:18081/openapi.json)" 'backend:/openapi.json' "$scheme OpenAPI reaches API"
  assert_contains "$(curl --silent --fail http://127.0.0.1:18081/auth/callback/provider)" 'spa' "$scheme OAuth callback remains SPA"
  callback="$(curl --silent --fail http://127.0.0.1:18081/api/auth/oauth/config -H 'X-Forwarded-Proto: http' -H 'X-Forwarded-Host: attacker.invalid')"
  assert_contains "$callback" "xfp=$scheme" "$scheme callback scheme is canonical"
  assert_contains "$callback" 'xfh=127.0.0.1' "$scheme callback host is forwarded"
  assert_contains "$(curl --silent --fail 'http://127.0.0.1:18081/renderer/render/markdown-pdf?attempt=1')" 'renderer:/render/markdown-pdf?attempt=1' "$scheme renderer rewrite and query"
  assert_contains "$(curl --silent --fail 'http://127.0.0.1:18081/internal/doc-renderer/render/html-pdf?attempt=2')" 'renderer:/render/html-pdf?attempt=2' "$scheme internal renderer rewrite and query"

  docker rm -f "$nginx_name" >/dev/null
done

printf 'nginx App Service parity smoke passed (http and https canonical schemes).\n'
