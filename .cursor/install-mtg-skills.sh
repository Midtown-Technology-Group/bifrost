#!/usr/bin/env bash
# Install the compact profile of mtg-codex-skills for Cursor cloud agents.
#
# Pin: reviewed bootstrap commit from mtg-codex-skills docs/agent-bootstrap.md
# (PR #36). Do not float this to main. When those docs name a newer reviewed
# pin, replace PIN with that commit. The package installer, not a raw copy,
# writes references/package-location.json and the release search index.
#
# Fail-soft: clone, Python, installer, or verify problems are logged and this
# script still exits 0 so an environment build is not blocked. Idempotent.
# A non-git path already at ~/mtg-codex-skills is left alone. Existing
# non-symlink entries under ~/.cursor/skills are left alone.

set -u
set -o pipefail

PIN="a4e8f7d9"
REPO_URL="https://github.com/Midtown-Technology-Group/mtg-codex-skills.git"
DEST="${HOME}/mtg-codex-skills"
CODEX_HOME="${HOME}/.codex"
CURSOR_SKILLS="${HOME}/.cursor/skills"

warn() {
  printf 'WARNING: mtg-codex-skills: %s\n' "$*" >&2
}

redact() {
  sed -E \
    -e 's#(x-access-token:)[^@[:space:]]+#\1[REDACTED]#g' \
    -e 's#(gh[pousr]_)[A-Za-z0-9_]+#\1[REDACTED]#g' \
    -e 's#(github_pat_)[A-Za-z0-9_]+#\1[REDACTED]#g'
}

# Run a command, keep its status, and print only redacted diagnostics.
run_quiet() {
  local log status
  log=$(mktemp)
  # `if cmd` resets $? to 0 when cmd fails and there is no else branch.
  status=0
  "$@" >"$log" 2>&1 || status=$?
  if [[ "$status" -eq 0 ]]; then
    rm -f "$log"
    return 0
  fi
  warn "command failed ($1, status ${status})"
  redact <"$log" >&2 || true
  rm -f "$log"
  return "$status"
}

python_bin() {
  if command -v python3.12 >/dev/null 2>&1; then
    printf '%s\n' python3.12
    return 0
  fi
  if command -v python3 >/dev/null 2>&1 \
    && python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
    printf '%s\n' python3
    return 0
  fi
  return 1
}

ensure_pyyaml() {
  local py="$1"
  if "$py" -c 'import yaml' >/dev/null 2>&1; then
    return 0
  fi
  echo "mtg-codex-skills: installing PyYAML for ${py}"
  "$py" -m pip install --user --disable-pip-version-check --no-input PyYAML
}

checkout_pin() {
  if [[ -e "$DEST" && ! -d "$DEST/.git" ]]; then
    warn "${DEST} exists and is not a git checkout; refusing to replace it"
    return 1
  fi
  if [[ ! -d "$DEST/.git" ]]; then
    echo "mtg-codex-skills: cloning ${REPO_URL}"
    if ! run_quiet git clone -- "$REPO_URL" "$DEST"; then
      if [[ -e "$DEST" && ! -d "$DEST/.git" ]]; then
        rm -rf -- "$DEST"
      fi
      return 1
    fi
  fi
  if ! git -C "$DEST" rev-parse --verify --quiet "${PIN}^{commit}" >/dev/null 2>&1; then
    echo "mtg-codex-skills: fetching pin ${PIN}"
    run_quiet git -C "$DEST" fetch --prune origin || return $?
  fi
  run_quiet git -C "$DEST" checkout --detach "$PIN" || return $?
  local sha
  sha=$(git -C "$DEST" rev-parse HEAD) || return $?
  echo "mtg-codex-skills: checked out ${sha} (pin ${PIN})"
}

install_profile() {
  local py="$1"
  if [[ ! -f "$DEST/scripts/skill_package.py" ]]; then
    warn "scripts/skill_package.py is missing from the pinned checkout"
    return 1
  fi
  mkdir -p "$CODEX_HOME"
  (
    cd "$DEST" || exit 1
    "$py" scripts/skill_package.py install --codex-home "$CODEX_HOME" --profile compact || exit $?
    "$py" scripts/skill_package.py verify --codex-home "$CODEX_HOME" --profile compact || exit $?
  )
}

publish_skills() {
  local skills_root="${CODEX_HOME}/skills"
  local dir name target linked
  if [[ ! -d "$skills_root" ]]; then
    warn "installer did not create ${skills_root}"
    return 1
  fi
  mkdir -p "$CURSOR_SKILLS"
  linked=0
  for dir in "$skills_root"/*; do
    if [[ ! -d "$dir" ]]; then
      continue
    fi
    name=$(basename "$dir")
    if [[ "$name" == .* || ! -f "$dir/SKILL.md" ]]; then
      continue
    fi
    target="${CURSOR_SKILLS}/${name}"
    if [[ -L "$target" ]]; then
      ln -sfn "$dir" "$target" || return $?
    elif [[ -e "$target" ]]; then
      warn "leaving existing ${target} in place"
      continue
    else
      ln -s "$dir" "$target" || return $?
    fi
    linked=$((linked + 1))
    echo "mtg-codex-skills: linked ${name} into ${CURSOR_SKILLS}"
  done
  if [[ "$linked" -eq 0 ]]; then
    warn "no installed skills with SKILL.md were found under ${skills_root}"
    return 1
  fi
}

main() {
  local py
  export GIT_TERMINAL_PROMPT=0
  if ! py=$(python_bin); then
    warn "Python 3.12 is required and was not found"
    return 1
  fi
  # Explicit statuses: this function is invoked from `if`, where `set -e` does not stop the caller.
  ensure_pyyaml "$py" || return $?
  checkout_pin || return $?
  install_profile "$py" || return $?
  publish_skills || return $?
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  if main; then
    echo "mtg-codex-skills: install finished"
  else
    status=$?
    warn "install stopped (status ${status}). Continuing so the environment build is not blocked."
  fi
  exit 0
fi
