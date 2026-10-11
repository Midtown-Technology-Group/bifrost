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

The next required consumer is the complete isolated coordinator: authenticate
actual source/caller and accepted archive, call this path against the real core
pool, obtain finite provision from the authenticated issuer, commit release,
deliver through the original private descriptor, serve the restricted TLS SDK,
commit Result/Receipt and read the existing authenticated Execution API. Common
cancellation, durable source-consumer settlement and uncertain outcomes still
need the same original custody and reviewed lock order. Until those are proved
and the evidence is accepted, the lifecycle goal remains incomplete.
