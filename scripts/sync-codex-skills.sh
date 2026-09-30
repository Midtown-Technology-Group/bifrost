#!/usr/bin/env bash
# sync-codex-skills.sh — regenerate the public plugin and single repository
# discovery mirror from .claude/skills/. The host check_skill_mirrors.py guard
# fails CI if generated files drift or duplicate discovery returns.
#
# Mirror rules:
#   plugins/bifrost/skills/  = PUBLIC  skills (dirs that have a symlink under skills/)
#   .agents/skills/          = ALL canonical repository skills
#   .codex/skills/           = retired; do not add a second discovery copy
#
# Idempotent: running twice produces no changes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SKILLS_DIR="${REPO_ROOT}/skills"
CANONICAL_DIR="${REPO_ROOT}/.claude/skills"
PUBLIC_MIRROR="${REPO_ROOT}/plugins/bifrost/skills"
LOCAL_MIRROR="${REPO_ROOT}/.agents/skills"

if find "${REPO_ROOT}/.codex/skills" -name SKILL.md -print -quit 2>/dev/null | grep -q .; then
    echo "ERROR: legacy .codex/skills discovery copies remain; reconcile them before sync." >&2
    exit 1
fi

# ── 1. Compute the public-target basenames from skills/ symlinks ──────────────
declare -A public_targets   # basename → 1

for link in "${SKILLS_DIR}"/*; do
    [[ -L "${link}" ]] || continue          # only symlinks
    target="$(readlink "${link}")"          # e.g. ../.claude/skills/bifrost-build
    basename_target="$(basename "${target}")"
    public_targets["${basename_target}"]=1
done

# ── 2. PUBLIC mirror (plugins/bifrost/skills/) ────────────────────────────────
mkdir -p "${PUBLIC_MIRROR}"

echo "=== PUBLIC mirror → ${PUBLIC_MIRROR} ==="
for target_name in "${!public_targets[@]}"; do
    src="${CANONICAL_DIR}/${target_name}"
    dst="${PUBLIC_MIRROR}/${target_name}"
    if [[ ! -d "${src}" ]]; then
        # A skills/ symlink pointing at a missing canonical dir would silently
        # drop that public skill from the mirror. Fail hard so CI catches it.
        echo "  ERROR: skills/ symlink target missing: ${src}" >&2
        exit 1
    fi
    rsync -a --delete "${src}/" "${dst}/"
    echo "  synced ${target_name}"
done

# Remove stale dirs in the public mirror that are no longer in the public set
for existing in "${PUBLIC_MIRROR}"/*/; do
    [[ -d "${existing}" ]] || continue
    dir_name="$(basename "${existing}")"
    if [[ -z "${public_targets[${dir_name}]+_}" ]]; then
        echo "  removing stale: ${dir_name}"
        rm -rf "${existing}"
    fi
done

# ── 3. Single repository discovery mirror (.agents/skills/) ──────────────────
mkdir -p "${LOCAL_MIRROR}"

echo "=== LOCAL mirror → ${LOCAL_MIRROR} ==="
for src in "${CANONICAL_DIR}"/*/; do
    [[ -d "${src}" ]] || continue
    skill_name="$(basename "${src}")"
    dst="${LOCAL_MIRROR}/${skill_name}"
    rsync -a --delete "${src}/" "${dst}/"
    echo "  synced ${skill_name}"
done

# Remove generated local copies whose canonical source no longer exists.
for existing in "${LOCAL_MIRROR}"/*/; do
    [[ -d "${existing}" ]] || continue
    dir_name="$(basename "${existing}")"
    src="${CANONICAL_DIR}/${dir_name}"
    if [[ ! -d "${src}" ]]; then
        echo "  removing stale: ${dir_name}"
        rm -rf "${existing}"
    fi
done

echo "=== Done ==="
