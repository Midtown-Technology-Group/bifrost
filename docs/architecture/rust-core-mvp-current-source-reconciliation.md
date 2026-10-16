# Rust MVP: current-source reconciliation

Checkpoint: 2026-10-16. This is an implementation decision record, not runtime
acceptance, a new credential policy, or deployment authorization.

## Source authority

Platform main: `e42999395231ebc536711e5015422edfb75613a7`.
This supersedes the October 9 source checkpoint. Runtime, authorization, worker
ownership and dependency changes require fresh integration evidence.
Workspace main: `0b70c082bfa1a3c65b1ead8661774dd5d4ae36a1`.
Architecture amendment #1011 remains provisional at
`93ec9421b23b0b4a1493ab45f15f55ded3377d2e`.
The current workspace audit inspected 2,449 committed authored Python files:
no platform src/SQLAlchemy imports, no parse errors, explicit empty allowlist.
Historical runtime results retain their original candidate pins.

## Retained October 9 runtime findings

These findings were derived from main `135a305811568362347e9d485ef992e9467f634f`.
The October 16 checkpoint below records the verified later changes; do not
treat the older analysis as complete reconciliation of every later source path.

`api/src/services/execution/worker_sdk_http.py` mounts selected existing FastAPI
route objects on a worker-owned Unix socket. The Python parent retains pooled
PostgreSQL access and protected credentials. This is route reuse, not a new
Rust API or a language-neutral execution protocol. The selected surface includes
SDK operations, streams, context, source resolution and renewal.

`api/bifrost/client.py` selects the injected socket through engine_request and
its synchronous counterpart. An injected socket does not fall back to network
HTTP. Process-installed credentials can renew through the same socket; retry
eligibility remains the SDK's public behavior. Preserve these semantics during
migration and characterize them against this current source.

`api/src/services/execution/template_process.py` scrubs ambient database,
storage and access/refresh credentials before tenant children; it substitutes
an inert signing-key sentinel and installs the socket before user execution.
The pool owns setup and socket lifecycle. These are useful existing Python
mechanisms, not proof of Rust admission, committed Start, descendant custody or
attempt/session capability enforcement. Python internals remain Python-owned.

`api/src/core/security.py` still mints ordinary access-audience engine tokens.
The inspected mint sets engine identity and is_superuser=true, binds execution,
attempt token and Solution claims, and optionally includes delegated identity.
Original engine_caller claims are explicitly audit-only. Source pins and local
transport therefore do not replace the separately approved CRED-P1 audience,
closed operations, attempt/session grants and non-upgrading renewal. Preserve
legacy behavior as the reference; apply approved changes as an explicit,
independently reviewed prerequisite, never a hidden parity normalization.

## Runtime protocol reconciliation

Current main's docs/architecture/runtime/contract.md and language-neutral schema
package define separate artifact/input/result/log/provision documents. They
explicitly retain bifrost.runtime/v1 and its partial control_profile/v1.
Do not inject new artifact documents into the existing P0 Hello, relax unknown
field rejection, or import an unmerged runtime stack wholesale into main.

Retain the existing Rust/Python P0 codec and all incumbent vectors. A complete
execution profile must separately close Prepare/Prepared, provision readiness,
workload/log/result/usage framing, receipt correlation, per-direction ordering,
wire bounds and cancellation frontiers. This document does not freeze those
unresolved decisions. The common binding must identify accepted deployment,
artifact/adapter, logical execution, typed attempt, caller/effective scope and
runtime session; credentials do not belong in static binding evidence.

The required execution permission remains the conjunction of accepted inert
preparation, owner-committed Start and valid same-binding provision. No tenant
import/hook may precede that permission. A child Result is an observation;
parent acceptance serializes against cancellation and current fences. Lost ACK,
process disappearance and Stopped never establish replay safety.

Go/Deno/.NET foundation documents do not expand this Python-runtime Rust MVP.
Sopdet remains an independent execution plane and blocked compatibility gate.

## Immediate work packages and acceptance

1. **QUAL-COMPILE:** correct only the native prototype's two compiler defects:
   scope Body to tests and specify Instant as the shutdown-channel payload.
   Update only its runner source hash. Preserve shutdown cutoff, TLS, fencing,
   bounds, custody and all test declarations. Review independently, then use
   the existing supported Rust Core workflow on the separate qualification ref.
   A prototype pass cannot qualify actual lifecycle or original failed-run cause.
2. **AUTHORITY-RECONCILE:** compare the approved credential prerequisite with
   current engine mint, renewal, SDK socket selection and route dependencies.
   Freeze the minimal binding/enforcement interfaces before connecting an owner.
   No broader auth redesign or new public consumer requirement is authorized.
3. **WORKFLOW-LIFECYCLE:** connect reviewed Rust owner operations to one real
   Python execution path and durable result acceptance. Require actual submit,
   admission, execution and terminal projection, then cancellation, stale fences,
   mixed writers, lost commit ACK and in-flight rollback. Resolve mechanical
   writer exclusion and owner/source/provision gates first; static SQL plans,
   fixture counts and socket availability do not satisfy them.
4. **AGENT-LIFECYCLE:** retain the pinned unchanged consumer and apply the same
   authority/lifecycle boundary. Both workflow and agent acceptance remain
   required for the final decision; no live vendor/model use is authorized.

Use existing supported CI and repository commands. Add no custom supervisor to
repair these compiler defects. The exhausted claim campaign remains STOP; its
builder-security refusal does not reveal a configuration leaf or authorize a
sixth run. Assess harness/review/diagnosis cost alongside correctness before
expanding verification infrastructure.


## Candidate and verification disposition

Compile correction `75ac54c8ede0b083f7733f8fa17d457398a85d44` received
independent source acceptance. Local integration of October 9 main produced
`9a5d74f44923adb98eff811d6b5b796fb75148b3`, tree
`fadacb31fae48cba04aced8256fa29323bd0b73f`. Its sole conflict retained both
main's BIFROST_TEST_UNIT_ONLY unit isolation and the candidate's incumbent
separate parity suite; no new skip was introduced. Independent integration
review accepted that resolution. Static scoped diff and Bash checks passed.
Broad integration diffcheck also found inherited main whitespace; those files
were preserved and are not claimed repaired by this change.

The third changed Rust Core qualification is
[run37988587464](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37988587464),
on the exact candidate above, completed successfully in all four jobs. It ran
locked formatting, all-target Clippy, the default 31 Rust tests, migrated-schema
readiness through PostgreSQL/PgBouncer, container health/shutdown/configuration
checks and dependency/security/license checks. The admission example compiled;
its four runtime tests are not part of the default test command.

The separately released second changed transport experiment,
[run 37990005550](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37990005550),
failed in the artifacts phase. Its safe receipt reports source_checks=true,
suite=null and staging_disposal=null. This proves fresh source checks, not
completion of the transport suite or staging cleanup. The concrete failure
cause was not exported. Duplicate push run 37990006243 was cancelled before any
jobs; it supplies no test evidence. Both authorized transport experiments are
consumed. Keep transport acceptance on HOLD; do not launch a third experiment
or retrieve private child captures to invent a cause.

Historical October 9 AUTH-P1 integration is candidate
`8292784e5c5014d7fba4eb78c18d84798064ff68`, incorporating
`b031b9ca3a4fc50d6eb04dbfdf578fe8f009913d`. The ten approved authority source/test paths are unchanged by that main
advance. Static parsing, qualified scoped Ruff and whitespace checks pass;
the exact candidate is published to existing PR #1018. Supported PR CI
[37991033951](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37991033951)
and manual CI
[37991094247](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37991094247)
both completed failed on Docker Hub pull limits. Planning, lint/type and client
unit tests passed. The latter ran the existing literal `bash test.sh pre-pr` job;
no custom verification workflow was added. Historical AUTH-P1 CI does not
qualify this candidate.

No full lifecycle, C2/C3, merge or production-deployment acceptance follows.
The prior qualification failures and exhausted claim experiment remain
recorded. Do not transfer passed subsets between changed candidates. Next work
is the concrete admission/session/writer boundary contract and workflow owner,
followed by agent ownership; another transport supervisor is not the product.


## Boundary contract review frontier

The [concrete interface proposal](rust-core-mvp-boundary-contract-proposal.md)
records admission, immutable source, Prepared/Start/provision, live-session
closure, durable receipts and workflow-first/agent-next packages. Independent
review holds the initial SQL defaults; root corrected null uniqueness, native
association and persistent Start requirements in the draft. DML guards cannot
be presented as read/table-lock guards. The explicit coordinator read/ANY-lock
disposition, exact writer/cascade catalog and reviewed migration/profile diff
remain prerequisites. No additional SDK capability or implementation release
is authorized by documenting these proposals.

Fresh AUTH-P1 ARM64/reliability runs and all PR candidate/CI image producers
failed on Docker Hub unauthenticated pull-limit responses. Candidate Images
and E2E failed their dependency gates. Manual pre-PR tripwires passed before a
Redis pull-limit failure at infrastructure startup. Backend tests, client E2E,
MCP, ARM64 smoke and reliability behavior did not execute. The digest-verified
manual pre-PR artifact has an empty container inventory after teardown; this
alone does not prove network/volume cleanup. Original logs/run/combined-tree
identities are retained; all failed gates remain blocking. No retry was
dispatched without evidence the quota condition cleared.


## October 16 implementation checkpoint

AUTH-P1 #1018 now publishes exact candidate
`39245315e193e1d04886044677bf7a75bb8c6f1f`, including current main above.
The integration merged without conflicts. The only new AUTH change repairs a
positive test fixture to satisfy main's real private-agent rerun ownership
check; non-admin cases assert the actual ownership lookup executes. No guard,
expectation, selector, retry or timeout was weakened. Independent Sol review
accepted the final candidate; AST, locked scoped Ruff and whitespace pass.

New [PR CI 38947585397](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/38947585397)
and [manual CI 38947574111](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/38947574111)
are queued for this exact head; manual CI includes literal `bash test.sh pre-pr`.
These are new-source validation, not reruns that erase October 9 failures.
They do not establish runtime evidence until the applicable checks execute.
No local host tests, merge, deployment or vendor/model execution occurred.

Current main also changes a previously documented coupling:
`api/bifrost/_sync.py` now leaves caller-owned pending changes in Redis until
explicit post-commit acknowledgement. Both workflow-consumer flush paths call
`acknowledge_pending_changes` after their commit. The old unconditional
"Redis deletion precedes caller commit" finding is historical, not a current
source fact. Failed commit, failed acknowledgement, race/idempotency and
Rust-owned event/receipt behavior still need their actual parity tests; this
source correction does not qualify Rust mixed-writer operation.

The next independent source packet prepares concrete native FK, receipt
uniqueness, Start/close and tombstone SQL for the workflow owner. It is a review
proposal only. Ownership/schema/role/profile decisions remain held, including
the explicit coordinator read/ANY-table-lock disposition. No new runtime SDK
capabilities are bundled into that work.


The [workflow schema review packet](rust-core-mvp-workflow-owner-schema-proposal.md)
now makes the native FK, session and receipt constraints concrete. Its SQL has
not been executed and is not migration history. Root holds its stronger
one-session restriction and RESTRICT deletion behavior pending native
recovery/deletion characterization; these cannot be assumed parity.


## Current candidate verification update

For `39245315e193e1d04886044677bf7a75bb8c6f1f`, manual CI 38947574111
now reports successful literal pre-PR, backend unit, backend E2E shards 2/3
and browser shard 1. Its remaining MCP, backend shards 1/4, browser shard 2
and coverage work are incomplete at this observation; the full run is not
accepted. ARM64 smoke, reliability and CodeQL also report success for the
candidate. PR CI 38947585397 still has queued image producers. The four required
checks passing is not a claim that the whole applicable gate set is complete.

Pre-PR artifact 12069177147 was downloaded and verified against provider SHA-256
`6044bc6a527a1a538583a219dd46bd6370fb7fe293458015a8b3745b86dfecbe`.
Its log binds the exact candidate and says the local PR gate passed; its
post-cleanup container inventory is empty. Network/volume cleanup is not
independently inferred from that inventory.

The [deletion/recovery decision](rust-core-mvp-workflow-deletion-recovery-contract.md)
corrects two old source findings: poison imports the typed workflow attempt
under a local alias, and current source accounting already includes active typed
workflow attempts. A still-live runtime session after logical terminal is a
separate missing source consumer. Native ancestor unlink parity and proven
pre-Start native retries must survive the eventual owner guards.
