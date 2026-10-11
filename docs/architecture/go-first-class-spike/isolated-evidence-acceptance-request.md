# Bounded isolated evidence acceptance request

Status: preparation only; **not yet submitted for acceptance**. Current candidate
`56d1fb006d220b9dbb0747f1cca4def18677f918`, SDK `0.0.0-spike.2`.
Its hosted producer and Rust foundation passed; its live and security/race lanes
remain pending. The last complete verified package is
[nine-case plus create-loss proof](nine-case-create-loss-proof.json), source
`50ccb0ef3432b39f939e0e6e361e4299f2681478`.
Do not accept new-source claims from that prior package.

Thomas's recorded [isolated implementation approval](isolated-implementation-authorization.json)
requires: “Green conformance is not runtime acceptance. No runtime or lifecycle
goal completion until required evidence is accepted.” The existing interface
implementation release at `b8b44cbc01b1fb6be86f4fba050e93eb0da55924` remains effective.
This future request is evidence/writer acceptance, not another implementation
permission request and not production or first-class-language adoption.

## Concrete review surface

Read the [current common writer review](owner-current-review.md) together with
its linked actual ORM, migration, Rust transaction and authenticated SQL guard
sources. Review these facts together before accepting the isolated lifecycle writer:

- Composite deployment/execution/owner/attempt/session/Start/grant/receipt/resource
  identities and the actual source/artifact digest distinction.
- Immutable source/caller/binding/receipt/resource evidence, permanent close and
  revocation, one fact per session, RESTRICT retention and downgrade denial.
- Mechanical mixed-writer exclusion using authenticated non-owner principals;
  OLD/NEW, non-FK and external-effect coverage; unchanged Python-owned positives.
- The shared workspace-release → typed attempt → NOWAIT owner/root/deployment/
  Solution → session → Start/ordered admissions/grants/receipts/resource lock order.
- Real guardian/process/descendant/material/gate/source closure before resource
  facts; opaque retirement evidence cannot be supplied by a runtime report.
- Crash and uncertainty decisions preserve the same logical owner, never replay
  SDK/Start/Result/material, and cannot infer retirement from missing resources.

## Required proof before submission

All final-candidate lanes must finish and their same-run artifacts must be
independently inspected. Required observations include the unchanged accepted
Go binary, real Rust admission and Start, finite SDK, normal durable Result/Receipt,
cancellation, authenticated existing Execution API readback, original resource
and source cleanup, closed/open original-owner crashes, actual Result/release/
resource COMMIT ACK loss, actual Docker create response loss, and the new partial
source-retirement negative. Retain raw results, archive/source identities, security
and race tests, API quality, task inventories and cold/warm measurements.

The partial-retirement candidate preserves nine completed cases and adds one
negative case. It requires actual cancellation and process drain, an inert
first-party source obstruction, CleanupRequired after partial retirement, no
retirement token or resource fact, original-owner death and denied stop-only
adoption. API must remain Cancelling/null and source accounting pinned. Disposing
the exact fixture obstruction is explicitly not runtime settlement. This test
must not be reported as automatic partial-cleanup recovery or durable completion.

## Limits that acceptance cannot silently erase

This proof uses disposable hosted fixtures and synthetic TLS capability data,
not production or vendor effects. The restricted SDK calls and Rust transactions
are real; the local non-Rust supervisor uses synthetic control-plane decisions.
Existing Python-owned behavior is preserved rather than ported in this spike.
The builder and host custody model are not accepted hostile multi-tenant production
boundaries. Metadata is explicit JSON; general schema inference and polished
local CLI/registration are future work. Partial retirement fails closed; automatic
continuation after an arbitrary cleanup-phase crash is not qualified here.

The recommended decision remains **CONTINUE SPIKE**. Evidence acceptance may close
the bounded isolated proof requirement only; it grants no production, deployment,
merge, broader credentials, renewal, extra agents or profile freeze. If the
reviewer requires a missing technical condition for this bounded acceptance,
record that precise objection and continue the authorized implementation.
