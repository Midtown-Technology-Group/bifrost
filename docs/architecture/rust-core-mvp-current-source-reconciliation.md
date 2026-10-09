# Rust MVP: current-source reconciliation

Checkpoint: 2026-10-09. This is an implementation decision record, not runtime
acceptance, a new credential policy, or deployment authorization.

## Source authority

Platform main: `135a305811568362347e9d485ef992e9467f634f`.
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
on the exact candidate above. It is running, not accepted. Original transport
remote remains e419; only the separate qualification ref was updated. No full
pre-PR, lifecycle, C2/C3, merge or production-deployment acceptance follows.
The prior two qualification failures and exhausted claim experiment remain
recorded. Do not transfer their passed subsets to this changed candidate.
