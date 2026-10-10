# Sonar CI coverage migration

## Verified main import (2026-10-06)

PRs #1068 and #1085 merged through the native queue. Main CI run
[37467149112](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37467149112)
analyzed revision `19728d6526b2fa7bfc5a14aa150d7bec76a86704` after Automatic
Analysis was disabled and `SONAR_CI_ENABLED=true` was read back.

Compute task `AaERZ0zrRjl70sckbn2a` completed successfully as analysis
`ee73a40e-3b92-404f-802f-ccf9730626dd`. The scanner imported `python.xml`,
`client.lcov` and `node.lcov`; independent artifact verification bound all three
to this source/configuration and manifest
`aa6f3b08c0f5649de989f1f7fca4ee2c6ae42bebb142f3d5f6e0f17ecdcb6135`.
Sonar's file inventory includes authored `.github` scripts and the `.claude`
shell hook. File measures confirm Python engine/proxy, client TypeScript and
Node tooling coverage. Markdown skill instructions have no coverage claim.

The existing Sonar way gate is **red**: new-code coverage is 69.3% against 80%,
reliability is C against A, and security is E against A. Overall coverage is
69.8% (73.1% lines, 62.1% branches). Duplication and hotspot-review conditions
passed. The inherited previous-version period starts on 2026-09-02; it includes
47,548 lines to cover and 2,738 new findings. These are observed baseline
results, not a proposal to reset the period or dismiss findings. Preserve the
failed CI result and unchanged gate/profile while remediation is scoped.

The main import is verified. A real PR analysis and the reviewed removal of the
obsolete automatic-analysis configuration remain migration acceptance steps.
The prior `.sonarcloud.properties` is retained in commit `19728d6526b2fa7bfc5a14aa150d7bec76a86704`
for rollback; restore that reviewed configuration before restoring Automatic
Analysis. A red gate after a trustworthy import is a quality finding, not a
reason to discard coverage or restore the old coverage-free green badge.

## State and ownership

This **Lane 3 migration now uses CI analysis behind an operator activation
variable**. It does not authorize queue admission, change required checks, dismiss
findings, or review security hotspots. Automatic Analysis was preserved during
preparation, then disabled before the first CI submission above. The main import
was verified afterward; this follow-up removes the obsolete automatic-analysis
configuration after that verification.

`.github/sonar-ci.json` declares `ci` mode for the Free adapter. CI executes the
evidence job and uploads reports; submissions stay disabled until the repository
variable `SONAR_CI_ENABLED` is exactly `true`. The reviewed adapter merged while
the variable was unset and Automatic Analysis was still active; activation
followed that merge. Keep the variable set for the current CI method. A successful evidence job
means the reports and their source identity passed local checks. It does **not**
mean Sonar analyzed those files or its quality gate passed.

The maintained `sonar-project.properties` applies only to CI-based analysis.
Automatic analysis ignores it. The former `.sonarcloud.properties` is archived
in the verified main commit above and is removed after CI import verification.
Sonar explicitly disallows simultaneous automatic and CI-based analysis for the
same project, so there is no safe indefinite overlap of the two scan methods.
Do not disable the current method before the replacement is ready to submit.

## What the evidence lane measures

- A clean, full-depth checkout of the PR's exact head SHA, the merge-group SHA,
  or the push/manual SHA, with its explicit base recorded. Existing CI still
  exercises its normal merge candidate separately.
- All Python unit tests, including slow unit cases, full API E2E tests, and
  repository Python tool tests. Pure tool tests run in the same locked Docker image from `/repo` with
  their own `PYTHONPATH`, before API tests append their coverage. This avoids the
  API `scripts` package shadowing repository tools. The dedicated `.coveragerc.sonar` includes authored CLI, proxy, router,
  worker, scheduler, MCP, execution, API scripts, and root tools. The ordinary
  API badge's narrower `.coveragerc` is unchanged.
- Comprehensive Vitest with the locked matching V8 provider, branch coverage,
  all authored client TS/TSX files (including unimported files at zero), and LCOV.
- Repository Node test suites, including `.github/scripts/*.test.mjs`, with
  native Node LCOV. The future bounded-policy evaluator is included when its
  files merge; it remains a human-reviewed control surface.
- Repository-wide static-analysis scope. Tests and generated/vendor outputs
  are separated; authored source is not excluded to improve the result.
  The `.claude/skills` originals remain in scope; generated `.agents/skills` and
  plugin skill mirrors and the four `skills/*` directory aliases are not counted twice.

The Sonar-only Compose override instruments the API, replica, worker, scheduler
and HTTP fixture processes with the same branch configuration and authored-source
scope as the unit collector. After E2E tests, normal process shutdown flushes their
parallel data; strict combination preserves real unit and runtime hits before
canonical source filtering and XML export. Missing or incompatible runtime data
fails the evidence job. The existing permissive badge reports are not imported.
A zero or absent per-file report entry is never promoted to a hit. Unmeasured or unsupported
languages, shell/configuration changes, and generated/vendor-file changes remain
explicit in the manifest and require human review. No claim of Rust coverage
is made; current main contains no tracked Rust source, and any future Rust
analysis requires confirmed project support and a real producer.

## Report and source validation

`scripts/sonar/preflight.py` stamps each report immediately after its producer
succeeds, then validates all three together. It binds head/base, source inventory,
configuration and report hashes. It rejects missing/empty/malformed reports,
unknown or escaping paths, stale source identities, unauthorized authored
exclusions and changed implementation omitted from coverage.

Container Python paths are canonicalized before import. `/app` maps to `api`,
except the separately mounted `doc_renderer_service`; `/repo` is the repository
root. The report configuration uses coverage.py's path mapping rather than
blind string replacement. LCOV paths are repository-relative. Branch records
and line numbers must be valid for the checked-out source.

The scanner runs in a clean second job, downloads only this run's exact-head
artifact, and verifies it against the producer job's manifest digest before and
after scanning. No test or dependency-install step receives the Sonar token. The preflight script is
repository-owned and runs before the token is introduced; this is not a security
sandbox for malicious same-repository workflow changes. Those changes require
trusted human review before activation.
The scanner action and scanner binary version are pinned; signature verification
stays on. The scanner waits for the quality gate of its submitted task, rather
than accepting a historic project-wide green status.

The manifest is diagnostic provenance, **not a trusted autonomous approval
attestation**. A PR can edit repository-owned workflows and policies. Such
changes require human review, and any later approval system must enforce its
configuration from a trusted base and authenticate provider/run identity.

## Controlled cutover checklist

1. Require exact-head pre-PR and comprehensive CI before queue admission. Inspect
   all three reports, normalized paths and the manifest. Confirm release-policy
   and authored proxy/CLI files are indexed and have honest coverage entries.
   Keep every current required CI/queue/candidate control in place.
2. In the existing SonarQube Cloud project, record the organization key, plan,
   default branch/new-code baseline, current gate conditions, active profiles,
   effective analysis/coverage/duplication exclusions, small-change exemption,
   and GitHub binding. Confirm Free main/PR support and record merge-group branch analysis as unavailable.
   Verify there is no organization-level exclusion silently defeating scope.
   Verify hidden `.github`/`.claude` sources in the actual scanner inventory;
   repository-root scope alone is not proof that each language indexed them.
3. Confirm an appropriate existing analysis token can be used, or obtain the
   owner's explicit action-time approval before creating/configuring persistent
   CI access. The owner enters secrets directly through the approved secure
   flow; never paste them into chat, logs, a commit or command arguments.
   Store only the approved CI credential as `SONAR_TOKEN` and the verified
   non-secret organization key as `SONAR_ORGANIZATION`. No subscription or
   credential creation is authorized by this draft.
4. Schedule the switch with the accountable maintainer. Keep the current gate
   thresholds/profile unchanged for this input migration. Prepare a fresh
   exact-head report run and a main-branch baseline run. With this reviewed adapter merged and a fresh main workflow ready to run,
   turn off Automatic Analysis under Project Administration →
   Analysis Method only when the replacement can run immediately. Set `SONAR_CI_ENABLED=true`
   and dispatch CI on main immediately. Never remove
   protections to get through the switch.
5. Run main baseline and an exact-head PR scan. Confirm scanner task ID,
   completed analysis ID, repository/PR/head/base binding, report imports,
   indexed-file inventory and effective gate/profile. Check the authored release
   tools and `.github/scripts` in Sonar's file view, and inspect Python/TS/JS
   line and branch coverage. A completed upload alone is insufficient.
6. Validate fork and Dependabot PR behavior: ordinary Actions secrets are
   withheld for those events. The scanner excludes these PRs; after activation, the Free scope disposition
   fails explicitly for the unsupported credential route. It never claims
   a skipped analysis is a successful gate. Choose
   and approve a supported credential/analysis route before enabling this for
   those PRs, without exposing a token to untrusted code.

   The owner selected the Free adapter on 2026-10-06. Main and PRs targeting
   main receive actual analysis. Merge groups retain exact combined-head/base
   coverage evidence and an explicit unsupported-provider disposition, with
   existing queue gates preserved. They have no Sonar quality gate; never
   relabel a PR result or disposition job as a merge-group analysis.
7. Re-run negative probes: missing report, omitted changed source, stale SHA,
   tampered source/config/report and full-versus-affected input. A missing or
   inconclusive scan must fail closed. Confirm the replacement's GitHub check
   belongs to the expected Sonar App (ID 12526, `sonarqubecloud`) and exact head.
8. Only after successful live verification and maintainer review, remove the
   obsolete automatic-analysis configuration in a follow-up reviewed change.
   Keep actual approval/merge-queue activation separate.

Rollback if CI cannot produce a trustworthy analysis: stop CI submissions by
setting `SONAR_CI_ENABLED=false`, restore Automatic Analysis, record the failed
migration, and keep the PR/control change blocked. Do not claim the old green
badge contains coverage or covers the expanded source scope.

## Optional standalone Sonar CLI verification

Use an approved, pinned official standalone CLI in the cloud environment for
read-only verification. Do not install machine-wide Codex hooks or MCP settings
as part of this migration. CLI authentication requires its approved user token;
an analysis/project token is not automatically interchangeable. Never create or
save that credential without the required explicit approval and secure entry.

For an existing authenticated CLI, bind the explicit project and PR instead of
allowing a default-branch fallback:

```sh
sonar quality-gate status \
  -p Midtown-Technology-Group_bifrost --pull-request PR_NUMBER \
  --category coverage --format json
sonar list issues \
  -p Midtown-Technology-Group_bifrost --pull-request PR_NUMBER --format json
```

Verify the installed CLI's `--help` before using these commands. Retain the
analysis/task identity and exact SHA alongside the returned JSON. A current PR
query alone does not prove freshness, complete inventory or configuration
integrity. `sonar analyze` is local feedback and is not a substitute for the
CI scanner or test coverage import.

## Gate policy is a separate decision

After inputs and the baseline are trustworthy, the owner can review stronger
conditions, such as a proposed 90% new-code coverage target, at most 3% new
duplication, zero agreed new issues, and 100% new security-hotspot review. Also
review the default under-20-new-lines exemption. These are proposals, not
settings changed by this migration. Never self-mark a hotspot safe or weaken a
profile to obtain green. Confirm custom-gate plan entitlement before any change;
no upgrade or new subscription is authorized.

## Official references

- [Automatic-analysis limitations and coordinated switch](https://docs.sonarsource.com/sonarqube-cloud/analyzing-source-code/automatic-analysis)
- [Coverage import overview](https://docs.sonarsource.com/sonarqube-cloud/analyzing-source-code/test-coverage/overview)
- [JavaScript/TypeScript LCOV](https://docs.sonarsource.com/sonarqube-cloud/analyzing-source-code/test-coverage/javascript-typescript-test-coverage)
- [Pinned official scanner action](https://github.com/SonarSource/sonarqube-scan-action/tree/d209202bc7d53ff1cc128f7f907dac145c9d6ae9)
- [CLI local-analysis limits](https://docs.sonarsource.com/sonarqube-cli/analysis/analyzing-local-changes)
- [CLI commands](https://docs.sonarsource.com/sonarqube-cli/using-sonarqube-cli/commands)
