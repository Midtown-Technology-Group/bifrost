#!/usr/bin/env bash
# Compute a monotonic semver dev version for the current commit.
#
# Format: <next-patch>-dev.<monotonic-first-parent-sequence>
# Example: latest tag v1.0.0, HEAD is the third first-parent commit after it
#          -> 1.0.1-dev.3 (the release commit itself occupies position 0)
#
# First-parent position is unique and strictly increasing on the publication
# branch even when GitHub creates distinct commits in the same second. The
# --next-after mode predicts one squash/merge commit after an exact parent, so
# a reviewed candidate and its eventual main publication compute identically.
#
# MTG fork: uses tags on MTG-Thomas/bifrost only. Before the first stable
# release, bootstraps to 1.0.0-dev.<commit-count>.
# Fork-local prerelease tags such as `v0.9.1-mtg.1` are intentionally ignored.
set -euo pipefail

target_ref=HEAD
additional_commits=0
if [[ $# -gt 0 ]]; then
  if [[ $# -ne 2 || "$1" != "--next-after" ]]; then
    echo "usage: $0 [--next-after <git-commit>]" >&2
    exit 2
  fi
  target_ref="$2"
  additional_commits=1
fi

if ! git rev-parse --verify "${target_ref}^{commit}" >/dev/null 2>&1; then
  echo "compute-dev-version: '$target_ref' is not a commit" >&2
  exit 2
fi

# Planned first MTG semver release when no stable tag exists yet.
MTG_BOOTSTRAP_MAJOR=1
MTG_BOOTSTRAP_MINOR=0
MTG_BOOTSTRAP_PATCH=0

# v3.0.1 dev images were already published with all-parent counts in this same
# namespace. Reserve a higher range before switching to first-parent counting
# so the rollout cannot move backward or reuse an existing immutable tag. The
# offset naturally disappears when the next stable release tag starts a new
# dev cycle.
MTG_V3_DEV_SEQUENCE_TAG=v3.0.0
MTG_V3_DEV_SEQUENCE_OFFSET=1000

last_tag=$(git describe \
  --tags \
  --abbrev=0 \
  --match "v[0-9]*.[0-9]*.[0-9]*" \
  --exclude "v*-*" \
  "$target_ref" \
  2>/dev/null || true)

if [[ -z "$last_tag" ]]; then
  commits=$(git rev-list --count "$target_ref")
  commits=$((commits + additional_commits))
  printf '%s.%s.%s-dev.%s\n' \
    "$MTG_BOOTSTRAP_MAJOR" "$MTG_BOOTSTRAP_MINOR" "$MTG_BOOTSTRAP_PATCH" "$commits"
  exit 0
fi

if ! [[ "$last_tag" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
  echo "compute-dev-version: latest tag '$last_tag' is not vMAJOR.MINOR.PATCH" >&2
  exit 1
fi

major="${BASH_REMATCH[1]}"
minor="${BASH_REMATCH[2]}"
patch="${BASH_REMATCH[3]}"
next_patch=$((patch + 1))

# The release commit is position 0; each first-parent commit advances once.
# Candidate publication adds the one not-yet-created merge commit to the
# reviewed parent's position.
sequence=$(git rev-list --first-parent "${last_tag}..${target_ref}" --count)
sequence=$((sequence + additional_commits))
if [[ "$last_tag" == "$MTG_V3_DEV_SEQUENCE_TAG" ]]; then
  sequence=$((sequence + MTG_V3_DEV_SEQUENCE_OFFSET))
fi
if ! [[ "$sequence" =~ ^(0|[1-9][0-9]*)$ ]]; then
  echo "compute-dev-version: could not compute a non-negative version sequence" >&2
  exit 1
fi

printf '%s.%s.%s-dev.%s\n' "$major" "$minor" "$next_patch" "$sequence"
