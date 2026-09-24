#!/usr/bin/env bash
# sonar-burndown.sh — thin, harness-agnostic wrapper around the SonarQube CLI
# for incremental, agent-driven SonarQubeCloud issue burn-down.
#
# Sonar is the source of findings and the verification oracle; the coding
# agent is the remediation worker. This script never marks issues resolved —
# subsequent Sonar analysis decides whether a finding is gone.
#
# Prerequisites: the `sonar` CLI on PATH (see --help install notes) plus
# non-interactive auth (see `check` below). No secrets on the command line.
#
# Usage:
#   scripts/sonar-burndown.sh check
#     # verify CLI install + authentication (no secrets printed)
#   scripts/sonar-burndown.sh list [--severities HIGH,BLOCKER] [--branch main]
#     # open backlog for the project, TOON output for agents (default page: 500)
#   scripts/sonar-burndown.sh list --all --format json > backlog.json
#     # paginate the full backlog (JSON only; TOON pages cannot be merged)
#
# NOTE: SONARQUBE_CLI_ORG must be the lowercase organization KEY
# (e.g. midtown-technology-group), not the display name. With a wrong value
# the CLI still authenticates, but `list` silently returns zero rows
# (verified against the raw Web API). `check` validates the key.
#   scripts/sonar-burndown.sh rule python:S3776
#     # inspect a rule's intent before changing code
#   scripts/sonar-burndown.sh verify [--base main] [--file path/...]
#     # server-side analysis of local changes vs base (SonarQube Cloud only)
#
# Environment:
#   SONARQUBE_CLI_TOKEN   SonarQubeCloud user token (required, never commit)
#   SONARQUBE_CLI_ORG     SonarQubeCloud organization key (required with token)
#   SONAR_PROJECT_KEY     Project key (default: Midtown-Technology-Group_bifrost)
#   SONAR_BASE_BRANCH     Default verify base (default: origin's HEAD, else main)
set -euo pipefail

DEFAULT_PROJECT="Midtown-Technology-Group_bifrost"
PROJECT="${SONAR_PROJECT_KEY:-$DEFAULT_PROJECT}"

default_base() {
  if [[ -n "${SONAR_BASE_BRANCH:-}" ]]; then
    echo "$SONAR_BASE_BRANCH"
    return
  fi
  local head
  head="$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null || true)"
  if [[ -z "$head" ]]; then echo "main"; else echo "${head#refs/remotes/origin/}"; fi
}

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed 's/^# \?//'
}

cmd_check() {
  if ! command -v sonar >/dev/null 2>&1; then
    echo "error: 'sonar' CLI not found on PATH." >&2
    echo "install one of:" >&2
    echo "  brew install sonarqube-cli" >&2
    echo "  mise use -g sonarqube-cli@latest" >&2
    echo "  curl -o- https://raw.githubusercontent.com/SonarSource/sonarqube-cli/refs/heads/master/user-scripts/install.sh | bash" >&2
    exit 1
  fi
  sonar --version
  local missing=0
  [[ -n "${SONARQUBE_CLI_TOKEN:-}" ]] || { echo "missing: SONARQUBE_CLI_TOKEN" >&2; missing=1; }
  [[ -n "${SONARQUBE_CLI_ORG:-}" ]] || { echo "missing: SONARQUBE_CLI_ORG" >&2; missing=1; }
  if [[ "$missing" -ne 0 ]]; then
    echo "Generate a token via SonarQubeCloud > My Account > Security, then export both variables." >&2
    echo "Set both: with only the token present the CLI warns and falls back to keychain credentials." >&2
    exit 1
  fi
  sonar auth status
  # Validate that SONARQUBE_CLI_ORG is the organization KEY, not the display
  # name: a wrong value still authenticates but yields silent zero-row
  # searches. The organizations API echoes only resolvable keys.
  local found
  found="$(sonar api get "/api/organizations/search?organizations=${SONARQUBE_CLI_ORG}" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('organizations',[])))")"
  if [[ "$found" -ne 1 ]]; then
    echo "error: SONARQUBE_CLI_ORG='${SONARQUBE_CLI_ORG}' does not resolve to a SonarCloud organization." >&2
    echo "Use the lowercase organization KEY (see the project URL on sonarcloud.io), not the display name." >&2
    exit 1
  fi
  echo "Org key OK: ${SONARQUBE_CLI_ORG}"
}

# Native `sonar list issues` with project/status defaults. TOON is the
# default format (LLM-friendly); --all paginates to completion through temp
# files (page payloads exceed argv limits) and requires --format json since
# TOON pages cannot be merged.
cmd_list() {
  local all=0 format="" page_size=0 args=() prev=""
  for arg in "$@"; do
    if [[ "$arg" == "--all" ]]; then all=1; prev=""; continue; fi
    if [[ "$prev" == "--format" ]]; then format="$arg"; fi
    if [[ "$arg" == --format=* ]]; then format="${arg#--format=}"; fi
    if [[ "$prev" == "--page-size" ]]; then page_size=1; fi
    if [[ "$arg" == --page-size=* ]]; then page_size=1; fi
    if [[ "$arg" == "--format" || "$arg" == "--page-size" ]]; then prev="$arg"; else prev=""; fi
    args+=("$arg")
  done
  [[ -z "$format" ]] && format="toon"
  if [[ "$all" -eq 1 ]]; then
    if [[ "$format" != "json" ]]; then
      echo "error: --all requires --format json (TOON/table/csv pages cannot be merged)." >&2
      exit 2
    fi
    local tmpdir page_num=1
    tmpdir="$(mktemp -d)"
    trap 'rm -rf "$tmpdir"' RETURN
    # Pagination controls the page itself: drop user --format/--page which
    # would collide with the loop's --format json / --page N.
    local page_args=() skip_next=0 x
    for x in "${args[@]}"; do
      if [[ "$skip_next" -eq 1 ]]; then skip_next=0; continue; fi
      case "$x" in
        --format|--page) skip_next=1 ;;
        --format=*|--page=*) ;;
        *) page_args+=("$x") ;;
      esac
    done
    list_page() {
      sonar list issues -p "$PROJECT" --statuses "OPEN,CONFIRMED" \
        --format json --page "$page_num" "${page_args[@]}" > "$tmpdir/page.json"
    }
    list_page
    cp "$tmpdir/page.json" "$tmpdir/merged.json"
    while python3 -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if len(d.get('issues',[])) < d.get('total',0) else 1)" "$tmpdir/merged.json"; do
      page_num=$((page_num + 1))
      list_page
      python3 - "$tmpdir/merged.json" "$tmpdir/page.json" "$page_num" <<'EOF'
import json, sys
merged_path, page_path, page_num = sys.argv[1:4]
with open(merged_path) as f:
    a = json.load(f)
with open(page_path) as f:
    b = json.load(f)
a["issues"].extend(b.get("issues", []))
with open(merged_path, "w") as f:
    json.dump(a, f)
n = len(a.get("issues", []))
print(f"fetched page {page_num} ({n} of {a.get('total', '?')} issues)...",
      file=sys.stderr)
EOF
    done
    python3 - "$tmpdir/merged.json" <<'EOF'
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
n = len(d.get("issues", []))
d["p"] = 1
d["ps"] = n
d["total"] = n
d["paging"] = {"pageIndex": 1, "pageSize": n, "total": n,
               "hasNextPage": False}
print(json.dumps(d, indent=2))
EOF
    return
  fi
  local has_format=0 a
  for a in "${args[@]}"; do [[ "$a" == --format* ]] && has_format=1; done
  if [[ "$has_format" -eq 0 ]]; then
    args+=(--format toon)
  fi
  if [[ "$page_size" -eq 0 ]]; then
    args+=(--page-size 500)
  fi
  sonar list issues -p "$PROJECT" --statuses "OPEN,CONFIRMED" "${args[@]}"
}

cmd_rule() {
  if [[ $# -ne 1 ]]; then
    echo "usage: $0 rule <RULE_KEY>   (e.g. $0 rule python:S3776)" >&2
    exit 2
  fi
  # rules/show requires the organization KEY (same value as SONARQUBE_CLI_ORG).
  sonar api get "/api/rules/show?organization=${SONARQUBE_CLI_ORG:-}&key=$1"
}

cmd_verify() {
  local args=() has_base=0 has_force=0 a
  for a in "$@"; do
    [[ "$a" == --base* ]] && has_base=1
    [[ "$a" == --force ]] && has_force=1
    args+=("$a")
  done
  if [[ "$has_base" -eq 0 ]]; then
    args=(--base "$(default_base)" "${args[@]}")
  fi
  # Agents run non-interactively, so always skip the large-changeset
  # confirmation prompt (deduplicated if the caller passed --force).
  if [[ "$has_force" -eq 0 ]]; then
    args+=(--force)
  fi
  local has_project=0
  for a in "${args[@]}"; do [[ "$a" == -p || "$a" == --project* ]] && has_project=1; done
  if [[ "$has_project" -eq 0 ]]; then
    args+=(-p "$PROJECT")
  fi
  sonar analyze agentic "${args[@]}"
}

cmd="${1:-}"; shift || true
case "$cmd" in
  check) cmd_check "$@" ;;
  list) cmd_list "$@" ;;
  rule) cmd_rule "$@" ;;
  verify) cmd_verify "$@" ;;
  -h|--help|help|"") usage ;;
  *) echo "unknown command: $cmd (try: check, list, rule, verify)" >&2; exit 2 ;;
esac
