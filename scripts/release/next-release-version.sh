#!/usr/bin/env bash
# Print the next MTG Bifrost release version (vMAJOR.MINOR.PATCH) from the
# semver labels / conventional-commit types of pull requests merged since the
# last stable tag. Exits 2 when there is nothing to release.
#
# Usage: scripts/release/next-release-version.sh [--base X.Y.Z] [--default patch|minor|major]
#
# The decision itself lives in scripts/next-version.py so it can be unit
# tested; this wrapper only discovers the base tag and the merged PRs.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
decide="${repo_root}/scripts/next-version.py"

base=""
default="patch"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --base) base="${2:?--base needs a value}"; shift 2 ;;
    --default) default="${2:?--default needs a value}"; shift 2 ;;
    *) echo "usage: $0 [--base X.Y.Z] [--default patch|minor|major]" >&2; exit 2 ;;
  esac
done

last_tag="$(git describe --tags --abbrev=0 \
  --match 'v[0-9]*.[0-9]*.[0-9]*' --exclude 'v*-*' 2>/dev/null || true)"

if [[ -n "$base" ]]; then
  :
elif [[ -n "$last_tag" ]]; then
  base="${last_tag#v}"
fi

if [[ -n "$last_tag" ]]; then
  since="$(git log -1 --format=%cI "$last_tag" 2>/dev/null || true)"
else
  since="1970-01-01T00:00:00Z"
fi
since_date="${since%%T*}"

pr_json="$(gh pr list --state merged --base main --limit 200 \
  --search "merged:>=$since_date" \
  --json title,labels \
  --jq '[.[] | {title: .title, labels: [.labels[].name]}]')"

if [[ -z "$base" ]]; then
  # No stable tag yet: the first MTG release is exactly v1.0.0 (VERSIONING.md).
  if [[ "$pr_json" == "[]" || -z "$pr_json" ]]; then
    echo "next-release-version: no stable tag and no merged pull requests" >&2
    exit 2
  fi
  echo "v1.0.0"
  exit 0
fi

next="$(printf '%s' "$pr_json" | python3 "$decide" --base "$base" --default "$default")"
echo "v${next}"
