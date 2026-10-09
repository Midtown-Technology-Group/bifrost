# Rust MVP: current-source reconciliation

Checkpoint: 2026-10-09. This is an implementation decision record, not runtime
acceptance, a new credential policy, or deployment authorization.

## Source authority

Platform main: `b031b9ca3a4fc50d6eb04dbfdf578fe8f009913d`.
The latest main advance adds only the CRLF normalization fast path and its tests;
it does not change runtime or authority interfaces.
Workspace main: `ef3c41a0ceeb07fd61acfa9bcd2010d9921cc31a`.
Architecture amendment #1011 remains provisional at
`93ec9421b23b0b4a1493ab45f15f55ded3377d2e`.
The current workspace audit inspected 2,217 committed authored Python files:
no platform src/SQLAlchemy imports, no parse errors, explicit empty allowlist.
Historical runtime results retain their original candidate pins.

## What current main changes

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
independent source acceptance. Local integration of current main produced
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

AUTH-P1 current-main integration is candidate
`8292784e5c5014d7fba4eb78c18d84798064ff68`, incorporating the platform head
above. The ten approved authority source/test paths are unchanged by that main
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
