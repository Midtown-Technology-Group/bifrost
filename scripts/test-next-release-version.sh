#!/usr/bin/env bash
# Offline Git/CLI seam checks: contract floors and discovery/error propagation.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture="$(mktemp -d "${TMPDIR:-/tmp}/bifrost-release-version-test.XXXXXX")"
trap 'rm -rf "$fixture"' EXIT
mkdir -p "$fixture/scripts/release" "$fixture/bin" "$fixture/api/shared" "$fixture/api/bifrost"
cp "$repo_root/scripts/next-version.py" "$fixture/scripts/next-version.py"
cp "$repo_root/scripts/release/next-release-version.sh" "$fixture/scripts/release/next-release-version.sh"
cat > "$fixture/bin/gh" <<'GH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${BIFROST_RELEASE_FIXTURE_GH_STATUS:-0}" != 0 ]]; then
  exit "$BIFROST_RELEASE_FIXTURE_GH_STATUS"
fi
cat "$BIFROST_RELEASE_FIXTURE_JSON"
GH
chmod +x "$fixture/bin/gh"
export PATH="$fixture/bin:$PATH"
export BIFROST_RELEASE_FIXTURE_JSON="$fixture/prs.json"
printf '%s\n' '[{"title":"feat: compatible feature","labels":["semver:minor"],"mergedAt":"2099-01-01T00:00:00Z"}]' > "$BIFROST_RELEASE_FIXTURE_JSON"
cd "$fixture"
git init -q
git config user.name 'Release fixture'
git config user.email 'release@example.invalid'
git config commit.gpgsign false
printf 'CONTRACT_VERSION: int = 11\n' > api/shared/contract_version.py
cp api/shared/contract_version.py api/bifrost/contract_version.py
git add .
git commit -qm 'base contract'
git tag v1.0.0

check() {
  local name="$1" expected_output="$2" expected_status="$3"
  shift 3
  local output status
  if output="$(bash scripts/release/next-release-version.sh "$@" 2> "$fixture/error.txt")"; then
    status=0
  else
    status=$?
  fi
  if [[ "$output" != "$expected_output" || "$status" != "$expected_status" ]]; then
    printf 'FAIL: %s (output=%s status=%s)\n' "$name" "$output" "$status" >&2
    exit 1
  fi
  printf 'PASS: %s\n' "$name"
}
check 'unchanged contract permits minor' v1.1.0 0
printf 'CONTRACT_VERSION: int = 13\n' > api/shared/contract_version.py
cp api/shared/contract_version.py api/bifrost/contract_version.py
git add api
git commit -qm 'breaking contract'
check 'contract change enforces major despite minor label' v2.0.0 0
check 'malformed version decision stays nonzero' '' 2 --base invalid
printf '[]\n' > "$BIFROST_RELEASE_FIXTURE_JSON"
check 'empty release interval retains no-release status' '' 3
export BIFROST_RELEASE_FIXTURE_GH_STATUS=1
check 'failed discovery cannot become success' '' 1
unset BIFROST_RELEASE_FIXTURE_GH_STATUS
printf '%s\n' '[{"title":"fix: compatible fix","labels":[],"mergedAt":"2099-01-01T00:00:00Z"}]' > "$BIFROST_RELEASE_FIXTURE_JSON"
printf 'CONTRACT_VERSION: int = 12\n' > api/bifrost/contract_version.py
git add api
git commit -qm 'mismatched contracts'
check 'server and CLI mismatch refuses release' '' 2
rm api/bifrost/contract_version.py
git add api
git commit -qm 'missing contract'
check 'missing contract refuses release' '' 2
