---
name: sonar-burndown
description: Burn down the SonarQubeCloud backlog in small, safe, PR-sized batches. Use when the user asks to fix Sonar issues, reduce static-analysis debt, or run a Sonar remediation cycle. Trigger phrases - "fix sonar issues", "sonar backlog", "sonar burndown", "burn down sonar", "remediate sonar findings", "/sonar-burndown".
---

# Sonar Burndown

Fix open SonarQubeCloud findings in small, behavior-preserving batches. Sonar is the source of findings and the verification oracle — you are the remediation worker.

## Hard rules (non-negotiable)

1. **Never mark issues resolved by hand.** Fix the code, then let subsequent Sonar analysis decide the finding is gone. Do not use "resolve as fixed / won't fix / false positive" transitions as part of remediation.
2. **One small batch per change.** Default: 3–10 related findings, fewer if nontrivial. Never a repo-wide "Sonar cleanup" PR.
3. **Smallest correct change.** No broad formatting churn, no speculative refactors beyond the selected findings.
4. **Never disable a rule, weaken a quality profile, or add `NOSONAR`/suppressions** just to make a finding disappear. A suppression needs a specific written justification tied to a false positive — and even then, prefer fixing the code.
5. **Stop if the fix would materially change public behavior, auth/authz, tenant isolation, cryptography, concurrency, workflow execution semantics, migrations, agent policy, or architecture.** Report the finding as left-unresolved with the reason.

## The cycle

1. **Check tooling.** `scripts/sonar-burndown.sh check` — needs the `sonar` CLI plus `SONARQUBE_CLI_TOKEN` and `SONARQUBE_CLI_ORG` in the environment. Never hardcode secrets or pass tokens as CLI args.
2. **Fetch the live backlog.** `scripts/sonar-burndown.sh list` (TOON output). Narrow with `--severities`, `--branch`, or `--file`. Use `--all --format json` only when the backlog exceeds one page (500).
3. **Pick one coherent batch:** same rule, same subsystem/file cluster, straightforward and behavior-preserving, fits one PR. Good pilot material: dead code, duplicated conditions, trivial resource handling, clear typing/nullability, low-risk maintainability findings with obvious intent.
4. **Read the rule first** when its intent is unclear: `scripts/sonar-burndown.sh rule <RULE_KEY>` (e.g. `python:S3776`).
5. **Make the smallest correct fix** in the working tree.
6. **Run the repo's normal validation** for the touched area (`./test.sh` scope per `bifrost-testing`: relevant unit tests plus lint/type-checks — `ruff`/`pyright` for Python via `./test.sh quality api`, `tsc`/`lint` for the client).
7. **Run Sonar verification** where supported: `scripts/sonar-burndown.sh verify --base <default-branch>` (Cloud agentic analysis; needs the project entitled for it — if it errors, say so and rely on Automatic Analysis of the pushed branch instead).
8. **Confirm the targeted findings disappeared** — re-list (`list --file <path>` or by rule) and check the issue keys are gone. A fix that doesn't clear the finding is not done; investigate, don't suppress.
9. **Summarize:** issue keys addressed, rule IDs, files changed, validation performed, findings intentionally left unresolved and why.

## Reference

Full operator doc (prereqs, env vars, MCP option, entitlement limits): `docs/runbooks/sonar-burndown.md`.
