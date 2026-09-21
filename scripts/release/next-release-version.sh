#!/usr/bin/env bash
# Print the next MTG Bifrost release version (vMAJOR.MINOR.PATCH) from the
# semver labels / conventional-commit types of pull requests merged after the
# last stable tag.
#
# Exits 3 when there is nothing to release. Every other non-zero status is a
# real failure (gh, Python, bad arguments) and is propagated unchanged.
#
# Usage: scripts/release/next-release-version.sh [--base X.Y.Z] [--default patch|minor|major]
#
# The decision itself lives in scripts/next-version.py so it can be unit
# tested; this wrapper only discovers the base tag and the merged PRs.
set -euo pipefail

readonly NO_RELEASE_STATUS=3

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
  # Exact boundary: the tag's commit timestamp, not just its date, so PRs
  # merged earlier on the same day are not counted.
  tag_iso="$(git log -1 --format=%cI "$last_tag" 2>/dev/null || true)"
  tag_date="${tag_iso%%T*}"
else
  tag_iso=""
  tag_date="1970-01-01"
fi

# gh's search backend caps at 1000; a release window will not approach it, but
# refuse to decide on a truncated set rather than under-counting a bump.
if ! pr_json="$(gh pr list --state merged --base main --limit 1000 \
  --search "merged:>=$tag_date" \
  --json title,labels,mergedAt \
  --jq '[.[] | {title: .title, labels: [.labels[].name], mergedAt: .mergedAt}]')"; then
  echo "next-release-version: failed to list merged pull requests" >&2
  exit 1
fi

count="$(printf '%s' "$pr_json" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')"
if [[ "$count" -ge 1000 ]]; then
  echo "next-release-version: hit the 1000-PR search cap; refusing an incomplete decision" >&2
  exit 1
fi

if [[ -z "$base" ]]; then
  # No stable tag yet: the first MTG release is exactly v1.0.0 (VERSIONING.md).
  if [[ "$count" -eq 0 ]]; then
    echo "next-release-version: no stable tag and no merged pull requests" >&2
    exit "$NO_RELEASE_STATUS"
  fi
  echo "v1.0.0"
  exit 0
fi

if next="$(printf '%s' "$pr_json" | python3 "$decide" --base "$base" --default "$default" --since "$tag_iso")"; then
  echo "v${next}"
  exit 0
fi

status=$?
if [[ "$status" -eq "$NO_RELEASE_STATUS" ]]; then
  echo "next-release-version: nothing merged since ${last_tag:-the first tag}" >&2
fi
exit "$status"
