#!/usr/bin/env bash
# Host checks for the Cursor cloud mtg-codex-skills installer.
# No network and no Docker: git and the package installer are fixtures.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INSTALLER="${ROOT}/.cursor/install-mtg-skills.sh"
ENV_JSON="${ROOT}/.cursor/environment.json"
FAIL=0

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  FAIL=1
}

assert_eq() {
  local got="$1" want="$2" label="$3"
  if [[ "$got" != "$want" ]]; then
    fail "${label}: got [${got}] want [${want}]"
  fi
}

assert_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    fail "missing file ${path}"
  fi
}

new_home() {
  mktemp -d
}

run_installer() {
  local home="$1"
  local path="$2"
  local log="$3"
  shift 3
  env HOME="$home" PATH="$path" "$@" /usr/bin/bash "$INSTALLER" >"$log" 2>&1 || true
  # The installer is fail-soft and must not propagate a caller status.
  return 0
}

write_fake_git() {
  local bin="$1"
  local mode="$2"
  mkdir -p "$bin"
  cat >"${bin}/git" <<'EOF'
#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >> "${GIT_FAKE_LOG:?}"
mode="${GIT_FAKE_MODE:-ok}"
if [[ "${1:-}" == "-C" ]]; then
  dest=$2
  shift 2
else
  dest=""
fi
cmd="${1:-}"
if [[ $# -gt 0 ]]; then
  shift
fi
case "$cmd" in
  clone)
    if [[ "$mode" == "clone-fail" ]]; then
      printf '%s\n' "fatal: repository not found" >&2
      printf '%s\n' "https://x-access-token:ghs_SUPERSECRETTOKEN@github.com/Midtown-Technology-Group/mtg-codex-skills.git" >&2
      exit 128
    fi
    dest="${!#}"
    mkdir -p "${dest}/scripts" "${dest}/.git"
    cp "${GIT_FAKE_PACKAGE:?}" "${dest}/scripts/skill_package.py"
    printf '%s\n' "${GIT_FAKE_SHA:?}" > "${dest}/.git/HEAD_SHA"
    ;;
  rev-parse)
    if [[ "${1:-}" == "--verify" ]]; then
      [[ -n "$dest" && -f "${dest}/.git/HEAD_SHA" ]]
      exit $?
    fi
    if [[ "${1:-}" == "HEAD" ]]; then
      cat "${dest}/.git/HEAD_SHA"
      exit 0
    fi
    printf 'unexpected rev-parse %s\n' "$*" >&2
    exit 99
    ;;
  checkout|fetch)
    exit 0
    ;;
  *)
    printf 'unexpected git command %s\n' "$cmd" >&2
    exit 99
    ;;
esac
EOF
  chmod +x "${bin}/git"
}

write_fake_package() {
  local path="$1"
  cat >"$path" <<'EOF'
#!/usr/bin/env python3
import os
import sys
from pathlib import Path

args = sys.argv[1:]
if not args or args[0] not in {"install", "verify"}:
    sys.stderr.write(f"unexpected argv {args}\n")
    sys.exit(2)
if "--codex-home" not in args or "--profile" not in args or "compact" not in args:
    sys.stderr.write(f"missing compact install flags {args}\n")
    sys.exit(2)
codex = Path(args[args.index("--codex-home") + 1])
if args[0] == "verify":
    sys.exit(1 if os.environ.get("MTG_SKILLS_TEST_FIXTURE") == "verify-fail" else 0)
for name in ("mtg-engineering-flow", "mtg-skill-router", "github-pr-stewardship"):
    skill = codex / "skills" / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\nbody\n")
router = codex / "skills" / "mtg-skill-router" / "references"
router.mkdir(parents=True, exist_ok=True)
(router / "package-location.json").write_text('{"profile":"compact"}\n')
sys.exit(0)
EOF
}

real_path() {
  printf '%s\n' "/usr/bin:/bin"
}

test_environment_json() {
  python3 - "$ENV_JSON" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text())
assert data == {
    "repositoryDependencies": [
        "github.com/Midtown-Technology-Group/mtg-codex-skills"
    ],
    "install": "bash .cursor/install-mtg-skills.sh",
}, data
PY
}

test_executable() {
  if [[ ! -x "$INSTALLER" ]]; then
    fail "installer is not executable"
  fi
  bash -n "$INSTALLER"
}

test_refuses_non_git_dest() {
  local home log bin
  home=$(new_home)
  log=$(mktemp)
  bin=$(mktemp -d)
  printf 'keep\n' > "${home}/mtg-codex-skills"
  cat >"${bin}/git" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${GIT_FAKE_LOG:?}"
exit 99
EOF
  chmod +x "${bin}/git"
  local git_log="${home}/git.log"
  : >"$git_log"
  run_installer "$home" "${bin}:$(real_path)" "$log" GIT_FAKE_LOG="$git_log"
  assert_eq "$(cat "${home}/mtg-codex-skills")" "keep" "non-git dest preserved"
  if [[ -s "$git_log" ]]; then
    fail "git was invoked for a non-git destination"
  fi
  if ! grep -q "refusing to replace it" "$log"; then
    fail "missing refuse warning: $(cat "$log")"
  fi
  rm -rf "$home" "$bin" "$log"
}

test_clone_failure_redacts_token() {
  local home log bin git_log
  home=$(new_home)
  log=$(mktemp)
  bin=$(mktemp -d)
  git_log="${home}/git.log"
  : >"$git_log"
  write_fake_git "$bin" clone-fail
  run_installer "$home" "${bin}:$(real_path)" "$log" \
    GIT_FAKE_LOG="$git_log" GIT_FAKE_MODE=clone-fail
  if grep -q "SUPERSECRETTOKEN" "$log"; then
    fail "token leaked in installer output"
  fi
  if grep -Eq 'ghs_[A-Za-z0-9_]+' "$log"; then
    fail "raw ghs token remains in installer output"
  fi
  if ! grep -q "x-access-token:\[REDACTED\]" "$log"; then
    fail "redacted token missing: $(cat "$log")"
  fi
  if ! grep -q "command failed (git, status 128)" "$log"; then
    fail "clone failure did not keep git status 128: $(cat "$log")"
  fi
  if grep -q "fetching pin" "$log"; then
    fail "installer continued after a failed clone: $(cat "$log")"
  fi
  if ! grep -q "Continuing so the environment build is not blocked" "$log"; then
    fail "fail-soft warning missing: $(cat "$log")"
  fi
  if [[ -e "${home}/mtg-codex-skills" ]]; then
    fail "failed clone left ${home}/mtg-codex-skills"
  fi
  rm -rf "$home" "$bin" "$log"
}

prepare_success_env() {
  local home="$1"
  local bin="$2"
  local package="$3"
  mkdir -p "${home}/py-stub" "${home}/.cursor/skills/mtg-engineering-flow"
  printf 'preserve\n' > "${home}/.cursor/skills/mtg-engineering-flow/marker"
  printf '%s\n' '__version__ = "test"' > "${home}/py-stub/yaml.py"
  write_fake_package "$package"
  write_fake_git "$bin" ok
}

test_success_links_without_clobber() {
  local home log bin package git_log
  home=$(new_home)
  log=$(mktemp)
  bin=$(mktemp -d)
  package="${home}/skill_package.py"
  git_log="${home}/git.log"
  : >"$git_log"
  prepare_success_env "$home" "$bin" "$package"
  run_installer "$home" "${bin}:$(real_path)" "$log" \
    GIT_FAKE_LOG="$git_log" \
    GIT_FAKE_MODE=ok \
    GIT_FAKE_PACKAGE="$package" \
    GIT_FAKE_SHA=a4e8f7d9aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
    PYTHONPATH="${home}/py-stub" \
    MTG_SKILLS_TEST_FIXTURE=ok
  if ! grep -q "install finished" "$log"; then
    fail "success run did not finish: $(cat "$log")"
  fi
  if ! grep -q "checked out a4e8f7d9aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa (pin a4e8f7d9)" "$log"; then
    fail "pin was not reported: $(cat "$log")"
  fi
  assert_eq "$(cat "${home}/.cursor/skills/mtg-engineering-flow/marker")" "preserve" "existing skill dir"
  if [[ -L "${home}/.cursor/skills/mtg-engineering-flow" ]]; then
    fail "existing skill directory was replaced with a symlink"
  fi
  for name in mtg-skill-router github-pr-stewardship; do
    if [[ ! -L "${home}/.cursor/skills/${name}" ]]; then
      fail "${name} was not symlinked"
    fi
    assert_file "${home}/.cursor/skills/${name}/SKILL.md"
  done
  assert_file "${home}/.cursor/skills/mtg-skill-router/references/package-location.json"
  if ! grep -q "leaving existing" "$log"; then
    fail "missing leave-existing warning: $(cat "$log")"
  fi

  # A second run stays successful and still does not replace the real directory.
  : >"$log"
  run_installer "$home" "${bin}:$(real_path)" "$log" \
    GIT_FAKE_LOG="$git_log" \
    GIT_FAKE_MODE=ok \
    GIT_FAKE_PACKAGE="$package" \
    GIT_FAKE_SHA=a4e8f7d9aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
    PYTHONPATH="${home}/py-stub" \
    MTG_SKILLS_TEST_FIXTURE=ok
  if ! grep -q "install finished" "$log"; then
    fail "second run did not finish: $(cat "$log")"
  fi
  assert_eq "$(cat "${home}/.cursor/skills/mtg-engineering-flow/marker")" "preserve" "existing skill dir after rerun"
  rm -rf "$home" "$bin" "$log"
}

test_verify_failure_does_not_publish() {
  local home log bin package git_log
  home=$(new_home)
  log=$(mktemp)
  bin=$(mktemp -d)
  package="${home}/skill_package.py"
  git_log="${home}/git.log"
  : >"$git_log"
  mkdir -p "${home}/py-stub"
  printf '%s\n' '__version__ = "test"' > "${home}/py-stub/yaml.py"
  write_fake_package "$package"
  write_fake_git "$bin" ok
  run_installer "$home" "${bin}:$(real_path)" "$log" \
    GIT_FAKE_LOG="$git_log" \
    GIT_FAKE_MODE=ok \
    GIT_FAKE_PACKAGE="$package" \
    GIT_FAKE_SHA=a4e8f7d9aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
    PYTHONPATH="${home}/py-stub" \
    MTG_SKILLS_TEST_FIXTURE=verify-fail
  if [[ -d "${home}/.cursor/skills" ]]; then
    fail "verify failure published ~/.cursor/skills"
  fi
  if ! grep -q "Continuing so the environment build is not blocked" "$log"; then
    fail "verify failure was not fail-soft: $(cat "$log")"
  fi
  if grep -q "install finished" "$log"; then
    fail "verify failure reported success"
  fi
  rm -rf "$home" "$bin" "$log"
}

test_missing_python_is_fail_soft() {
  local home log bin
  home=$(new_home)
  log=$(mktemp)
  bin=$(mktemp -d)
  run_installer "$home" "$bin" "$log"
  if ! grep -q "Python 3.12 is required" "$log"; then
    fail "missing python warning: $(cat "$log")"
  fi
  if ! grep -q "Continuing so the environment build is not blocked" "$log"; then
    fail "missing python was not fail-soft: $(cat "$log")"
  fi
  rm -rf "$home" "$bin" "$log"
}

test_environment_json
test_executable
test_refuses_non_git_dest
test_clone_failure_redacts_token
test_success_links_without_clobber
test_verify_failure_does_not_publish
test_missing_python_is_fail_soft

if [[ "$FAIL" -ne 0 ]]; then
  printf 'install-mtg-skills tests failed\n' >&2
  exit 1
fi
printf 'install-mtg-skills tests passed\n'
