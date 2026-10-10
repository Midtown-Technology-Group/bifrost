# Proposed isolated Rust-owned Go slice

Status: interfaces at `b8b44cbc01b1fb6be86f4fba050e93eb0da55924` explicitly released
by Thomas for isolated implementation only. [Exact authorization and conditions](isolated-implementation-authorization.json).
This is not runtime acceptance, a profile freeze or an accepted lifecycle writer.
SDK version remains0.0.0-spike.2. Original proposal platform main is
`b031b9ca3a4fc50d6eb04dbfdf578fe8f009913d`; workspace main refreshed to
`e666a60b614fc34022008dadfa734b1d970208c8`. No workspace asset was edited.
[Source inventory](owner-integration-source-evidence.json) binds inspected seams.

## Approved isolated implementation scope

Release the following common interfaces for **isolated local/synthetic workflow
implementation and race testing only**, subject to independent proposed-profile
codec acceptance. Keep production dispatch disabled, incumbent Python ownership
unchanged, and all credential, source, writer-exclusion and finalization acceptance
gates intact. This does not freeze the profile for production or accept agents,
no-timeout renewal, provider access, a source SDK grant, merge or deployment.

The released external gate is #1132's `implementation-slices.md`, Package4:
“human architecture release required.” Its final instruction says Package1 only
follows review; green static checks do not authorize Packages2–4. #1011's
`runtime-authority-sequence.md` still labels its records semantic interfaces,
not approved table names, and requires the schema/constraints/lock order together.
The present source provides no accepted owner-session/Start/release/Result schema.
Rust W0 has health/readiness routes; Running/Cancel SQL examples are standalone
characterization, not a workflow coordinator. Calling either the owner would be
false evidence.

## Proposed smallest executable slice

Retain the ordinary readiness workflow and unit tests. Its synthetic execution
input contains **one** admitted integration name in one known organization and
Solution installation. Existing control flow remains ordinary Go; extra names
are denied by the exact operation policy. A second execution uses the identical
accepted binary without rebuilding. Cancellation is a separate execution using
that same binary while its integration request waits at the synthetic TLS server.
No vendor endpoint is contacted.

Build a separate trusted adapter, without importing the tenant package. The
accepted bundle binds its actual adapter digest, workload executable digest,
build evidence, schemas and native descriptor. Metadata discovery never executes
the workload. The existing Python `function_name`/source-path deployment resolver
is not sufficient native artifact registration: propose an additive accepted
artifact association with the same Solution deployment and workflow identity.
Rust consumes the neutral descriptor and accepted association; it never parses Go
source or calls a build tool. Missing adapter/association evidence denies admission.
No fabricated digest or Python wrapper registration is accepted.

## Common owner operations and storage candidates

Names below are proposed internal records, not public wire fields or installed
production DDL. Isolated storage candidates now exist in the current Alembic
history: `20261009_runtime_artifacts`, `20261010_runtime_sessions` and
`20261010_runtime_receipts`. [Retained storage proof](owner-session-schema-proof.json)
does not accept a lifecycle writer, live process/session custody or finalization.
Use the existing Alembic history after reconciling the private grant branch;
its `20261001_runtime_sdk_grants` revision is absent from current main. Do not
create an independent migration root or install that branch by copying one file.

| Operation | Required retained fact / proposed record | Commit boundary |
| --- | --- | --- |
| Admit | Immutable owner association for existing `executions.id`, exact accepted deployment/artifact/caller and existing workflow attempt | Admission, owner assignment and source-consumer obligations together; no new logical job |
| Prepare | `runtime_sessions`: typed workflow attempt, owner, supervisor/runtime incarnations, actual private-channel identity, binding/Prepare/artifact observations; persistent `closed_at` tombstone | Trusted custody registered before preparation; UUID alone is insufficient |
| Start | `runtime_starts`: unique session and exact Prepare/binding/input/context/deadline digests, parent Start ID and committed clock evidence | Existing workflow running transition and Start record together, before sending any Start/material |
| Provision/release | `runtime_admissions`: unique purpose/identity, exact Start/session/grant/delivery/operation digest, finite expiry and admission frontier | Fresh owner/session/attempt/source checks; purpose cannot be changed or renewed into broader authority |
| Accept Result | `runtime_report_receipts`: unique session+message ID/type, Start ID, SHA256 of the exact validated raw payload excluding prefix, retained projection disposition | Receipt and existing attempt/execution terminal projection together; ResultReceipt only after observed commit |
| Close | Session tombstone, same-grant revocation, future-admission denial and durable close reason | Same owner serialization as Result/Cancel; close-before-absent-grant prevents later grant creation |
| Stop/drain | Separate supervisor wait/reap and source-consumer closure evidence attached to session | Actual exit/descendant observation, independent of terminal projection or advisory Stopped |

Require composite identity constraints linking owner, execution, attempt and
session through all dependent records, immutable owner/source/binding fields,
and uniqueness for one Start and one release purpose per session. Direct UPDATE,
DELETE, FK cascades, conflict updates and rollback must retain those constraints.
Do not reuse payload-free generic `execution_lifecycle_events` as wire receipts:
its FK is to generic `execution_attempts`, while this workflow uses
`workflow_execution_attempts`. Agent receipts remain a separate future mapping.

## Proposed lock and launch custody

For transactions admitting source, take the existing shared
`bifrost:workspace-release` advisory fence first. Then lock the workflow attempt,
refresh owner/execution/deployment/Solution using NOWAIT secondary locks as private
S requires, then session, Start/admission, grant and receipt rows. On secondary
contention abort the whole transaction without effects; do not wait while holding
an attempt behind an execution-first incumbent writer. Every new owner operation,
issuer/ingress/close path must use the same reviewed order. Row creation/absent-grant
races require the session row as their common serialization point. Existing
Python writers require mechanical ownership rejection; lock convention alone
cannot exclude them.

One trusted launch guardian owns a session's actual channel and process handle.
A release record is committed only after observed matching Start, authenticated
actual material, valid input, current eligibility and unexpired budget. Only an
unambiguously acknowledged release commit may enter physical spawn. Guardian
serializes its launch and observed-close commands; close before admission yields
no tenant initialization. Close after a winning admission retains possible
initialization/effects and requires stop/reap. This is **not** an atomic database/
OS-spawn claim. Commit ambiguity, guardian death or missing stop evidence prevents
replay; replacement requires proven old-process custody and owner recovery.
Actual race/initializer tests must accept or falsify this mechanism before release
acceptance. Do not turn a persisted release UUID into portable bearer authority.

## Proposed private provision interface

Use an inherited, unidirectional descriptor owned by the trusted supervisor and
adapter, separate from both protocol directions and tenant stdout/stderr. Its
closed bounded delivery envelope repeats canonical binding, Prepare/Start IDs,
grant/delivery IDs, operation digest, exact finite expiry and capability names,
and carries the dedicated SDK credential plus the accepted TLS origin. No engine,
DB, Redis, provider, signing key or ambient authorization fallback is included.
The trusted recipient validates this against independently authenticated issuer
custody and the committed admission; mere matching adapter-supplied strings deny.
Propose four-byte big-endian length framing and strict UTF-8 JSON, with a
64KiB envelope limit, duplicate/unknown-key rejection and exactly one delivery;
no reconnect or credential retransmission. Authentication is inherited descriptor
custody from the verified guardian, not a child-shared signing key. The guardian
ports private S's trusted issuance checks and validates its grant reference before
writing the envelope. The adapter compares that trusted envelope to Provision and
the accepted binding; the opaque bearer is freshly checked at restricted ingress.
Only the guardian has the writer; the adapter has the sole reader. FD inheritance
must be closed for every unrelated child, including the tenant executable, which
receives only its own scoped SDK configuration through a separately restricted
adapter channel. No guardian key or authority passes to the tenant. These are
proposed common private-delivery rules, not a Go wire message or an implemented
credential scheme. Logs/errors/results never include either delivery.

Keep private S's existing finite expiry/digest/JWT rules, exact installation and
null selector policy. Couple grant issuance and every SDK request to the actual
open session/tombstone. The SDK operation is only admitted `integration-get`;
ordinary app/lifecycle/refresh routes reject that purpose. Redirects are disabled.
No renewal is necessary for this finite slice. The existing synthetic local Go
provision is not accepted issuer evidence and cannot be promoted by renaming it.

## Durable projection and mandatory proof

Port the existing workflow consumer's success/error/cancellation semantics into
the Rust owner, including attempt fence and transactionally stored execution
result/type/error/metrics/log projections. A prior committed Cancelling decision
wins over later successful observation. Do not map oracle dispositions to new
public statuses. Preserve event/delivery/SDK buffer policy from current source;
commit before ResultReceipt/publication and characterize failed publication
without launching again. Read the outcome through the existing authenticated
Execution result endpoint backed by real `executions.result`, not a spike store.

The acceptance run must retain actual Rust service process/source, installed
Alembic graph, pool/session_user identities, exact accepted bundle, private-channel
and process custody, real restricted ingress denials, SDK request/output/logs,
ResultReceipt bytes, existing API readback and stop/drain evidence. Race tests
cover Cancel before release and after release, both Cancel/Result commit orders,
wrong/stale/closed session, expiry/revocation, rollback and lost commit/receipt.
Run unchanged Python-owned positives plus incumbent writer negatives through the
real pool. The fake supervisor may exercise the same bundle only after the common
adapter interface exists, and its observations never count as durable ownership.

## Verified inputs to the pending decision (2026-10-09)

Independent Go, Python and Rust codecs now consume the pinned structural/wire
corpora and all68 session scenarios (695 steps including fixture setup) without
importing the proposal oracle's session implementation. Hosted diagnostic
run38001923899 at `ae9b76c18d2b097e53c0799391377e8149fdbe8c` passed, including all
six18-message encoding directions, raw-byte receipt identities and deliberate
semantic-drift negatives. P0 remains unchanged. These are independently executed
specification checks, not architecture ratification or accepted production seams.
[Exact conformance and native proof](rust-session-conformance-proof.json).

The full isolated build recovered with the same pinned images and credential
restrictions. Hosted native run38001923981 at that same source passed414 Go tests,
ordinary build/static/security checks, synthetic SDK execution and cancellation.
The immutable workflow digest remains
`160917deeb94275f31ca9ddee2dacae00fb079fdf54cb60e206c8def261f9c34`; it is9,920,235
bytes. Latest cold compile is12.474s, warm edited compile313.27ms and median local
fixture execution3.776ms (20 samples). These do not measure real Rust lifecycle
latency or prove accepted native registration. No quota workaround, credential
expansion or performance-based isolation exception was used.

Thomas explicitly approved this common isolated owner/session/Start/provision/
release/receipt implementation scope at `b8b44cbc0`, retaining every acceptance
test above. That release permits implementation; it does not accept the issuer,
writer exclusion, process custody, durable projection or mixed-owner behavior
without proof. Concrete schema, identity, immutability, role constraints and common
lock order must be reviewed together before accepting a new lifecycle writer.
Crash recovery must preserve uncertain commit/spawn/lost-ACK evidence without
replay or ownership reassignment. Production dispatch remains disabled.

Actual runtime implementation still remains: native deployment association and
trusted adapter, common live-session/finite provision/release admission, real Rust
owner transactions and existing Execution API readback, actual-schema/pool writer
exclusion, and process/source cleanup. There is no accepted Rust service that can
already perform this slice. Do not satisfy it by promoting the test models or
local harness to an owner. Decision stays CONTINUE SPIKE; the goal is incomplete.
