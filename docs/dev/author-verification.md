# Author verification before review

Use this loop while editing. `./test.sh pre-pr`, required CI, reviews, and
candidate provenance remain the final gates. An import graph helps find tests;
it cannot prove that every consumer was found. No result here proves production
acceptance. Use the dedicated Linux test VM or CI; never a production database.

## Trace the boundary first

Put these five short lines in your working notes or PR description before coding:

- **Change:** observable behavior before → after, and compatibility decision.
- **Callers:** direct callers and downstream consumers (including string/HTTP
  dispatch, preview, rename, stylesheet and SDK consumers); name their tests.
- **State:** persisted identities, credentials, references and side effects;
  name the test that reads them back after commit.
- **Exits:** refusal, provider error, cancellation, partial reads, cleanup and
  one deadline budget; name the tests that distinguish each relevant exit.
- **Delivery:** mixed-version implications, migration/rollback or revert limits,
  and any acceptance boundary still needing runtime or external-provider proof.

Keep irrelevant entries to “not applicable: reason.” A test filename alone does
not show which assertion protects the changed behavior.

## Focused loop

```bash
git fetch origin main
./test.sh author --plan
./test.sh stack status
./test.sh stack up                    # only if this worktree's stack is down
./test.sh author --risk sdk --boundary 'Real production Azure adapter; pinned SDK autospec transports; no Azure service'
./test.sh author --risk database --risk concurrency --boundary 'Real PostgreSQL/asyncpg and Redis; no external provider'
./test.sh author --risk contracts --risk consumers --boundary 'Real DTO/inventory generators and compiler consumers; test-specific provider stubs'
```

The command reuses `api/scripts/plan_affected_tests.py`, includes dirty and
untracked work, and runs changed tests together with graph-owned consumer tests.
Backend unit neighbors run together with workers paused; integration tests run
in their own batch under the existing E2E lifecycle. Explicit risk mappings add edges the import graph cannot establish. Extend
`RISK_PATHS`/`RISK_TESTS` with the smallest named boundary when a new invisible
edge appears; do not replace ordinary graph ownership with a universal map.
`--test tests/unit/path.py::test_name` or `--test client/src/path.test.tsx`
adds an author-traced consumer. Missing mapped files fail selection.

`--risk security` exercises real authentication, tenant isolation and persistence
while embedding calls are stubbed; no embedding key or provider setup is needed.
`--risk deadline` exercises timeout cancellation and resumption of the accepted
run ID. Security, deadline, concurrency and contract risks also run existing
API pyright/ruff checks. Contract risks check the operation inventory and DTO
version tripwires, generated CLI/OpenAPI/SDK documentation, TypeScript OpenAPI
output and skill mirrors. Generated checks compare temporary output, never
silently rewrite committed artifacts. Use the normal generators to repair drift
and make the breaking-versus-compatible decision explicitly.

A comprehensive planner result stays comprehensive. This command runs the
focused tests it can name and records broad lanes as outstanding acceptance
work; it does not execute the entire suite or turn those lanes into passes.
For unknown behavior, trace and add tests before relying on the focused result.
Selector, dependency, container, CI and shared harness changes still require
broad validation under the existing plan. Check neighboring tests together to
expose leaked namespaces, obsolete fixture signatures and changed call counts.

Stack startup, resets and Docker builds can dominate local wall time. Receipts
record each command's wall time and JUnit case time; compare both. Preserve a
borrowed stack. Tear down task-created disposable stacks from their original
worktrees with `./test.sh stack down`, then verify project containers, networks
and volumes are absent. Do not globally prune Docker or reset other worktrees.

## A regression must discriminate

Run **the same test code** against a declared old implementation and the fix in
two disposable worktrees on the test VM. Commit the candidate first. Choose the
actual bug-introducing parent/base, rather than assuming today's main is broken.

```bash
fix=$(git rev-parse HEAD)
base=$(git rev-parse '<reviewed-old-ref>^{commit}')
root=$(mktemp -d /tmp/bifrost-red-green.XXXXXX)
git worktree add --detach "$root/red" "$base"
git worktree add --detach "$root/green" "$fix"
# Copy ONLY the regression and any strictly necessary test-only helpers from
# green into red, retaining identical bytes. List/hash every overlaid file.
# Example (replace with the actual regression path):
file=api/tests/unit/services/test_azure_sdk_boundary.py
mkdir -p "$root/red/$(dirname "$file")"
cp "$root/green/$file" "$root/red/$file"
sha256sum "$root/red/$file" "$root/green/$file"
(cd "$root/red" && ./test.sh stack status && ./test.sh stack up)
(cd "$root/green" && ./test.sh stack status && ./test.sh stack up)
# Run individually; a nonzero red exit is expected and must be inspected.
(cd "$root/red" && ./test.sh tests/unit/services/test_azure_sdk_boundary.py -v)
(cd "$root/green" && ./test.sh tests/unit/services/test_azure_sdk_boundary.py -v)
```

Worktree paths give distinct Compose projects, isolated networks and cloned
PostgreSQL state. Use only `docker-compose.test.yml`; do not copy application
`.env` files, set remote database URLs or point at debug/production services.
Test-runner credentials are fixed synthetic test credentials. External provider
calls must be stubbed for this comparison. Older harnesses that require live
credentials are an exception to document, not a reason to use production.

Copy both JUnit reports printed under `/tmp/bifrost-<project>/test-results.xml`
before teardown. Record the exact SHAs, overlay hashes and commands. Inspect the
**behavioral assertion/exception**, then check the named pair:

```bash
python3 api/scripts/check_regression_evidence.py \
  --red /path/to/red.xml --green /path/to/green.xml \
  --test 'tests.unit.services.test_azure_sdk_boundary::test_chunked_eof_closes_transport_owner' \
  --expected '<specific behavioral diagnostic observed on old code>'
```

The checker rejects missing/duplicate names, collection/setup errors, imports,
syntax failures, skips, an unexpected old failure and a failing fixed case.
It does not certify the diagnostic's meaning: explain why it proves the bug.
A runtime `AttributeError` at an invented SDK method is behavioral evidence;
an import failure before reaching the adapter is not. If old and fixed both
pass, report characterization coverage, not red/green evidence.

Before removing either worktree, run `./test.sh stack down` in it and verify its
project's containers/networks/volumes are gone. Keep reports outside the removed
worktrees. Use a shell cleanup trap for unattended runs; on interruption, inspect
for a surviving named pytest runner before removing the worktree.

Exceptions must name the reason and substitute evidence: a genuinely new
feature, a harness incompatible with the old implementation, or an existing
characterization case that already passes both. Never change production code
on the old side, weaken assertions, add skips or call import errors “red.”

## Reusable boundary patterns

- [`test_azure_sdk_boundary.py`](../../api/tests/unit/services/test_azure_sdk_boundary.py)
  uses `create_autospec(..., spec_set=True)` against installed pinned Azure SDK
  classes. It exercises the production adapter through read-all, bounded EOF,
  partial close, cancellation and provider errors. Cleanup belongs to the
  transport owner. A parallel test-only adapter would miss its finalizer.
- [`postgres.py`](../../api/tests/helpers/postgres.py) creates a UUID database
  using only the test Compose PostgreSQL hostname and synthetic credentials,
  and drops that exact database on every exit. The populated OAuth migration
  example preserves identities, private/global tokens and references through
  upgrade/downgrade and a conflicting transaction rollback. It deliberately
  exercises one historical migration against a minimal historical schema;
  it does not claim full-chain migration or active #1205 acceptance.
- [`test_authoring_concurrency.py`](../../api/tests/e2e/platform/test_authoring_concurrency.py)
  starts real production heartbeat and completion transactions, observes the
  actual PostgreSQL blocking edge, then flushes logs with both foreign-key locks
  before committing. It asserts persisted logs and terminal state. Extend the
  scenario for stop/expiry/claim changes; isolated lock-order calls omit implicit
  locks. Capture UUID/scalar identities before expiration; read state in a fresh
  session. Never infer persistence from an awaited mock call.
- [`test_authoring_authorization.py`](../../api/tests/e2e/api/test_authoring_authorization.py)
  uses real signed request authentication, real tenant rows and PostgreSQL
  queries with a constrained embedding stub. Anonymous/foreign-scope requests
  must fail before any provider call. Assert concrete row IDs/orgs and unchanged
  stored content; permissive empty mock results can hide a forbidden query.
- [`test_authoring_deadline.py`](../../api/tests/unit/sdk/test_authoring_deadline.py)
  coordinates request entry/cleanup with events, checks bounded cancellation,
  and resumes the existing ID without enqueueing again. A transport refactor
  that separates final-result retrieval must keep it inside the **same** remaining
  budget as polling and cleanup; add that final-fetch case with the refactor.

Keep monkeypatches local and restore exact `sys.modules` entries and `sys.path`
changes. Do not remove entire unrelated module trees to clean one fake package.
The focused command batches neighbors; it cannot automatically discover all
order-sensitive pairs, FK cycles, or external service behavior.

## Receipt

Each run writes a unique `author-verification/<id>/receipt.json` under this
worktree's Git metadata. `--plan` runs nothing and earns no evidence. Backend
JUnit is copied into that receipt directory before another invocation can
replace it. Client commands currently require their native report for named
cases/skips; the receipt records that as a gap. Skipped backend cases produce an incomplete result and a nonzero exit. Exit zero
means the selected commands completed, never that deferred gates passed.

For a PR or handoff, summarize the JSON and add the human context:

```text
Head / base: <full SHAs>; dirty: <yes/no>; changed-content SHA256: <digest>
Environment: <host, VM ID, worktree, Compose project; pre-existing or task-owned>
Contract trace: <five short lines above>
Executed: <exact commands and named cases, outcomes, wall/case times>
Skipped / unrun: <names, reasons, queued/deferred gates; never counted as passes>
Boundaries: <real auth/DB/adapter; mocked provider/transport; live gaps>
Regression: <red + green SHAs/reports/signature, or explicit exception>
Failures: <introduced / inherited / unexplained, with source evidence and owner>
Acceptance gaps: <required CI, live provider, migration chain or lifecycle gaps>
Cleanup: <verified teardown, or retained owner/reason/checkpoint>
```

A pass on base and failure on head suggests an introduced regression only when
test bytes, environment and execution order match. A failure on both can be
inherited; explain the cause and blocking disposition. An unexplained failure
stays unexplained. Do not fish for green with retries or label it flaky.

## Evidence informing this slice

Current main was inspected at `e42999395231ebc536711e5015422edfb75613a7`.
The current review batch was read before implementation, including later repairs:
[#1312](https://github.com/Midtown-Technology-Group/bifrost/pull/1312) SDK shape;
[#1205](https://github.com/Midtown-Technology-Group/bifrost/pull/1205) populated migrations;
[#1218](https://github.com/Midtown-Technology-Group/bifrost/pull/1218) whole-transaction locks;
[#1180](https://github.com/Midtown-Technology-Group/bifrost/pull/1180) credential-free auth proof;
[#1212](https://github.com/Midtown-Technology-Group/bifrost/pull/1212) deadlines/contracts;
[#1185](https://github.com/Midtown-Technology-Group/bifrost/pull/1185) ORM expiration;
[#1202](https://github.com/Midtown-Technology-Group/bifrost/pull/1202) namespace cleanup;
[#1223](https://github.com/Midtown-Technology-Group/bifrost/pull/1223) async persistence;
[#1184](https://github.com/Midtown-Technology-Group/bifrost/pull/1184),
[#1197](https://github.com/Midtown-Technology-Group/bifrost/pull/1197),
[#1200](https://github.com/Midtown-Technology-Group/bifrost/pull/1200) downstream consumers.
These are design evidence, not claims that each finding is still open or that
this slice repairs those PRs. Review uses the MTG engineering-flow and
[MTG Brooks review](https://github.com/MTG-Thomas/brooks-lint/blob/bafbef6df6578a4e5da0b52abf6d669c974cf604/skills/brooks-review/SKILL.md)
guidance. No cross-repository changes are included. A separate small
mtg-codex-skills proposal could point generic authoring guidance at a repository's
contract trace and receipt convention; it should not copy BiFrost test commands.
