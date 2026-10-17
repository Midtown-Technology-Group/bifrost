# Workflow Start candidate and prepared fixture qualification

Package `R1-START/fixture-source/v1`, 2026-10-17. This extends the
[inactive Rust Start source](rust-core-mvp-workflow-start-source.md). It does
not qualify actual Start SQL, runtime admission, Rust result ownership or the MVP.

Current follow-on source is `30d36a6d40ed235716d5e5e6c9959662046a2159`; see
[the native claim integration correction](#native-claim-integration-correction).
The earlier `74bfef3e0` source/results below remain historical. Its Pending-root
fixture assumption is superseded; no previous check is transferred to new code.

## Source and review boundary

Platform main is `e42999395231ebc536711e5015422edfb75613a7`; workspace main is
`a8d3f9e0c8050d4f5fa507b246c3063bfb4d5b6d`. #1001/#1112 ancestry was reverified.
The [fresh committed-blob workspace audit](rust-core-mvp-workspace-boundary-a8d3f9e0.json)
checks 2,460 authored Python files: zero forbidden imports/parse errors and an
empty platform-internal allowlist. No workspace changes are required.

Candidate `74bfef3e046bedf94f3c757fe17660fbc51c5976`, branch
`rust/workflow-start-fixture`, worktree
`/home/thomas/src/bifrost-rust-workflow-start-fixture`. [PR #1320](https://github.com/Midtown-Technology-Group/bifrost/pull/1320) is
ready for review. Its stacked base is the unmerged R1 candidate `560e3decd7a18200809edb9bf9a078ede5d06ea5`,
not a landed schema. Twelve owned paths include the six prior Rust Start files,
five Python test-helper/test files, and the Rust Dockerfile. No migration, public
endpoint/SDK, workflow consumer, issuer or runtime callsite changes.

The Rust operation and opt-in SQL acceptance source are byte-identical to `6793d750e`.
The original cancellation helper's only changed definition is
`_seed_native_history`: private timeout accepts exactly int 30 or int 0, rejects
bool, and writes the same value into both the Workflow and immutable deployment
manifest. Default 30 preserves existing cancellation callers. All its other
function/class ASTs, including the Running/Start constructor, are unchanged.

New test-only `common_start_fixture.py` constructs two distinct synthetic
Pending/claimed cohorts with open sessions and no Start/admission/grant/report
or resource evidence. Native parent construction is separate from the common
stage. Explicit native-owner reads verify actual immutable pins; the restricted
core stage uses source/execution fences and the reviewed current schema. Private
0600 intents are durable before mutation; no-overwrite final output follows both
commits and independent native/core readback. Any failure preserves files and
rows for diagnosis; no automatic reset, retry, reopen or ownership transfer.

Prepare/custody/artifact hashes remain synthetic and non-runnable. They cannot
satisfy live admission, physical custody or source-retirement evidence. The finite
cohort's deadline is 30 seconds from its sampled PostgreSQL clock, before setup:
precompile/warm the SQL consumer before generation, preserve the original clock,
and fail rather than refreshing an expired cohort. Each direct/pool run needs a
new disjoint cohort and separately approved execution envelope.

Independent Sol source reviews accepted exact `6793d750e` (Start), `a52d68bf8` (producer), and `74bfef3e0`
(the three-line Docker pure-test addition). Reviewers performed no imports,
builds or SQL. Locked Ruff 0.15.12, format and diff checks pass. The fixture driver
has explicit native/common phases and DSNs. It creates no schema or role, issues
no SDK credentials, and launches no runtime work.

## Supported evidence

Environment: pve-t340, VM101 `bifrost-platform-test-debian13-01`, supported Docker
lane. PVE performed only static Git/AST/hash/locked Ruff work. Native fixture
qualification used clean guest checkout
`/home/dev/bifrost-rust-start-fixture-a52d68bf8`, project `bifrost-test-f527ace6`.

The selected command passed 34 unique cases, zero failures/errors/skips:

```sh
./test.sh tests/unit/test_common_start_fixture.py \
  tests/unit/test_common_cancel_fixture.py \
  tests/unit/test_common_cancel_fixture_driver.py \
  tests/e2e/platform/test_common_cancel_fixture_native.py \
  tests/e2e/platform/test_common_start_fixture_native.py -q --no-cov
```

This includes actual native Alembic PostgreSQL checks of immutable timeout 30/0
pins and existing native namespace behavior. It does not run the common stage.
All five Python inputs and normal Python build inputs remain unchanged from `a52d68bf8`
to `74bfef3e0`. The `a52d68bf8` literal pre-PR pass remains historical source-bound evidence,
not a transferred `74bfef3e0` pre-PR result.

The exact final `74bfef3e0` checkout `/home/dev/bifrost-rust-start-fixture-74bfef3e0`,
project `bifrost-test-b67753bd`, passed:

```sh
docker build --target checks -f core-rs/Dockerfile \
  -t bifrost-start-74bfef3e0:checks core-rs
BIFROST_FORCE_PORT=1 ./test.sh pre-pr
```

The actual Docker checks execute pinned fmt, strict all-target/all-feature
Clippy, 36 default tests and 7 candidate-feature library tests. Five tests overlap:
38 unique Rust identities, zero failures or ignored tests. Common SQL tests
compile only. All 44 tracked Rust inputs, including the changed Dockerfile, match
host committed blobs.
The exact `74bfef3e0` pre-PR ledger marks repository/client/quality/generated complete on
that head; its comprehensive planner leaves broader suites for normal hosted CI.

The stacked base is outside automatic CI triggers. Existing supported manual
entry points were invoked once each after checking no branch runs existed:
[normal CI 39004360577](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39004360577)
and [Rust Core 39004362524](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39004362524),
both attempt 1 at exact `74bfef3e0`, queued at publication. No filter, workflow, threshold
or required-gate change; no retry, waiver or full-CI success claim.
CodeRabbit skipped automatic review on this non-default base; one normal
[manual review request](https://github.com/Midtown-Technology-Group/bifrost/pull/1320#issuecomment-6229908763)
was posted. No automated review acceptance is claimed.

| Final `74bfef3e0` evidence | SHA256 |
| --- | --- |
| Docker Rust checks | `adb0c22d1dc9c537051417d7a30e95d1ef9a2cf12b85832e6527dd9f8188b910` |
| Literal pre-PR log | `966f56e38e6012680d4600c7c63cfb0a0cc325e646fbea6f39a1afb1f0bf1816` |
| Private guest evidence manifest | `ffa251a38fb408bf22b25a3e226ff1f03cf84094c66bfae75e9e4f9d1ee93559` |
| Source qualification receipt | `fa714c0192caa6c84637958d3faa963bfa0627d161a77209ddfc450ea06f7e3c` |
| Cleanup receipt | `cc8e3026714bd0c7e1374b47a71603239f8553aa9ca456c67c220616848006a5` |

Private raw evidence is under
`/home/thomas/src/bifrost-rust-mvp-artifacts/start-fixture-a52d68bf8` and
`start-fixture-74bfef3e0`. Raw logs/private intent material do not belong in Git.
The `a52d68bf8` selected JUnit SHA256 is
`91eeafd3c166404dcb31a2fa991b49b43030476ec25c054ab50b90e7c4063700`;
its literal pre-PR log is
`364d8bacdff18c3b8e66956f0f99f7d2ae4e20da682b83dab2dba8fdc1093251`.

## Scope, cleanup and next product gate

Both task projects were torn down with their normal launcher. Container-create
mount receipts captured 20 events at `a52d68bf8` and 3 at `74bfef3e0`, with zero misses. Residual
anonymous volumes were removed only after matching captured metadata, excluding
the baseline and checking no container references. Final baseline equality is 143
stopped containers and 2,583 volumes; no task containers/networks/volumes remain.
The 16 previously unattributed volumes remain preserved within that baseline.

Pinned R1 Python/Rust images and qualified native fixture image `38bcdf191` remain. The
normal `a52d68bf8` build replaced the prior unqualified shared dev cache index `e3fe8496b`; its
restoration attempt failed because the old index was no longer present. No
restoration success is claimed. Final `74bfef3e0` pre-PR produced shared dev index `f21d6c748`;
it is mutable cache, not new experiment authorization. One task-owned Rust
checks image was removed with `--no-prune` only after producer-digest verification
and an empty container-reference check, freeing export storage. No global prune,
pre-existing container/volume removal or frozen-image replacement occurred.
A `74bfef3e0` setup-only wrong image alias failed before product checks; the corrected
checked-in alias was recorded before the first build. Neither orchestration
failure was a retried product failure.

Private evidence is archived on PVE. No running containers or named build/test
processes remained. VM101 was gracefully shut down and independently read back
`stopped`.

The frozen R1 source 560 and operator runbook SHA256
`9322bea3681afaecd19cbdc8df0bdbac36cab422ef4ef4ea23e2f50b3439cd86`
remain unchanged. Its explicit isolated authority decision is still pending.
No common DDL/roles/logins/grants, actual Start SQL, SDK issuance or runtime
execution has occurred. R1 cancellation approval would not automatically grant
this separate Start experiment.

The next product dependency remains real installed-principal and cancellation
qualification. Start then needs an exact separately reviewed SQL experiment,
full coordinator admission and lost-commit acknowledgment fault injection.
Result ownership, unchanged Python workflow/agent consumers, coexistence,
physical/resource/source closure and routing rollback remain unproved. No MVP
CONTINUE/STOP decision, production admission, profile freeze, merge or deployment.

Codex/Sol authored the source with independent Sol review. Existing Go Start
source attribution and AGPL-3.0-only provenance remain in the Rust implementation;
no accepted Go source/evidence was modified. Procedures: engineering flow and PR
stewardship 2026-10-06.1; router 2026-10-01.1; package 2026-10-09.1-b3560edd5d13f1a7.

## Native claim integration correction

Current [PR #1320](https://github.com/Midtown-Technology-Group/bifrost/pull/1320)
head `30d36a6d40ed235716d5e5e6c9959662046a2159`, still based on frozen 560.
This correction preserves the existing public projection; no runtime API change
or activation follows. Platform main e429 and workspace a8d3f9e0 remain unchanged
from the earlier exact fetched/audited baseline.

Source inspection found that
`api/src/jobs/consumers/workflow_execution.py:_claim_durable_execution` commits
Execution Running while the typed attempt is claimed/claim and both native
start clocks are null. The existing Rust domain claim plan also returns logical
Running. The earlier Start primitive and synthetic cohort instead required
Pending, so those individually qualified components did not compose. No common
SQL or runtime had been activated with that assumption.

The corrected inactive operation requires the native Running/null-clock root
and claimed/null-clock typed attempt. It preserves the root status and commits
its first clock, typed execution phase/clock and immutable Start in one database
transaction. No-Start observation uses the same native precondition; absence
still grants no replay, spawn, replacement or owner-transfer authority. Synthetic
producer readback and opt-in SQL initial/rollback/clock assertions now match it.
No public SDK/runtime/workspace accommodation, migration or privilege changed.

The new `api/tests/e2e/platform/test_workflow_delivery_admission.py` has five
real-PostgreSQL cases: Pending/Scheduled claim to Running/claimed with no start
clocks and ordinal 1; duplicate no new attempt/clock rewrite; expired/wrong-token
rejection without mutation; active-attempt failure rolling back the transport
started marker. It calls the real native admission method and production
commit/rollback context. Only the connection factory and inert pool identity are
selected for the test. Independent review found and repaired an initial wrapper
that omitted production's post-yield commit before any test execution. No pool,
broker, child or workflow executes. These are ordinary native claim fixtures,
not deployment/tenant/SDK or selected Solution-runtime acceptance.

Independent Sol scoped SOURCE ACCEPT exact `30d36a6d4`; no reviewer execution. GitHub
[COMMENT review 5556354178](https://github.com/Midtown-Technology-Group/bifrost/pull/1320#pullrequestreview-5556354178)
also references exact `30d36a6d4` and finds no confirmed introduced defect. It was
published through MTG-Thomas and signed Eddie; infer no personal human approval,
SQL permission or activation from this account/signature.

Supported VM101 checkout `/home/dev/bifrost-rust-start-claim-30d36a6d4`, project
`bifrost-test-8d51481e`, passed the actual Docker `checks` target, 44 selected
native/helper cases, and fresh literal `BIFROST_FORCE_PORT=1 ./test.sh pre-pr`.
The selected command adds the five new cases and existing workflow recovery /
stale-handler fencing cases to the earlier 34. All 44 identities are unique with
zero failures/errors/skips. All 38 unique Rust source tests pass (43 executions
including five overlaps), fmt and strict all-target/all-feature Clippy pass;
common SQL acceptance only compiles. Repository/client/API-quality/generated
pre-PR stages are complete for exact `30d36a6d4`. Its comprehensive plan leaves broader
suites to required hosted CI. PVE performed no application imports/tests/Cargo.

The exact focused command was:

```sh
./test.sh tests/unit/test_common_start_fixture.py \
  tests/unit/test_common_cancel_fixture.py \
  tests/unit/test_common_cancel_fixture_driver.py \
  tests/e2e/platform/test_common_cancel_fixture_native.py \
  tests/e2e/platform/test_common_start_fixture_native.py \
  tests/e2e/platform/test_workflow_delivery_admission.py \
  tests/e2e/platform/test_postgres_work_delivery.py::test_workflow_recovery_obeys_durable_domain_outcome \
  tests/e2e/platform/test_postgres_work_delivery.py::test_recovery_resumes_unstarted_claim_and_fences_old_handler \
  -q --no-cov
```

All 44 committed Rust inputs and six changed Python inputs match guest hashes.
The six native reference implementation/model files are byte-identical to
main e429; unchanged Python remains the oracle. Private raw receipts are under
`/home/thomas/src/bifrost-rust-mvp-artifacts/start-claim-30d36a6d4`.

| Exact30d evidence | SHA256 |
| --- | --- |
| Docker Rust checks | `08e01f840fc5849f20599ba5be9dcf5d822e61544afe266c53f6c946b264771d` |
| Selected 44 JUnit | `90e7b5cb6933770dd5e1b00b49c2abb1e986d40ac67edfc0a1d910493ba6d7b1` |
| Literal pre-PR | `30122f12b8fb3548655085279505f097bb25f2d4b17803982e363e50afbe30f4` |
| Source qualification | `885237e8a162024b148dbfd02b6f1000665b2435c212d1f718338842b96f6b29` |
| Guest evidence manifest | `ee79d8f6a78ed6eadbe476726c35f642dc9390f976a3935e6c6cf677d7350cf6` |
| Cleanup | `116a5a4079746ecd4dab637b8d5bb3d129b80ba90e754ec2c6cfcff1eb18b48a` |

Old 74 CI 39004360577/Rust 39004362524 received one normal cancellation each and
are terminal cancelled. Two Rust jobs had started before cancellation; no result
is carried to changed source or treated as a failure waiver. Metadata is retained.
After verifying no current-head campaign existed, normal supported manual
[CI 39005963999](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39005963999)
and [Rust 39005965484](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/39005965484)
were accepted once, attempt 1/exact `30d36a6d4`, queued at readback. No workflow, selector,
threshold, test expectation or required-gate changes. No hosted-CI pass claimed.

Cleanup captured 20 container-create mount receipts with zero misses; resource
exclusion readback found no baseline project overlap. Normal stack down and
identity-checked remaining-volume cleanup restored exactly 143 stopped containers
and 2,583 volumes. Pinned R1 Python/Rust and qualified 38 images, shared Go cache,
and the 16 previously unattributed baseline volumes remain preserved. The checked
Rust image alone was retired with producer-digest verification and `--no-prune`.
No running containers/named build-test processes remained. VM101 was gracefully
shut down and independently verified stopped; private shutdown readback is retained.

Transport authority remains a separate blocking interface. Native claim locks
execution/domain before checking its current WorkDelivery token/expiry and
setting transport started in the same transaction, then claims the typed attempt.
The current four-role R1 core proposal does not grant work_deliveries access;
effective installed authority remains unverified. A typed claim and a prior HTTP/worker check cannot replace this gate.
A future selected-workflow admission adapter must preserve its authority, binding,
renewal/interruption and transaction order before any runtime callsite; no generic
delivery rewrite or extra privilege is authorized here. Cancellation/historical
observation must still work after transport ownership expires, so do not impose
a blanket live-lease requirement on every common operation.

Frozen R1 source 560/runbook 9322 remain unchanged and its approval question remains
pending. This source correction does not expand that request to Start, delivery
privileges, SDK issuance or a runtime experiment. Common SQL/roles/logins, actual
Rust Start/result, unchanged workflow/agent execution, events, physical retirement
and routing rollback remain unproved. No MVP decision, merge or deployment.
