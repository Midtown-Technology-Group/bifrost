# Go spike review of proposed execution_profile/v1

Review scope: PR #1132 at `370230fb4d42f481c9fb94efc8cc9ca7f1185e8c`,
inspected on 2026-10-09 against current platform main
`b031b9ca3a4fc50d6eb04dbfdf578fe8f009913d`. This is the Go spike's source
review, not a submitted GitHub approval, common-profile freeze, or authority
implementation release. [Exact inspected hashes](execution-profile-review-evidence.json)
retain the distinction between corpus inventory and executed tests.

## Disposition

The proposed message/identity model is suitable as the explicit candidate for
independent Go codec prototyping. No Rust implementation dependency was found
in the inspected schema/prose: the author imports capability APIs, the adapter
uses independent JSON schemas and frames, and another coordinator can implement
the same semantic contract. This is conditional source compatibility, not proof
that the same application bundle has run through another conforming supervisor.

Do not create a competing Go profile or widen P0. Keep the current P0 codec and
its pinned vectors unchanged while developing against this candidate separately.
Reference corpus counts are 68 decoded documents, 94 raw wire specimens and
68 synthetic session cases. These counts are not test passes. The oracle's
injected release/commit/delivery/cleanup events are assumptions, not runtime
authority; implementing its state fields would not create a coordinator.

## What resolves the previous specification gap

- Offer/Select negotiates the common family and one admitted artifact class.
  P0 rejects the new messages; no downgrade or profile switch follows admission.
- Prepare/Prepared binds actual artifact evidence, schemas and a trusted context
  without evaluating tenant code. Go variable initializers require a separate
  trusted adapter; hashing the application or local probe does not supply it.
- Original caller organization and effective scope are explicit separate facts.
  Historical unknown provenance cannot be converted to known global/null. Roles,
  claim tokens, ownership, accounting and issuer authority remain parent-private.
- Start and Provision are distinct, and actual private material is another fact.
  Their order may vary after commitment. No effects precede accepted preparation,
  observed committed Start, actual matching provision and fresh release admission.
- ResultReceipt identifies the exact raw Result payload bytes. Durable owner
  duplicate processing is different from prohibited pipe retransmission. Result
  loses to a prior committed Cancel; Stopped and transport loss prove neither
  durable completion nor safe replay.

## Three concrete integration decisions still needed

### 1. Fresh release admission and physical launch custody

The proposal explicitly leaves the mechanism that serializes current eligibility
with owner close before native spawn/import to #1011. Name the owner-held
operation, its actual source/session/attempt fence and lock order, how it consumes
the prepared process/channel identities, and what durable/custody evidence is
retained. Do not add a Go lifecycle state or an adapter-supplied `released=true`.

Acceptance needs actual initializer tests with close/cancel before admission,
wrong binding, missing/expired/revoked material, and cancel after admission.
The first cases must produce no tenant effects; the last must preserve truthful
possible admitted effects and prove bounded stop/reap. Parent crash between
admission and spawn must remain ambiguous, with no automatic replay.

### 2. Actual provision delivery and dedicated SDK ingress

Provision intentionally contains no bearer material. Freeze the separately
authenticated delivery interface and recipient custody: descriptor ownership,
delivery/grant identity, accepted operation selector/digest, expiry and current
session evidence. Define which trusted component checks issuer evidence before
the adapter exposes a usable credential. A synthetic matching string is not
this evidence, and a metadata notice cannot launch tenant code.

The readiness workflow needs only admitted integration-get for one exact
organization/Solution/name. Actual tests must deny ordinary app/lifecycle routes,
wrong selectors, closed/expired sessions and credential-bearing redirects before
unintended delivery. Preserve existing caller/disclosure and finite/no-timeout
policy. No source, DB/Redis, renewal upgrade or provider credential is needed.

### 3. Durable Result/Cancel/close acceptance

Bind the profile's raw-byte receipt identity to the existing owner projection
transaction and public Execution readback. Choose actual additive Alembic records
and constraints under the existing history, refreshed fence/lock order, exact
workflow/agent domain mappings and event publication after commit. A local Go
store, fixture role called Rust, or a receipt boolean cannot finalize anything.

Acceptance needs real Rust owner commits, conflicting/identical receipt cases,
Cancel/Result races in both commit orders, lost ACK/ambiguous commit, stale and
closed sessions, actual pool identities and hidden incumbent writer negatives.
Existing reduced ownership fixtures and Running/Cancel SQL tests are prerequisites,
not whole lifecycle or mixed-writer acceptance. Verify the genuine workflow through
the existing Execution API and re-execute its pinned bundle without rebuilding.

## Next bounded implementation

Use #1132's exact candidate as the proposed Go wire-codec baseline, pending common
architecture disposition. Implement independently from schemas/prose/raw vectors,
not from the Python oracle's private fields. Preserve every incumbent P0 record;
exercise actual partial/interrupted IO and both peer encoding directions in the
supported hosted CI/VM lane. Any changed proposal requires a new source pin and
review. Such a codec prototype does not release the three authority interfaces
above or complete the requested vertical slice.

No external review message, approval, credential expansion, production operation,
merge or deployment was performed. SDK remains `0.0.0-spike.2`; retained executable
proof remains at `8dbb5169bc3e90dd6d9f32d8c62d7fc350ca4ace`. Loaded MTG package
`2026-10-09.1`, Engineering Flow and PR Stewardship `2026-10-06.1`.
