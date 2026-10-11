# Isolated finite issuer exchange

This is private trusted-coordinator IPC, not a Go lifecycle protocol or a public
SDK route. `LiveStart::issue_material` consumes the original handle's one issuance
attempt, checks original guardian custody, reads the actual committed finite
provision under the common source/attempt/session lock order, exchanges with the
original issuer, and checks the complete guardian binding again. There is no
recovery constructor, reconnect, renewal or production dispatch.

The parent independently pins the issuer's PID, nonroot UID, process start ticks,
private canonical socket path/inode and accepted test CA. The Rust client checks
actual kernel `SO_PEERCRED` before sending and after EOF, plus the original process
incarnation and private socket identity. The issuer independently checks the
original Rust owner peer before parsing, after reading and before responding.
Unobservable PID namespaces deny. Both processes stay outside tenant namespaces;
issuer/socket/key/core credentials never enter workload or builder mounts.

The canonical UTF-8 JSON request is four-byte big-endian length framed, at most
8192 bytes, then EOF. Exactly these fields are allowed:

| Field | Required value |
| --- | --- |
| `snapshot` | Actual 32-field committed finite CRED-P1 grant snapshot |
| `caller` | Actual currently eligible, unchanged owner caller snapshot |
| `grant_id` | That snapshot's UUID, matching the parent-reserved grant |
| `grant_digest` | Actual admitted grant preimage digest |
| `expires_at` | Committed expiry rendered in UTC with six fractional digits and `Z` |

The issuer loads the fixed execution/owner/attempt/session/grant tuple through a
readonly committed-provision reader. Incoming IDs cannot select another record.
Its caller/source/operation policy comes from independently pinned parent inputs,
not request authority. It compares the complete supplied snapshot/caller and
validates all existing finite signing preimages before using the dedicated SDK
signer. Matching fixture models alone remain consistency evidence.

The response is at most16384 bytes with the same framing and EOF. Its closed
object contains `request_sha256`, exactly the same `expires_at`, and
`sdk_configuration`. The latter has only `endpoint`, `bearer`, `organization_id`,
`solution_id`, `test_ca_pem`. Rust checks the exact request hash, scope, Solution,
expiry, pinned CA and fixed isolated origin `https://127.0.0.1:8443`. Duplicate,
unknown, noncanonical, truncated and extra bytes deny. No credential is formatted
in a diagnostic, probe result or public runtime frame.

One accepted issuer connection consumes the server; one Rust exchange attempt
consumes its channel even on denial/uncertainty. A failed signature/preimage/read,
closed peer, changed socket or missing reply cannot trigger a second issue or
transmission. The whole exchange has a three-second budget, including partial I/O;
individual reads cannot extend it by slowly dripping bytes. The issuer explicitly
joins its request task and removes only its original socket during drain.

The hosted fixture connects a nonroot Rust probe performing actual SQL issuance
admission to a separate nonroot Python issuer loading actual rows with the existing
readonly fixture principal. The core fixture credential goes only to Rust. A
separate ephemeral signing key goes only to the trusted issuer. Success must
observe one material response, deny a second exchange, leave all provision rows
unchanged, and prove socket/process cleanup. A lost-reply case signs once then
closes before transmission; Rust must deny, with no replay or lifecycle write.
Unit checks cover private response rebinding and immutable preimage/replay denial.

These are implementation checks pending exact-source CI, not runtime acceptance.
The probe retains synthetic artifact/source and guardian custody. Full accepted
source/build association, fresh release eligibility, original material delivery,
real Go SDK call, Result/Receipt/API readback, cancellation/recovery and durable
source-consumer settlement still require the complete coordinator and evidence.
Neither a finite token nor an observed issuer response is release/spawn authority.
