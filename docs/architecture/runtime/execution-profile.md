# Proposed execution_profile/v1

Status: **proposal for architecture review; not frozen, implemented in production,
or released**. Family: `bifrost.runtime/v1`. The executable package is a test-only
specification oracle, decoded schemas and shared vectors, not a trusted runtime.
See [identity](identity-binding.md), [implementation gates](implementation-slices.md)
and [upstream compatibility](upstream-compatibility.md).

## Source reconciliation and scope

Review starts at MTG main `5efefbcc296825c75cdeb3bf5d095e8eca8d72a6` (2026-10-09),
reconciled to `135a305811568362347e9d485ef992e9467f634f` after #1129.
The SDK dependency/source refresh is recorded in [review](review.md); it releases
no adapter/dependency custody gate.
[#1074](https://github.com/Midtown-Technology-Group/bifrost/pull/1074) merged the
neutral documents as `91be1fce66048fffe27276e223af5b67022e6f32`. Unmerged references:
[#1015](https://github.com/Midtown-Technology-Group/bifrost/pull/1015)
`9f35278f90ba3757318ac9f07895bda9957330de`,
[#1011](https://github.com/Midtown-Technology-Group/bifrost/pull/1011)
`93ec9421b23b0b4a1493ab45f15f55ded3377d2e`, and `spike/go-first-class-g0`
`1783de8a3154a07d434acf76111fc53f7154a4e1`. Exact P0 source hashes are in
[references.json](../../../contracts/runtime/v1/execution-profile/references.json).
No reference source is imported or promoted to production authority.

The architecture branch's runtime-authority-sequence, extraction-gates,
runtime-credentials, credential-foundation and runtime-sdk-ingress records are
constraints and historical proposal evidence. Their private grants, session
registry, source-client/dependency policies and STOP dispositions remain gates.
UUID binding alone did not prove live custody. Actual public credential admission,
renewal, writer exclusion, descendant cleanup and nominal owner behavior remain
unproved here. Current Python `ExecutionContext`, ContextVar scope resolution,
SDK credential resolution and execution services are source evidence, not an
already isolated implementation of this profile.

This proposal adds no credential, database writer, lifecycle behavior, runtime
routing or deployment. No Deno implementation or primary-target plan is included.
Future JavaScript isolates can consume the same semantics; neither workerd nor
celld is required. Ninja/Sopdet remains a separate execution plane.

## Wire contract and negotiation

A private authenticated parent/adapter channel is selected for one session and
one profile before launch of an **inert trusted adapter**. The parent obtains
actual process/channel identity independently; child UUIDs/PIDs do not establish
custody. No tenant process is required to exist at negotiation.

Keep the seven P0 envelope keys: `protocol`, `type`, `session_id`, `message_id`,
`sequence`, `correlation_id`, `body`. Protocol is exactly `bifrost.runtime/v1`.
Frames use a four-byte big-endian unsigned length followed by exactly that many
UTF-8 JSON bytes, 1..16,777,216 bytes, at most 64 nested containers (root depth0;
a container at depth64 is rejected). Each direction starts at sequence1 and
increments by1 independently. UUIDs are lowercase canonical strings; numeric
control fields are lexical integers within 0..9,007,199,254,740,991, positive where
specified. Booleans, float tokens and lexical `-0` are not control integers.
Unknown/missing fields, duplicate keys at any depth, invalid UTF-8, lone
surrogates, NaN/infinity and nonfinite numeric overflow fail closed. The P0
integer parser's i64/u64-to-f64 fallback/error precedence is retained.

Decoded schema validity is a separate layer. Wire rejection order is length/
framing, JSON/tree validity, exact envelope keys, protocol, nonempty type,
then common envelope UUID/sequence/correlation types, supported message discriminator and body structure
and within-frame correlation equality. Cross-frame correlation and direction
follow in session validation. Errors are static codes, never supplied bytes, secrets or tracebacks.
Clean EOF before a prefix is transport loss; partial prefix/body is
`TruncatedFrame`. IO failure is `Io`. A stream codec reads one frame at a time;
concatenated frames are legal streams, not one specimen. The offline oracle
accepts exactly one complete frame specimen, and does not test stream IO.

| Type | Direction | Correlation | Purpose |
| --- | --- | --- | --- |
| Offer | adapter→parent | null | inert adapter incarnation, supported families, profile capabilities and artifact classes |
| Select | parent→adapter | Offer ID | exact family, `execution_profile/v1`, one admitted artifact class |
| Prepare | parent→adapter | Select ID | accepted descriptor, canonical binding, workload input/schema identities and trusted context |
| Prepared | adapter→parent | Prepare ID | complete observed descriptor and exact Prepare identity |
| Start | parent→adapter | Prepare ID | exact committed Start ID and remaining monotonic execution budget |
| Provision | parent→adapter | Prepare ID | same binding/Start, grant and private delivery identities, finite expiry, operation digest and capability names; no bearer material |
| Heartbeat | adapter→parent | null | child-observed frontier and nondecreasing monotonic elapsed time |
| LogBatch / Usage / Result | adapter→parent | Start message ID | bounded observations; bodies repeat exact Start message ID |
| ResultReceipt | parent→adapter | Result ID | committed decision, exact raw Result digest and durable disposition |
| Cancel | parent→adapter | null | committed parent decision or terminal cleanup, reason and bounded grace |
| Stopped | adapter→parent | null | advisory child-observed stop, nullable Start/Cancel/Result references and redacted structured error |

These are thirteen message types. Input/context ride Prepare; there is no extra
input-ready, provision-ACK, begin, result-ACK-ACK or resume exchange. Heartbeat
retains the incumbent liveness/frontier observation without acquiring authority.
Prepared replaces P0's externally injected preparation evidence only in this
profile. ResultReceipt is necessary because parsing success cannot mean durable
completion. Provision metadata is necessary because Start and actual capability
delivery are different facts. The raw secret descriptor format is a separate
custody review gate, not an undocumented payload in Provision.

Offer must advertise the family and `execution_profile/v1`; Select must choose
both exactly and an offered, independently admitted artifact class. Capability
lists are unique bounded strings. Additional offered profiles do not get activated.
Unknown selected capabilities/classes reject before Prepare, provision or tenant
effects. There is no downgrade/fallback after failed negotiation or admission.
A P0-only session continues using its exact old Hello/body/correlation rules.
Neither profile can switch midstream. The new Offer is rejected by a P0 decoder;
new frames never travel through or widen P0 Hello. Profile selection must be
explicit in the eventual launch configuration. Codec authors must preserve every
P0 specimen and session result unchanged alongside the new corpus.

## Preparation, input and custody

Prepare is single-use and immutable. Its binding must equal the parent-admitted
binding; context must equal its defined tenant projection. The descriptor's
artifact_id must equal binding.artifact_id and the selected artifact class.
Prepared must repeat the exact descriptor and Prepare ID. Before accepting it,
the parent verifies actual staged bundle, adapter, payload, image and dependency
bytes, namespaces, mounts and process/channel identity against accepted evidence.
Child digest claims alone do not count. Descriptor IDs retain #1074's accepted
bundle-byte preimage; descriptors are external evidence, not self-hashed objects.

Preparation may inspect immutable files and validate schemas without tenant
imports, startup/site/package hooks, dependency restoration, integration probes
or model calls. It receives no usable tenant capability. Ordinary Go variable
initializers require a trusted adapter outside the tenant executable; Python
requires independent immutable adapter/interpreter/environment custody before
startup. Managed .NET static constructors also wait; Native AOT .NET is native.
There is no language-specific lifecycle exception. Current Python mutable pool/
requirements paths cannot be labelled prepared simply by hashing their metadata.
Stop extraction if adapter custody cannot be established; never invent a digest.

Parent validates input against the accepted input schema before Start; adapter
validates again before release. Output validation precedes durable Result
acceptance. JSON business values are opaque to lifecycle types: schema owners
must define portable numeric/string/null/time/binary representations, reject
unsafe integer reliance and avoid implicit Python/Go coercion. Runtime validators
must not load executable schema hooks. Context is trusted data supplied at Prepare
but is exposed to tenant code only at release. Tenant setters do not change scope,
caller or server authorization. Parent-only roles, claim tokens and fences are
absent. No resource/credential objects are embedded in input or context.

## Start, provisioning and the effects boundary

The owner accepts Prepared, then atomically commits Start for the exact Prepare,
typed attempt, session/incarnations, immutable deployment, caller snapshot,
source closure, deadline and operation set under #1011's refreshed ownership and
source-accounting fences. Flush or child assertion is insufficient. No Start or
bearer material leaves trusted custody before that commit.

Start must match the committed record
including its budget; it cannot substitute an extended budget. Start and Provision
may arrive in either order **after commitment**. Provision
must match the issuer's accepted binding, Start, Prepare, grant, delivery ID,
expiry, operation digest and exact capability set. The separately authenticated
private descriptor must deliver the actual matching material to that adapter.
A metadata notice alone, reserved slot or socket open is insufficient. An empty
capability set still needs explicitly admitted empty provisioning; it grants
nothing. This contract authorizes no token issuance or renewal implementation.

The exact permission transition is the trusted release admission after all of:

1. accepted preparation and actual immutable custody;
2. adapter observation of that one committed Start;
3. actual same-binding provision delivery, validated against issuer evidence;
4. valid input/context, unexpired and unrevoked grant, current open session,
   unchanged owner/attempt/fence/source eligibility and unexpired execution budget;
5. no winning cancellation/close at the release admission frontier.

The trusted supervisor/adapter applies this admission before spawning native
bytes, importing tenant modules, running managed initializers or invoking an
isolate. No new wire message grants release. How its current eligibility check
and physical spawn/import serialize with owner close is an explicit #1011 custody
integration gate. It must use the common refreshed admission fence, not cache a
child boolean. Cancellation before admission prevents release; cancellation after
admission may race already-admitted initialization/effects and demands stop.
No instantaneous rollback or revocation of effects already admitted is promised.
The test oracle's `release` is an injected trusted decision, not an implementation
of this fence, a database record, nor evidence of OS enforcement.

Missing Start, missing actual material, wrong incarnation/binding, mismatched or
expired/revoked grant all keep tenant effects forbidden. A committed but lost
Start/provision is treated conservatively as possible execution; never resubmit,
replace Start, fall through to Python or run another attempt automatically.

## Observations and durable Result

LogBatch entries retain #1074 levels, 256-entry and 16,384-character bounds;
positive batch sequence increments only for emitted batches. Usage has at most64
nonnegative safe-integer measurements with explicit name/unit. Cumulative usage
snapshots are descriptive, not billable deltas; durable owner integration must
choose accepted names/units and deduplicate before billing. Neither observation
may set lifecycle state, accounting org or audit authority.

Redact before emission and again before persistence. Implementation must bound
queues and reserve capacity for Result/Stopped/cancellation handling. Under log
pressure, drop unsent diagnostic batches before assigning sequence/IDs; never
block cancellation indefinitely or silently drop Result/Receipt. No exactly-once
log promise. Frame/body limits are normative; concrete queue sizes, scheduling
and drain-time acceptance remain process-package tests, not new author privileges.

Result is single-use success with value **or** error with code/message/nullable
JSON details. Error code is a bounded diagnostic string, not retry policy; native
exceptions and stack traces require redaction. Agent/workflow result schemas
preserve their distinct business outcomes under the same envelope; this profile
does not implement agent tool/model/step protocols or assert full-agent acceptance.
Unsupported interactive capabilities must reject before admission.

The parent checks private channel, session, Start, exact binding, output schema,
bounds, current grant/deadline and owner/fence facts in its durable acceptance
transaction. Result receipt and the single terminal decision/projection commit
atomically. Terminal eligibility seals new grant/SDK/adoption admission under that
same owner fence; actual process stop and source drain remain separate facts.
Publication occurs afterward and is separately retryable. `accepted`
means Result won that durable decision. `retained` means a Cancel/failure already
won and the bounded late observation was recorded without completing the attempt.
Neither successful parsing nor adapter `Stopped(completed)` proves completion.

Receipt identity is (session_id, committed_start_id, Result message_id). Digest is
SHA256 of **exact received UTF-8 JSON frame payload bytes**, excluding the four-byte
length, with no reserialization, normalization or business-value-only hashing.
Retain those bytes/digest before acceptance. Byte-identical owner-side repeated
processing returns the existing decision and receipt; different bytes under that
identity fail closed with `ConflictingReceipt`. Wire sequences remain strict:
retransmitting a frame on the pipe is invalid, even if identical. A changed payload
under a repeated message ID is a protocol violation, not a second observation.
Durable owner lookup may return an existing receipt after close, but cannot admit
new Result, reopen a session or replay workload effects.

A lost ResultReceipt leaves the adapter uncertain. The parent may already have
committed; no reconnect, resume or automatic Result retransmission is specified.
Operator/owner recovery consults retained durable evidence, never reruns tenant
code to obtain a receipt. Receipt delivery is not an acknowledgement of process
exit, delivery settlement, event publication or source drain.

## Cancellation, deadlines and closure

Cancel commitment and Result acceptance serialize under the same owner/attempt/
session fence. If Result commits first, later Cancel cannot replace its projection;
Cancel may still stop descendants. If Cancel commits first, a queued Result may
be retained, never promoted to completion. Arrival or emission timestamps do not
choose the winner. Parent commands do not order the other direction: a prepared
Heartbeat queued before child observation of Start, or Result queued before child
observation of Cancel, remains valid evidence with the proper references.

Before Start, cancellation blocks release and cleans preparation. During execution,
propagate cancellation through SDK waits, close future grant/adoption admission,
request cooperative stop, then terminate/reap all descendants after grace. Grace0
means immediate escalation; it does not grant extra execution time. Stopped uses
child-observed Start/Cancel IDs, not merely parent-sent IDs. It is advisory: actual
exit/descendant/source-consumer custody is independently verified by the supervisor.
After Stopped no new adapter frames are accepted, but a durable parent Receipt
may still be delivered until transport closure. No post-close Result is admitted.

`deadline_utc` is UTC with up to6 fractional digits; null means no workload deadline,
not global authority or unlimited credentials. `remaining_run_ms` is a nullable
positive safe integer measured by the trusted parent from its committed Start
clock; finite values cannot extend Prepare's deadline. The adapter derives a
conservative monotonic budget (subtracting transport delay through trusted clock
custody); equality at expiry denies release/acceptance. Current clock conversion
and suspend behavior must be proven by the adapter package. Null budgets preserve
no-timeout workloads, while provision still has finite expiry. No-timeout renewal
requires the separate reviewed same-grant session policy; never reinterpret null
as an eternal bearer token. Without renewal acceptance, continued capability use
at expiry is denied and stopped through owner policy.

Transport loss, invalid framing, child crash or adapter crash seals the session,
revokes future capability admission and initiates trusted stop/cleanup. Crash
before Start has no legitimate tenant effects; crash after committed Start is
ambiguous even if no acknowledgement was received. Adapter death must cause
supervisor cleanup of tenant descendants; it cannot certify that cleanup itself.
Parent/supervisor death requires custody-aware owner recovery under #1011. There
is no timer-based safe replay or new public terminal-status mapping in this PR.
Transport/crash observation alone creates no durable terminal decision. A separate
trusted owner commit chooses the failure disposition. Failure/cancel/result are oracle decision labels; existing workflow/agent domain
statuses and delivery policies need separate characterization and integration.

## Required review decisions before authority implementation

- Ratify this profile, correlations, bounds and byte-digest identity with independent
  Rust/Python codecs while all incumbent P0 vectors remain unchanged.
- Accept original caller custody (including unknown historical provenance),
  tenant-context compatibility and workload/agent schema policies.
- Name and prove the existing authoritative lock/fence order for Start, provision,
  release admission, Result, Cancel and close; no builder may improvise SQL writers.
- Prove issuer/ingress/renewal/provider restrictions, empty provision behavior,
  actual secret delivery, clock custody and post-close denial.
- Establish immutable Python dependency/adapter custody and actual supervised
  process/descendant closure, distinct non-owner runtime database roles through the
  real pool, hidden SQL/Redis writer negatives and source drain.
- Review durable receipts/public status mapping, events, cancellation races,
  mixed-owner/coexistence rollback and lost-commit recovery before C2/C3 acceptance.

A static proposal merge, if later approved, still does not freeze the profile or
release any of these authority-bearing paths.
