# Isolated original-owner Start integration

`owner-rs/src/live_start.rs` connects the actual guardian to existing common Rust
admission and Start transactions. It is an isolated implementation candidate,
not production dispatch or runtime acceptance. Source/build acceptance and caller
authentication remain mandatory parent prerequisites; this function does not
turn fixture metadata into either authority.

The parent stages verified readonly bytes and attaches one dormant guardian. The
function consumes its actual received Offer and sole live observation, verifies
the negotiated common protocol/profile/native artifact class, and matches the
complete Prepare binding to the pinned session bundle. It derives custody from
the guardian and hashes sorted compact UTF-8 binding JSON itself. Supplied digest
strings cannot replace these observations. Neither digest is a bearer credential.

Admission creates the existing execution, immutable owner, typed workflow attempt
and session through the reviewed real Rust transaction. After a fresh check, the
function sends Select and Prepare on the original channel. Prepared must name the
same session, original Prepare correlation, next adapter sequence and exact
artifact; reused message IDs deny. Start uses the existing transaction and is sent
only after observed commit and another fresh physical check. The entire monotonic
transaction/check interval is conservatively rounded up and deducted from the
returned finite budget. An exhausted budget denies transmission.

An error retains the borrowed original guardian with its caller for drain. Its
sole live observation has already been consumed; calling again cannot repeat
admission or Start. Commit ambiguity never authorizes a send, and a failed send
cannot be replayed. There is no recovery constructor for `LiveStart`, no clone or
serialization, no ownership reassignment and no new lifecycle table or language
specific state machine. The handle proves only observed Start commit/write on the
original process channel, not provisioning, launch, effects or acceptance.

The live guardian probe now consumes this integration in a denial case with an
invalid parent message identity and a lazy, deliberately non-authoritative SQL
endpoint. It must deny before SQL, reject an attempted repeat with the original
Offer, and drain that original process/namespace. Unit checks cover mismatched
Prepared acknowledgements and finite-budget exhaustion. These are acceptance
negatives, not proof of a successful owner transaction or SDK workflow.

`LiveStart::authorize_issuance` now consumes one process-local attempt before
checking the original guardian, reads the committed finite provision under the
common lock order, and checks the full original binding again. A failure or
uncertain read cannot retry through this handle. Its returned snapshot/caller
are signing prerequisites, not a credential, launch permit or release authority.
The common read-only SQL path requires exactly the provision admission before
issuance and exactly provision plus release before SDK use; cancellation,
revocation, expiry, current-caller/source drift and mismatched identity deny both.
The probe tests exercise actual committed provision and release transactions,
with synthetic custody/source explicitly retained as component limitations.
Authenticated issuer exchange and positive live delivery remain unimplemented.
The private `peer::OriginalPeer` primitive pins the parent-custodied PID, nonroot
UID and current `/proc` start ticks, then checks actual `SO_PEERCRED` on a connected
Unix stream and rechecks the incarnation. It fails closed across an unobservable
PID namespace. This is IPC identity only, never signing or lifecycle permission;
there is no caller-controlled wire identity, reconnect or recovery policy.
It uses the safe `rustix` 1.1.4 net API with the existing `unsafe_code=forbid`
gate. The lock adds only rustix, errno and linux-raw-sys; existing dependency
versions are unchanged. Application compilation/tests remain in hosted isolation.
The issuer still needs to consume this pin, its private socket identity, the
one-use live-owner check and independently loaded immutable signing preimages.

The next required consumer is the complete isolated coordinator: authenticate
actual source/caller and accepted archive, call this path against the real core
pool, obtain finite provision from the authenticated issuer, commit release,
deliver through the original private descriptor, serve the restricted TLS SDK,
commit Result/Receipt and read the existing authenticated Execution API. Common
cancellation, durable source-consumer settlement and uncertain outcomes still
need the same original custody and reviewed lock order. Until those are proved
and the evidence is accepted, the lifecycle goal remains incomplete.

## Post-Start original actor candidate

`LiveStart.serve_until_result` polls the original guardian and restricted SDK
admission gate in one borrowed Rust actor, without executing a build tool. Its
monotonic run budget starts before the actual Start write; issuance, delivery
and SDK service consume that budget. It permits one service attempt and caps
retained message identities at 4,096. Empty queue observations do not assert
process completion; reader loss/EOF/invalid bytes fail closed.

Runtime frames must carry the original session and a fresh canonical message ID
with increasing runtime sequence. Executing heartbeats retain nondecreasing
monotonic elapsed time. LogBatch requires the original Start correlation and
consecutive batch numbers. No untrusted text is automatically printed or stored.
The adapter emits one fixed credential-free process-exit observation before its
Result. The independent local supervisor checks that log's exact published
schema/correlation and observes it from the same unchanged workload binary.

Result acceptance consumes one attempt and sends the exact received payload to
the existing common Rust transaction. Only observed durable commit reaches
ResultReceipt on the original channel. Uncertain commit or receipt write drains;
it never reconstructs a live actor, repeats the transaction, or reruns a child.
The returned counts prove component observations only, not SDK HTTP completion.

This candidate still needs positive source/build/admission/SDK/Result/Receipt/API
readback proof. Cancellation, advisory Stopped handling, physical descendant and
source settlement, and crash/lost-ACK acceptance remain outstanding. No production
runtime, Go-specific lifecycle table, or replacement owner is introduced.


## Runnable original-owner entrypoint candidate

`spikes/go-native/owner-rs/examples/live_owner.rs` connects the existing common
transactions to one original network-none guardian and its private material and
SDK gate. The foundation producer emits `runtime-live-owner` after the unchanged
locked offline formatter, clippy, test and example-build gates. This is a runnable
integration candidate, not runtime acceptance.

The trusted parent must supply authenticated caller/source/build acceptance and
an actual native deployment association before this entrypoint admits anything.
The descriptor and archive pins belong to that parent. The candidate independently
checks the closed six-entry archive and unchanged workflow digest, stages private
immutable execution files, then requests restricted ingress on its exact socket
directory before creating a guardian. After actual Rust admission and Start it
reports public committed identifiers; the parent derives provision preimages
from those committed rows and starts the original finite issuer. No bearer,
signing material or database URL is emitted on stdout. Issuance, release,
private delivery, SDK admissions and Result/Receipt use the existing common
operations. No build tool runs on execution admission.

Every post-launch operation outcome attempts original guardian drain before
removing the material pipe, SDK gate and exact hash-checked source staging. The
parent owns issuer/ingress/key/socket cleanup and an outer bounded deadline for
stdin barriers. A failed or uncertain guardian create/drain requires inspection
of the original durable launch journal and stop-only recovery; it does not permit
a new nonce, owner or launch. A successful physical drain still does not establish
durable source-consumer settlement. The CLI retains that distinction.

Remaining proof: actual accepted native registration plus authenticated Execution
API readback, SDK HTTP fetch evidence, cancellation and durable cancelled outcome,
crash/uncertain commit/spawn/lost-ACK recovery, durable source settlement, and
re-execution of the identical accepted binary. The current SQL exchange fixture
and local portable supervisor do not satisfy those live prerequisites.
