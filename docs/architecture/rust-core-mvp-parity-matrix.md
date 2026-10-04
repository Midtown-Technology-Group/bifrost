# Rust workflow/agent MVP: parity matrix

Evidence checkpoint: 2026-10-04. This is an incomplete acceptance ledger,
not permission to cut over traffic or a CONTINUE recommendation.
[PR #1011](https://github.com/Midtown-Technology-Group/bifrost/pull/1011)
remains the provisional workflow/agent scope authority. Sopdet compatibility
is a separate blocked gate; it is not replaced by workflow fixtures.

## Source and evidence identities

The source inspection for this matrix uses collector candidate
`4234d30e62e8db4d077751541907a5cec4888041`. Its `core-rs/` has no diff against
`c911b46baa7260ed1492bfbe5e1295c6db9eb410`, the supported Rust source-check
candidate. The latter's
[run 37185003270](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37185003270)
passed formatting, locked all-target/all-feature Clippy and default Cargo tests
(7 + 6 + 14 = 27). This does not establish that examples, SQL scenarios,
Python characterization, protocol integration or events passed.

A later claim-characterization checkpoint is candidate
`0ef442248babd12a829543d801e5c6666a785ed2`, tree
`c9cb442957e8fd69f6c25b74a82d8a486248645c`, containing platform main
`7e737cacdba56b214e427afed5a28e89ae1b6a47`. Its
[source-check run](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37232398145)
passed on push attempt 1: formatting, locked all-target/all-feature strict
Clippy, 31 default workspace tests and 17 explicitly selected example tests.
Independent executed-evidence review accepted this bounded proof (receipt
`193163eea4e3a2e17670c6d919ab943fb04ac110602458719b5773f0ecb7728a`). Its separate
[characterization run](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37232401370)
failed on attempt 1. The primary exit-1 cause is unavailable in the retained log;
the evidence upload separately failed with EACCES on `source.txt` and no artifact
was retained. None of the new Python/PostgreSQL claims or cleanup is accepted.

The reference source is pinned by each campaign's receipts; do not silently
substitute a newer Python implementation. Main observed at the associated
checkpoint was platform `7ff37809f249ff21a9ca150780fbc8f1c6d07b0c` and workspace
`848134becf9e8fdb930efc86872daa62fdeb93cc`. These are checkpoint identities,
not a claim that main cannot have advanced.

The [verification disposition](rust-core-mvp-verification-disposition.md)
retains failed campaigns and their exact candidates. Source existence and an
accepted source design are separate from successful supported execution.

## Operation and acceptance ledger

Paths below are repository-relative evidence locators in the pinned source.
“Unproven” means the full row's required behavior has not been established;
it does not mean no partial evidence exists.

| Surface | Python authority / behavior | Rust source | Current parity status | Required acceptance evidence |
| --- | --- | --- | --- | --- |
| Service startup, configuration, health/readiness, tracing and shutdown | Existing platform remains traffic owner; Rust foundation adds its own health surface | `core-rs/crates/bifrost-core/`, `core-rs/Dockerfile` | Rust default checks passed at c911; production operation is unproven | Supported startup/readiness failure, bounded shutdown and observability evidence; normal W0 CI disposition |
| Existing-attempt claim | Original workflow consumer, attempt helper and PostgreSQL delivery checks; Python retains actual allocation | `plan_existing_attempt_claim` and explicit domain-vector driver | Source/unit checks accepted; candidate differential run failed with missing primary evidence | Actual Python/PG rows, real lock/lease observations, deliberate drift controls and qualified cleanup; pure plan does not confer SQL/Start authority |
| Attempt running | Original `mark_attempt_running`, called by `api/tests/parity/workflow_domain_harness.py` | `plan_attempt_running` in `core-rs/crates/bifrost-domain/src/workflow/mod.rs` | Pure typed row-plan tests passed; durable ownership and HTTP unproven | Paired real DB transitions, exact fences/state precedence, mixed writers and unchanged runtime Start behavior |
| Fenced result and nullable projections | Original consumer `_process_success` / `_process_failure`; omitted arguments remain omitted | `plan_fenced_result`; `bifrost-db/src/workflow_parity.rs`, `workflow_numeric.rs`; SQL vector example | Typed plan tests passed; complete SQL/result parity unproven | Genuine Python/Rust committed rows, type/null/numeric/clock fidelity, fence loss and transactional outcomes |
| Queued/running cancellation | Original `ExecutionRepository.cancel_execution`; reference SQL order is observed by `test_workflow_sql.py` | `plan_cancel_state` and selected SQL example paths | Plan tests passed; coexistence, lock ordering and event parity unproven | Actual cancellation/result/running races, update order, no cross-tenant effect and committed-event outcomes |
| Runtime control codec/session | Python remains execution runtime; SDK and authority boundary remain public | `bifrost-contracts/src/runtime/`, control-vector fixture and example | Contract source/default checks exist; operational authority not established | Versioned codec differential evidence, caller/session/attempt/source binding, negative auth and non-upgrading renewal tests |
| Execution admission, dispatch and source pinning | Current Python admission/deployment/source ownership remains authoritative | No migrated service route in the inspected Axum router | Unimplemented in serving Rust path | Durable admission and dispatch parity, immutable source/deployment identity, recovery and no automatic replay after possible effects |
| Workflow/agent HTTP and runtime credential authorization | Python owns public contracts and caller/org policy | Router currently exposes only `/health` and `/ready` | Unimplemented/unproven | Request/error/status precedence, identity/org boundaries, closed runtime grants and credential-bearing redirect refusal |
| Events and Redis | Python reference publications are observed by domain harness | Domain plan has no emitter; SQL test records `rust_event_adapter=absent-held` | Rust event parity absent | Actual committed event comparison and deliberate event drift detection, including Redis failure; no event publication before failed commit |
| Agent/tool lifecycle and caller authority | Current Python agent/tool path, with separately reviewed prerequisites | No authoritative migrated serving path proved | Unproven | Pinned unchanged compatibility consumer, tool/answer/summary and provider-authority propagation; no live vendor use without separate authorization |
| Ambiguous COMMIT, restart and recovery | Original Python transaction and side-effect ambiguity rules | Consumed-COMMIT actor/controller source exists | Runtime campaign failed before actors/SQL; no fault parity | Actual consumed positive bytes, committed transaction fate, same-DB final rows, no replay and independently observed cleanup |
| Reversible routing and mixed writers | Python remains administration/runtime owner and rollback destination | No acceptance proof for live migrated lifecycle traffic | Unproven | Same-database interleaving, in-flight rollback without data repair, single-owner dispatch and tested routing reversal |
| Workspace public boundary | Authored code uses public SDK/API; explicit empty internal-import allowlist | No Rust-specific workspace accommodation | Checkpoint source audit retained; runtime compatibility unproven | Continue exact-head boundary audit plus unchanged workspace/runtime consumer evidence |

Row-plan functions do not authorize DB ownership, a process spawn, a runtime
session or event emission. Successful synthetic states do not establish that
all those states are reachable under the real PostgreSQL constraints.

## Declared fixture coverage, not executed test totals

At the pinned source, `api/tests/parity/fixtures/workflow-domain-v1.json`
declares 138 cases. `workflow-result-v1.json` declares 233 paired cases and
controls in these categories:

| Category | Declared entries | Scope |
| --- | ---: | --- |
| Codec pre-admission negatives | 27 | Refusal before candidate operation |
| Actual schema INSERT | 9 | PostgreSQL constraint behavior |
| Source-only/out-of-tracked | 9 | Source admission and selected surface bounds |
| Decode-parity gate | 20 | Retained-row decoding fidelity |
| Feature-preservation | 8 | Separate feature controls, excluded from SQL pytest parametrization |

The later claim fixture declares 53 scenarios: 22 genuine Python cases compare
existing-attempt Rust plans; 13 genuine allocations compare Rust deferral; seven
Python-reference-only cases omit the Rust driver; eleven native-only boundaries
create no impossible SQL rows. Shared fixture dependencies still resolve. The
selected domain module predicts 207 JUnit identities (138 original cases,
53 claim cases, five detector-plane cases and eleven singletons). These are
source counts, not executed pass counts or 207 service-level parity cases.

The accepted three-path characterization change adds 4,609 and removes 339 lines
relative to `8b7d2c6597fd134044e74249e188c55a9c3c0844`: harness +1,306/-172,
tests +489/-22, conductor +2,814/-145. Their resulting sizes are respectively
2,581, 634 and 2,828 lines. This is verification-code maintenance cost, not
production Rust size, elapsed labor, performance benefit or reliability proof.
The full evaluation must justify the custom conductor and recurring review
burden against demonstrated lifecycle invariants; green unit checks do not
settle that cost/benefit question.

The SQL pytest source declares 233 paired cases + 65 non-feature controls +
one queued-cancel ordering test = 299 items. This arithmetic is a source count,
not a collection receipt or proof of 299 passes. The eight feature controls
must not be added again as SQL pytest items. Eight separately declared
`held_protocols` schedules cover distinct rollback faults, numeric overflow,
both cancellation/result and running/result orderings, lock timeout and lost
COMMIT response; their declaration does not prove execution.

`test_workflow_domain.py` declares deliberate status, nullable-input,
projection, clock and unselected-row comparator drift controls. Those controls
mutate retained observations; they are not alternative Python reference
implementations. The harness must still demonstrate meaningful HTTP, DB and
committed-event drift detection before those acceptance gates can close.

## Evidence needed to close a row

For each new passing claim retain the exact source/tree, reference source,
supported environment/image, selected commands, scenario IDs, normalized fields
with reasons, actual outcome and safe evidence link. Compare HTTP and committed
state/events where the operation has those effects. Missing evidence is not a
pass; a plan-level comparison cannot close a service-level row. Preserve failed
runs, unresolved checks and known differences instead of replacing them with
a later aggregate green label.

The next unclosed integration gates are the complete result/SQL suite, consumed
COMMIT characterization and agent authority/deletion characterization, followed
by actual lifecycle ownership and HTTP/event integration. Source and credential
acquisition repairs enable those gates but do not satisfy them. M1–M5 authority
concessions remain unapproved; source-only work must not exercise them.

Until these rows close, Python retains workflow/agent execution, dependency
environments, virtual imports, SDK runtime-local behavior, agent/model runtime,
public authorization and the currently unmigrated control-plane operations.
Alembic remains the only schema migration history. No merge, production deploy
or global MVP acceptance is implied by this ledger.
