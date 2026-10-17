# Runtime execution-profile implementation map

Status: proposed packages, no issues created or authority implementation released.
The #1074 static artifact/workload foundation is merged; this PR adds a proposed
negotiated profile, canonical binding, test-only oracle and vectors. It does not
freeze that profile. Read [profile](execution-profile.md), [binding](identity-binding.md)
and [review](review.md). Owners below name responsibilities, not assigned people.

## Package 1: Independent Rust/Python codecs and interchange

Owner: contract maintainers; accountable architecture reviewer ratifies semantics.
Inputs: `contracts/runtime/v1/execution-profile/{profile.schema.json,wire-vectors.json,
session-vectors.json,structural-vectors.json}`, shared
`language-neutral/{binding,artifact}.schema.json`, and unchanged P0 at #1015.

Target seams (unmerged reference paths; absent production paths stay absent here):
`core-rs/crates/bifrost-contracts/src/runtime/` and its
`tests/fixtures/runtime/v1/`; `api/src/runtime_protocol/{control,session}.py` gains
isolated `execution.py` / `execution_session.py`; tests in
`api/tests/runtime_protocol/` and Rust examples/tests. Choose the accepted
reference base explicitly; do not transplant either unmerged stack onto main.
The canonical vector owner remains `contracts/runtime/v1/execution-profile/`;
container copies require identical hashes, never independently edited fixtures.

Dependencies: architecture review of negotiation, lexical rules, receipt bytes,
canonical identity and direction/correlation rules. No authority credentials.
Acceptance: real encoders in each language feed the other real decoder in both
directions; consume every new vector and every incumbent P0 wire/framing/session
vector unchanged; compare exact error precedence. One-byte IO, interrupted/partial
reads/writes, max-size/depth boundaries and deliberate codec drift must prove RED.
A Rust/Python consumer must expand named JSON session fixtures without importing
the Python oracle. This package certifies codecs/transitions, not execution.

## Package 2: Trusted adapter interface and Python/Go initialization custody

Owner: supervisor/security owner with Python and Go adapter maintainers.
Target seams: new private adapter interfaces beneath accepted Rust runtime
supervision, plus an isolated Python adapter adjacent to `api/src/runtime_protocol/`.
Current `api/src/services/execution/{process_pool,template_process,module_loader,
worker,worker_sdk_http,requirements_setup_helper}.py` are compatibility inputs,
not approved extraction points. Go reference `spikes/go-native/` and its
`runtimecontrol/` are experiment evidence; reviewed production path TBD.

Interface: accepted immutable descriptor+binding/input/context; actual supervised
process/channel handle; Prepare observations; committed Start; separately bound
private provision delivery; refreshed release admission; observation channel;
cooperative cancellation, bounded termination and verified descendant cleanup.
Tenant code has no lifecycle interface. Use additive shims; retain existing
Python authoring semantics only where characterized and permitted.

Dependencies: Package1 and #1011's explicit caller/source/dependency/custody,
real session-close/issuer/ingress/renewal and mechanical writer-exclusion gates.
Select immutable Python environment/adapter evidence independently; do not
manufacture adapter hashes or silently accept mutable package installation.
Existing stopped reference cycles remain stopped. No new credential or source
permission follows from writing this interface.

Acceptance: actual Python import/site/default-expression and ordinary Go variable/
package-initializer canaries stay inert for every missing/wrong/expired/revoked/
cancelled Start/provision combination. Both valid arrival orders release the same
unchanged workload. Bound raw HTTP/SQL/Redis negatives deny control-plane writes.
Inspect actual environment/files/descriptors, role/pool identity, bytes and child
process tree; crash the adapter and prove no descendant survives. Run upstream
compatibility negatives with unchanged Go/profile. Mechanism evidence alone is
not Rust-owned execution acceptance.

## Package 3: Executable shared Python/Go behavioral oracle

Owner: compatibility/test maintainers plus selected workload/schema owner.
Inputs: six retained `language-neutral/fixtures/` cases and pinned provenance,
`compatibility.schema.json`, `required-scenarios.json`, selected original workspace
workloads and Package2's actual adapters. Existing ordered-HTTP/result/error
fixtures remain byte-for-byte; extend a separate executable case envelope only
under oracle/schema review. No placeholder workload counts as actual behavior.

Target seams: new shared runtime conformance tests in
`api/tests/runtime_protocol/` and supported Rust contract/adapter test lane;
common immutable case fixtures under `contracts/runtime/v1/`; equivalent Go
consumer in the reviewed adapter test package. Test-only HTTP/effect oracle owns
ordered exchanges, capability selectors, no-network preparation, redaction,
serialization/schema and time/cancel stimuli, not production lifecycle authority.

Dependencies: Packages1–2, accepted workload/context schemas, retained source pins,
issuer/ingress proof and supported dedicated-VM/CI execution environment.
Acceptance: identical ordered interactions and exact outcomes/errors across real
Python/Go, including every roster case; deliberate output, permission, effect and
secret-disclosure drift must fail. Keep raw observations distinct from durable
owner decisions. Static JSON validation is not behavioral evidence.

## Package 4: Rust-owned durable Result/Receipt and Cancel integration

Owner: #1011 lifecycle/security/data owner; human architecture release required.
Target seams: accepted Rust workflow/agent owner transactions and supervised
session registry; existing `api/src/services/execution/attempts.py`,
`api/src/services/execution_attempts.py`, `api/src/jobs/consumers/workflow_execution.py`,
workflow and agent attempt/domain projections, source accounting, event receipts
and credential grant consumers are characterization/integration inputs. No new
DML/schema writer is added here. Any additive storage must use Alembic's current
history and separately reviewed ownership constraints, not new parallel jobs.

Dependencies: Packages1–3 plus #1011's source-accounting-first admission fence,
final attempt/session/owner lock order, original-caller/credential prerequisites,
non-owner runtime roles through the actual pool, hidden-writer negatives, installed
schema custody, event/summary policies, source drain and coexistence/rollback.
Do not bypass STOP gates with an oracle boolean or session UUID.

Acceptance: real transactions prove Result-vs-Cancel in both orders, changed and
identical duplicate receipt bytes, closed/wrong sessions, revoked/expired grants,
rollback-before-publication and lost commit/Receipt. Exactly one existing domain
projection wins; publication retries never launch/bill twice. Actual adapter death
and supervisor recovery settle process/descendant/source custody independently.
No automatic workload replay follows from any ambiguous Start, crash or lost ACK.
Keep workflow dispatch settlement distinct from agent terminal delivery semantics.

## Independent future consumers

Managed .NET and Native AOT .NET consume these same interfaces after honest
Python/Go acceptance and a separately reviewed freeze decision. Future JavaScript
isolate runtimes remain possible consumers; Deno is not a planned primary target.
No workerd/celld substrate decision or Ninja/Sopdet refactor is implied.

Smallest next step after this proposal: accountable architecture review of the
profile/binding and custody/receipt gates, then Package1 only. Green static checks
cannot authorize Packages2–4 or declare a freeze.
