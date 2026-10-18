#!/usr/bin/env bash
# TEMPORARY RBAC merge diagnostic. Remove before the final PR/pre-PR candidate.
# Generates reviewable inputs only; this does not satisfy any acceptance gate.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mode="${1:-all}"

regenerate_python_lock() {
    local python_image
    python_image="$(awk '$1 == "FROM" && $2 ~ /^python(:|@)/ { print $2; exit }' "$repo_root/api/Dockerfile")"
    if [[ ! "$python_image" =~ ^python(:3\.14-slim)?@sha256:[a-f0-9]{64}$ ]]; then
        echo "API Dockerfile must identify a digest-pinned Python 3.14 image." >&2
        return 1
    fi

    # No host Python, credential mounts or token environment are passed through.
    docker run --rm --volume "$repo_root:/repo" --workdir /repo "$python_image" sh -eu -c '
        python -c "import sys; assert sys.version_info[:2] == (3, 14), sys.version"
        pip install --quiet --require-hashes -r requirements-piptools.lock
        pip-compile --generate-hashes --output-file=requirements.lock pyproject.toml
    '
}

regenerate_client_lock() (
    node -e 'if (Number(process.versions.node.split(".")[0]) !== 26) throw new Error("This diagnostic requires Node 26")'
    # The inherited lock pins Tiptap's old exact intra-family dependencies.
    # Resolve the complete candidate manifest in a fresh directory so npm can
    # select one compatible graph, with normal peer validation and all existing
    # fork dependencies/overrides intact. Never modify the source lock on failure.
    temporary_parent="$(realpath "${RUNNER_TEMP:-${TMPDIR:-/tmp}}")"
    lock_workspace="$(mktemp -d "$temporary_parent/bifrost-rbac-npm.XXXXXXXX")"
    case "$lock_workspace" in
        "$temporary_parent"/bifrost-rbac-npm.*) ;;
        *) echo "Unexpected npm regeneration directory" >&2; exit 1 ;;
    esac
    trap 'rm -rf -- "$lock_workspace"' EXIT
    cp "$repo_root/client/package.json" "$lock_workspace/package.json"
    (cd "$lock_workspace" && npm install --package-lock-only --ignore-scripts --no-audit --no-fund)
    node - "$repo_root/client/package.json" "$lock_workspace/package-lock.json" <<'NODE'
const assert = require('node:assert/strict');
const fs = require('node:fs');
const manifest = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const lock = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
assert.equal(lock.lockfileVersion, 3);
for (const field of ['dependencies', 'devDependencies', 'optionalDependencies', 'engines']) {
  assert.deepEqual(lock.packages[''][field], manifest[field], `Lock root drift: ${field}`);
}
const selected = {};
for (const name of [...Object.keys(manifest.dependencies).filter(name => name.startsWith('@tiptap/')), '@tiptap/core', '@tiptap/pm']) {
  const entry = lock.packages[`node_modules/${name}`];
  assert.ok(entry?.version, `Missing Tiptap selection: ${name}`);
  selected[name] = entry.version;
}
console.log(`Fresh Tiptap selection: ${JSON.stringify(selected)}`);
NODE
    cp "$lock_workspace/package-lock.json" "$repo_root/client/package-lock.json"
)

install_client_lock() {
    node -e 'if (Number(process.versions.node.split(".")[0]) !== 26) throw new Error("This diagnostic requires Node 26")'
    (cd "$repo_root/client" && npm ci --no-audit --no-fund)
}

case "$mode" in
    python) regenerate_python_lock ;;
    client) regenerate_client_lock ;;
    client-install) install_client_lock ;;
    all)
        regenerate_python_lock
        regenerate_client_lock
        install_client_lock
        ;;
    *)
        echo "Usage: $0 [all|python|client|client-install]" >&2
        exit 2
        ;;
esac
