# Common Rust control plane: Python and Go reconciliation

Canonical coordination and decision record, 2026-10-16. Status: source/evidence
reconciliation, **not a schema/profile freeze or implementation release**.
This record supersedes the separate common-schema proposals as the next-work
entrypoint. Do not build parallel lifecycle tables, locks or credential issuers.

## Owners and authority

Thomas authorized these existing owners to coordinate. No new agents, threads,
experiments, merge, deployment, release, expanded credentials or profile approval
are authorized by this division.

| Workstream | Accountable existing owner | Immediate deliverable |
| --- | --- | --- |
| Common control plane | Rust MVP thread `import:codex-imported:01a0f5db-8f14-7630-80b7-502c8288cae3`, pve-t340 | Consolidated schema/authority/lock decisions; #1018 and existing gates; Python extraction/parity; native retry/delete/recovery compatibility; installed writer/catalog proof |
| Go compatibility and developer experience | Go spike thread `import:codex-imported:01a0fa30-a29f-7323-8e64-72efdcc0d5b5`, pve-t340 | Compatibility/reuse map against this record first; Go SDK/runtime/build/local supervisor/manifest work only after the relevant common seam is settled and authorized |
| Shared recovery requirement | Common-control-plane owner, with Go compatibility evidence | General partial-cleanup recovery with preserved identity and no replay; not a Go-specific workaround |

This file has one editor: the common-control-plane owner. The Go owner returns
its mapping as a separate accessible artifact; the common owner reconciles it
here. Keep accepted Go code and evidence unchanged. A transport receipt proves
message delivery, not completion of a requested mapping.

## Exact source and evidence

| Source | Immutable reference and accessible location |
| --- | --- |
| Platform main used for this review | `e42999395231ebc536711e5015422edfb75613a7` |
| Workspace boundary | `0b70c082bfa1a3c65b1ead8661774dd5d4ae36a1`; 2,449 committed authored Python files, zero internal src/SQLAlchemy imports, no parse errors, empty allowlist |
| AUTH-P1 #1018 | `39245315e193e1d04886044677bf7a75bb8c6f1f`, branch `fix/agent-original-caller-authority`, `/home/thomas/src/bifrost-agent-caller-authority`; current main incorporated |
| Common decision worktree before this record | `accd464ba99b9cfc79705ae427c8e5dfd04c8bf7`, branch `docs/rust-mvp-verification-disposition`, `/home/thomas/src/bifrost-rust-mvp-verification-disposition` |
| CRED-P1 #1037 | `3918d608a2d497d1ca8424fd4229d191323e2d7a`, `/home/thomas/src/bifrost-runtime-sdk-ingress`; separate/unmerged, not automatically replaced by the Go subset |
| Go implementation candidate | `56d1fb006d220b9dbb0747f1cca4def18677f918` |
| Go evidence head | `a47586732ef1b2523b9119b5056d83574ccdd779`, branch `spike/go-first-class-g0`, `/home/thomas/src/bifrost-go-first-class-spike` |
| Go SDK | `0.0.0-spike.2`; workflow SHA-256 `5f9c912ec6055882e0d443378cb93bb87b58eb7ab2a49eb5e14d338d72865edd` |
| Go archive | `sha256:9ac79cd99e41b10bd07613ff54aca5e50a2c7f5d2326da83064bedf9060a8e5a` |

The accepted Go packet is
`/home/thomas/src/bifrost-go-spike-artifacts/56d1fb006d220b9dbb0747f1cca4def18677f918/`.
Its `thomas-bounded-acceptance.json` records bounded human acceptance and
CONTINUE SPIKE, not production/full adoption/profile freeze. The original
`final-isolated-proof.json`, `owner-current-review.md`, `completion-audit.md`
and `decision.md` remain historical immutable evidence at the pinned head.
Their earlier pending-acceptance wording is reconciled by the supplemental
acceptance record; do not rewrite them to imply broader approval.

Root read the acceptance, decision, owner review and final proof, inspected the
common ORM, migration identities and Rust lock function, and independently
rehashed all nine files in final proof's retained_hashes: no mismatch. Final
proof SHA-256 is `4b24b8e2efb8ca33b7c10f37f94011ba19ddc63dbc1bdbe577c2c09166276ede`.
This does not mean root independently repeated all Go tests or reviewed every
implementation line. Nine completed same-binary cases and the tenth negative
remain evidence for their exact candidate and isolated environment.

AUTH-P1 manual CI 38947574111 has passed literal pre-PR and backend unit at the
last observation; full PR/manual CI, images, shards, MCP and coverage were still
incomplete. Required-check success is not full gate acceptance. Keep current
run readback authoritative and retain old pull-limit failures at their old head.

## Implementation baseline and compatibility gaps

The Go spike already implements common mechanisms. Prefer reconciliation and
reuse over reconstructing equivalent unproved machinery. Reuse is not approval
to import the whole spike into main or to rename a spike as production core.

| Concern | Existing Go candidate | Common target / required reconciliation | Owner |
| --- | --- | --- | --- |
| Schema vocabulary | runtime_execution_owners, runtime_sessions, typed runtime_starts, runtime_report_receipts, runtime_admissions, runtime_resource_settlements | Use these implemented records as the comparison baseline. Retire the proposal for parallel runtime_workflow_* tables. Review exact additive changes only after compatibility decisions. | Common |
| Deployment/artifact | Immutable workflow/Solution/native-artifact association; actual archive/build/adapter evidence | Preserve source versus executable versus archive identity. Map Python interpreter/dependency/SDK/materialization evidence without native-only assumptions. | Common contract; Go mapping |
| Native links and deletion | RESTRICT live FKs and immutable association/receipts, including reviewer/user references | Preserve native form purge, user/CLI/workflow SET NULL and existing Python behavior. Distinguish immutable historical identity from detachable native live links. Do not copy RESTRICT wholesale or apply an unreviewed tombstone policy. | Common |
| Attempt/session/retry | One runtime session per typed workflow attempt; permanent close | Proven no-effect, trusted-reap, native-policy-eligible NEW-attempt retry can coexist with this rule. Verify actual attempt counting and pre-Start claim recovery; neither blanket retry prohibition nor same-attempt replacement is implied. Possible Start never replays. | Common |
| Locks | Shared source fence → typed attempt → NOWAIT owner/root/deployment/Solution → session → ordered Start/admission/grant/operation/receipt/resource rows | Review together with native execution-first advisory/poison/recovery and CRED attempt-first issuance, including FK/trigger/table/advisory waits. NOWAIT on selected rows is not proof of the combined graph. | Common |
| Writer exclusion | Distinct authenticated wex_core/wex_incumbent fixture principals, OLD/NEW and dependent/ancestor checks, isolated race/effect suites | Reconcile exact guarded paths, finite ancillary/unlink operations, public reads and ANY-table-lock disposition against current main. Disposable fixture grants are not production provisioning or installed-catalog proof. | Common; Go supplies exact cases |
| Credentials | Existing dedicated audience/purpose foundation, finite attempt/session grant, live close/provision/release; accepted Go scope has no renewal | Preserve approved CRED two-operation policy and non-upgrading renewal where reference requires it. Map original/effective org, actor/OAuth/null/default/install behavior. No broad SDK grants, second issuer or silent loss of Python positives. | Common; Go supplies capability map |
| Result and source drain | Rust durable receipt/projection, fenced Result/Cancel, retained resource settlement fact | Reuse exact identity/receipt rules; map Python logs/resources/usage/summary and current native source inventory. Terminal or Stopped alone is not physical/source retirement. | Common |
| Partial cleanup | Tenth negative remains Cancelling/null, grant revoked, no resource fact, source pinned | Retain the negative. General resumable cleanup/crash recovery is shared future work; no replay, missing-file retirement inference, owner reassignment or false completion. | Common with Go conformance |
| Protocol/SDK/runtime | Separate trusted adapter and ordinary Go workload; independent codecs/probe | Compare complete selected profile and existing P0 framing; do not infer freeze from bounded acceptance. Python remains a supported execution runtime, unchanged public SDK/workspace boundary. | Common protocol; Go adapter mapping |

Current source facts supersede old findings: poison imports
WorkflowExecutionAttempt as ExecutionAttempt (typed, not generic); source
accounting includes active typed attempts; SDK pending changes acknowledge Redis
after caller commit. A still-live runtime session after logical terminal remains
a distinct source-consumer concern. Preserve these corrections in the mapping.

## Migration collision: resolve before consolidation

Both candidates define `workflow_runtime_sdk_grants` and
`workflow_runtime_sdk_grant_operations`:

- CRED-P1 migration `20261001_runtime_sdk_grants`, parent
  `20261001_solution_src_account`, with models in `runtime_sdk_grants.py`.
- Go migration `20261010_runtime_grants`, parent `20261010_runtime_receipts`,
  with expanded common models in `runtime_execution.py`.

Go's runtime chain begins at `20261009_runtime_artifacts`, parent
`20261006_mtg_upstream_sdk`; current main's `20261012_work_delivery_started`
uses that same parent. These are candidate graph/source facts, not evidence
that both chains were applied in any production database.

Do not concatenate these chains, create both grant tables, delete historical
migrations, or introduce a separate migration framework. Common owner must
prepare one reviewed Alembic integration against actual current main and
installed history. Preserve frozen spike refs and original proof; any extracted
or adapted candidate gets fresh source-bound qualification. Go owner supplies
column/constraint/policy/API differences, not a competing migration patch.

## Next work and dependency edges

1. **G-COMPAT**, Go owner: return a reusable-component/gap map against this exact
   record at `/home/thomas/src/bifrost-go-spike-artifacts/common-core-compatibility-20261016.md`
   outside the frozen accepted packet. Include source file/symbol/migration,
   exact demonstrated cases, limitations and proposed adapter impact. Cover
   schema/grant collisions, RESTRICT/ancestor unlink, NEW-attempt retry,
   lock interleaving and partial retirement. No code/evidence mutation or rerun.
2. **CP-CONSOLIDATE**, common owner: consume that map and current Python source;
   decide one schema/issuer/lock design, finite writer/read/unlink matrix and
   receipt/resource lifecycle. Prepare concrete migration/role/profile diffs
   for the required review/authority decision. Do not land the old preview.
3. **PY-EXTRACT**, common owner: after common seam/prerequisite disposition,
   connect actual Python execution and unchanged SDK through the same accepted
   boundary; prove native behavior, cancellation, attempts, mixed writers,
   uncertain ACK/effect and reversible coexistence. Actual agent lifecycle
   acceptance remains required after workflow integration.
4. **G-ADAPT**, Go owner: only after common interfaces are settled, propose the
   affected Go adapter/conformance and developer-tool changes. Reuse frozen
   evidence only for unchanged inputs; changed consumers require new evidence.

Partial-cleanup recovery is not smuggled into either current acceptance or a
Go-only SDK change. Scope any new recovery implementation explicitly against
its source-bound negative, existing custody and no-replay invariant.

## Coordination receipt

Contract version 1, report `COMMON-RUNTIME-RECONCILE-20261016-01`.
Transport: T3 native thread send to the existing Go owner; explicit user
coordination authorization only. Handoff skill package `2026-10-09.1`,
engineering flow `2026-10-06.1`. G-COMPAT response pending. Commit and native
submission receipt identify this exact record; receipt is not mapping completion.
