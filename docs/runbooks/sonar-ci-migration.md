# Sonar CI coverage migration

## State and ownership

This is a **Lane 3, human-reviewed, report-only migration**. It does not enable
an approval producer, authorize queue admission, change required checks, dismiss
findings, or review security hotspots. The existing SonarCloud Code Analysis
GitHub App check and automatic analysis remain active during preparation.

`.github/sonar-ci.json` starts in `report-only` mode. CI executes the evidence
job and uploads reports; the scanner job is skipped. A successful evidence job
means the reports and their source identity passed local checks. It does **not**
mean Sonar analyzed those files or its quality gate passed.

The maintained `sonar-project.properties` applies only to CI-based analysis.
Automatic analysis ignores it and continues to use `.sonarcloud.properties`.
Sonar explicitly disallows simultaneous automatic and CI-based analysis for the
same project, so there is no safe indefinite overlap of the two scan methods.
Do not disable the current method before the replacement is ready to submit.

## What the evidence lane measures

- A clean, full-depth checkout of the PR's exact head SHA, the merge-group SHA,
  or the push/manual SHA, with its explicit base recorded. Existing CI still
  exercises its normal merge candidate separately.
- All Python unit tests, including slow unit cases, and repository Python tool
  tests. Pure tool tests run in the same locked Docker image from `/repo` with
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

Unit coverage is not evidence of E2E/server-process coverage. The lane does not
combine the existing permissive badge/server-process reports. A zero or absent
per-file report entry is never promoted to a hit. Unmeasured or unsupported
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

1. Keep the PR draft until exact-head pre-PR and comprehensive CI pass. Inspect
   all three reports, normalized paths and the manifest. Confirm release-policy
   and authored proxy/CLI files are indexed and have honest coverage entries.
   Keep every current required CI/queue/candidate control in place.
2. In the existing SonarQube Cloud project, record the organization key, plan,
   default branch/new-code baseline, current gate conditions, active profiles,
   effective analysis/coverage/duplication exclusions, small-change exemption,
   and GitHub binding. Confirm branch/PR support, including merge-queue branches.
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
   exact-head report run and a main-branch baseline run. Change the reviewed
   mode to `ci` and turn off Automatic Analysis under Project Administration →
   Analysis Method only when the replacement can run immediately. Never remove
   protections to get through the switch.
5. Run main baseline and an exact-head PR scan. Confirm scanner task ID,
   completed analysis ID, repository/PR/head/base binding, report imports,
   indexed-file inventory and effective gate/profile. Check the authored release
   tools and `.github/scripts` in Sonar's file view, and inspect Python/TS/JS
   line and branch coverage. A completed upload alone is insufficient.
6. Validate fork and Dependabot PR behavior: ordinary Actions secrets are
   withheld for those events. The current scanner fails closed without its
   credential; it must not silently report a skipped gate as success. Choose
   and approve a supported credential/analysis route before enabling this for
   those PRs, without exposing a token to untrusted code.

   Exercise a merge-group run on its combined head and actual base. If the plan
   cannot analyze that branch, leave the migration blocked and maintain human
   review; do not relabel an unrelated PR result as merge-group evidence.
7. Re-run negative probes: missing report, omitted changed source, stale SHA,
   tampered source/config/report and full-versus-affected input. A missing or
   inconclusive scan must fail closed. Confirm the replacement's GitHub check
   belongs to the expected Sonar App (ID 12526, `sonarqubecloud`) and exact head.
8. Only after successful live verification and maintainer review, remove the
   obsolete automatic-analysis configuration in a follow-up reviewed change.
   Keep actual approval/merge-queue activation separate.

Rollback if CI cannot produce a trustworthy analysis: stop CI submissions by
returning mode to `report-only`, restore Automatic Analysis, record the failed
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
