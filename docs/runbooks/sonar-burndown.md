# Sonar Burndown Runbook

Incremental, agent-driven remediation of the SonarQubeCloud backlog for
`Midtown-Technology-Group_bifrost` (default branch `main`).
Existing integration: SonarCloud **Automatic Analysis** owns branch/PR
decoration — there is no CI scanner job, and `sonar-project.properties` /
`.sonarcloud.properties` only carry scope/exclusion settings (no org or
project key is stored in the repo). This workflow adds an agent-operated
loop around that integration without changing it.

## Prerequisites

- The [SonarQube CLI](https://github.com/SonarSource/sonarqube-cli) (`sonar`,
  verified against v1.8.0):
  - `brew install sonarqube-cli`, or
  - `mise use -g sonarqube-cli@latest`, or
  - `curl -o- https://raw.githubusercontent.com/SonarSource/sonarqube-cli/refs/heads/master/user-scripts/install.sh | bash`
- A SonarQubeCloud user token with Browse permission on the project.
  Generate it via SonarQubeCloud > My Account > Security.
- The repo's normal validation stack (`./test.sh`, `./debug.sh`) for the
  touched area — see `bifrost-testing` / `AGENTS.md`.

## Authentication (non-interactive)

Export both variables before invoking `sonar` (CI secret store, direnv, an
untracked `.env` file, or a password-manager CLI — never commit them, never
pass the token as a CLI argument):

```bash
export SONARQUBE_CLI_TOKEN="<token>"      # SonarQubeCloud user token
export SONARQUBE_CLI_ORG="midtown-technology-group"  # org KEY, not display name
export SONAR_PROJECT_KEY="Midtown-Technology-Group_bifrost"  # default if unset
```

`SONARQUBE_CLI_ORG` must be the lowercase organization **key** (as it
appears in project URLs on sonarcloud.io), not the display name
(`Midtown-Technology-Group`). A wrong value still authenticates, but every
issue search silently returns zero rows — `scripts/sonar-burndown.sh check`
validates the key and fails fast with this exact hint.

Set **both** `SONARQUBE_CLI_TOKEN` and `SONARQUBE_CLI_ORG`: with only the
token present the CLI warns on stderr and falls back to keychain
credentials, which is rarely what automation wants. Interactive humans can
use `sonar auth login` instead (agents cannot — it needs a browser).

## Listing issues

```bash
scripts/sonar-burndown.sh check                                  # install + auth sanity
scripts/sonar-burndown.sh list                                  # open backlog, TOON
scripts/sonar-burndown.sh list --severities HIGH,BLOCKER        # narrow by severity
scripts/sonar-burndown.sh list --branch main --file api/src     # narrow by scope
scripts/sonar-burndown.sh list --all --format json > backlog.json  # full backlog
scripts/sonar-burndown.sh rule python:S3776                     # rule intent
```

This is the exact agent backlog command:

```bash
scripts/sonar-burndown.sh list --statuses OPEN,CONFIRMED --format toon
```

(`--statuses OPEN,CONFIRMED` is the default; `--format toon` is the default —
both are shown explicitly so agents can copy-paste.) Raw equivalent:

```bash
sonar list issues -p Midtown-Technology-Group_bifrost \
  --statuses OPEN,CONFIRMED --format toon --page-size 500
```

Severity vocab depends on the server mode: MQR mode uses
`INFO,LOW,MEDIUM,HIGH,BLOCKER`; Standard mode uses
`INFO,MINOR,MAJOR,CRITICAL,BLOCKER`. If a `--severities` value is rejected,
the project is in the other mode — adjust and retry.

## Local remediation cycle

1. Pick ONE batch: 3–10 related findings (same rule, same file cluster),
   behavior-preserving, PR-sized.
2. Fix the code. If a rule's intent is unclear, read it first
   (`... rule <RULE_KEY>`).
3. Validate with the repo's normal gates for the touched area
   (`./test.sh <path>`, `./test.sh quality api`, client `tsc`/`lint`).
4. Verify with Sonar where supported:

   ```bash
   scripts/sonar-burndown.sh verify --base main
   scripts/sonar-burndown.sh verify --file <path/to/changed.py>  # fast single-file loop
   ```

   Raw equivalent: `sonar analyze agentic --base main --force`.
5. Confirm the targeted issue keys are gone by re-listing
   (`list --file <dir>` or the same rule filter). Never transition issues
   manually — analysis decides.
6. Stop and report instead of fixing when the change would touch public
   behavior, auth/authz, tenant isolation, crypto, concurrency, execution
   semantics, migrations, agent policy, or architecture.

## How an AI coding agent consumes this

Any shell-capable agent (Codex, Muse/jcode, OpenCode, …) needs only the
CLI path above — no harness-specific integration. The `sonar-burndown`
skill (`.claude/skills/sonar-burndown/`, mirrored to `.codex/skills/`)
encodes the batch-selection rules, safety constraints, and summary format.
Point the agent at this runbook plus the live `list` output and let it run
the cycle.

## Entitlement / capability limits discovered

- `sonar analyze agentic` is **SonarQube Cloud only**, runs server-side
  "Vortex" analysis, and carries "limitations apply" (large change sets
  prompt for confirmation — the wrapper passes `--force`; scope and depth
  limits are enforced server-side). **Confirmed 2026-09-24: Vortex returns
  `403 Forbidden` for this organization**, so `verify` cannot run here —
  verification goes through pushing the branch and letting Automatic
  Analysis report, then re-listing. If entitlement is added later, `verify`
  works unchanged.
- `sonar analyze agentic --format` supports only `text`/`json` (no `toon`).
- `sonar remediate` (AI-generated fixes, Cloud only, max 20 issue keys,
  non-interactive via `--issues`) exists but is **not** part of this
  workflow: fixes here are agent-authored and repo-validated so they pass
  the normal test/lint/type gates.
- Reading findings needs only Browse permission; no admin, no quality
  profile changes — and profile changes are out of scope for remediation.

## Optional: SonarQube MCP server

The official [SonarSource/sonarqube-mcp-server](https://github.com/SonarSource/sonarqube-mcp-server)
(Docker image `sonarsource/sonarqube-mcp`) exposes issues/rules to MCP
clients. It needs `SONARQUBE_TOKEN` plus `SONARQUBE_ORG` (Cloud) or
`SONARQUBE_URL` (Server), e.g.:

```json
{ "mcpServers": { "sonarqube": {
  "command": "docker",
  "args": ["run", "--init", "--pull=always", "-i", "--rm",
           "-e", "SONARQUBE_TOKEN", "-e", "SONARQUBE_ORG",
           "sonarsource/sonarqube-mcp"],
  "env": { "SONARQUBE_TOKEN": "<token>", "SONARQUBE_ORG": "<org>" } } } }
```

It is **optional**: the CLI path stays sufficient and harness-agnostic, and
no MCP config is committed here (it would couple the repo to specific
clients). Where it may help: richer rule/issue detail browsing for agents
that already speak MCP. Verify flags against the upstream README at time
of use — the server evolves independently of this repo.

## CI consideration

No CI job is added by this change, deliberately:

- SonarCloud Automatic Analysis already decorates branches/PRs; nothing is
  newly blocking and the merge gate is unchanged.
- A recurring burndown job would need a `SONARQUBE_CLI_TOKEN` secret plus
  agentic-analysis entitlement, and historical debt must not fail PRs.
- If that ever becomes desirable, the shape is: `workflow_dispatch` +
  scheduled, non-blocking (`continue-on-error`), running
  `scripts/sonar-burndown.sh list --all --format json` to publish a backlog
  snapshot artifact — never a gate.

## Pilot note

Tooling-first: the first remediation PR should be one tiny batch from
`list` output (see the skill for candidate selection). If the live backlog
shows no safe batch, stop after tooling and say so.
