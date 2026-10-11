# Scoped CI recovery

This is the platform adapter for [shared scoped CI recovery](https://github.com/Midtown-Technology-Group/mtg-codex-skills/blob/main/skills/mtg-engineering-flow/SKILL.md#scoped-ci-recovery-and-final-acceptance). It is policy guidance only; it changes no CI enforcement, contribution admission, merge authorization or deployment control.

## Candidate and failure classification

- Preserve the original job logs, run/attempt, exact source SHA, actual base or merge-group tree, configuration/environment identity and affected-test-plan before acting. Classify the failure from evidence. An unexplained assertion failure is not a runner fault.
- On the same candidate, a verified infrastructure/transient runner failure permits the provider-supported rerun of the failed job and required downstream dependents, retaining independently valid successful checks. If the job graph cannot be recovered safely, rerun the smallest complete supported workflow.
- Any source/base/input change establishes a fresh candidate. Run focused diagnostics during development, then use the reviewed dependency-aware plan for all applicable final gates. Prior-source green statuses cannot qualify the new candidate.
- A test that passes on retry remains a known failure until diagnosed and durably resolved under the [testing protocol](../../.claude/skills/bifrost-testing/SKILL.md#6-known-failures-outside-the-scoped-run). Retain failing logs, order and concurrency evidence; never rerun until green or mask instability with retries, timeouts, skips or xfail.

## Platform gates and provenance

Before opening or queueing a PR, run `./test.sh pre-pr` on the exact clean candidate containing current `origin/main`. Existing successful-stage reuse requires identical clean source, environment/configuration identity and a proven unchanged actual base; failed stages never count. Focused diagnostic tests do not replace this gate or deferred required CI.

The current local stage cache does not bind the actual base SHA: its context hashes the affected plan, and the ledger records HEAD and environment identity. A base change can leave both HEAD and that plan unchanged. Until a separately approved runtime repair binds base identity, record the actual base SHA with the validation evidence and run `./test.sh pre-pr --fresh` whenever the actual base changes or you cannot prove it matches the base of the reusable stages. Do not infer unchanged-base identity from an identical plan or a prior pass. This is a documentation-level operating requirement using the existing `--fresh` option, not a claim that the cache already enforces it.

`api/scripts/plan_affected_tests.py` owns dependency-aware selection, including transitive consumers and contract boundaries. Review its `affected-test-plan` evidence. Unknown/unmodeled impact uses the full fallback. Shared configuration, lockfiles/dependencies, CI and selector changes are broad-impact inputs. Never edit selection, ownership, exclusions or gate conditions merely to obtain green. Planner or enforcement changes need their own explicit approval, review and regression coverage.

The final PR or merge-group candidate must satisfy every applicable gate against its actual base. Preserve required checks, review, contribution-admission controls and normal merge-queue authorization. A documented inapplicable lane is distinct from missing, pending or failed evidence.

For release artifacts and their consumers, verify source, immutable digest, producer workflow/run and attempt, build inputs and configuration. A partial retry cannot combine stale or incompatible evidence. Regenerate the complete producer set when the release contract requires one coherent run/attempt. Matching source alone is insufficient; a green PR does not authorize a release or deployment.

## Bounded recovery

Ordinary validation recovery stays within the approved outcome and needs no repeated general permission. Read back run state before dispatching after uncertainty; never start duplicate active attempts. Follow provider rate limits, Retry-After and documented backoff/retry budgets. During a confirmed provider incident, wait instead of repeatedly dispatching. Without a documented budget, allow one evidence-backed recovery attempt, then require a new diagnosis before another. Continue owning the outcome after a retry pause.

Ask before recovery expands security/access, spending, deployment effects or changes approval controls; hand off personal attestations and required human approvals. Do not weaken protections or manufacture sign-offs.

Examples: a lost runner on A may reuse valid lint on A and rerun its failed job/dependents; a race that passes unchanged remains blocking; a fix on B requires B's plan and gates; a lockfile or unknown dependency requires the comprehensive path; mismatched producer attempts require coherent regeneration; a lost dispatch response requires readback before any retry.
