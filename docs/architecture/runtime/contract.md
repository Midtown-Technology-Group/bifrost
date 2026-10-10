# Language-neutral execution contract v1

Status: proposed, structurally implemented foundation. Read [review](review.md)
for source pins, compatibility decisions and remaining gates. Authoritative
schema package: [language-neutral](../../../contracts/runtime/v1/language-neutral/README.md).

## Authority and execution permission

The coordinator admits an immutable deployment and binds logical execution,
typed attempt, caller, effective scope, Solution/deployment and runtime session.
The trusted adapter prepares without evaluating tenant code or receiving runtime
credentials. A parent-observed process handle is custody evidence; a child PID,
artifact claim or readiness message establishes no authority.

Execution permission requires all three facts together: accepted preparation,
durable Start committed by its owner, and valid provision for that same binding.
These facts must be freshly checked at release; a child-reported boolean is not
proof. A provision whose grant ID, expiry, binding or capability set differs
from the issuer's accepted grant fails closed. Secrets do not travel in these
static binding documents. The eventual delivery transport must authenticate its
recipient, prevent disclosure, and enforce server-side bounds and revocation.

For Go, spawn tenant code only after permission, including all package variable
initializers. Future JavaScript isolates and .NET likewise prepare accepted files without evaluating
imports/static constructors; Python preparation must not import tenant modules.
Different process arrangements remain possible only if they preserve this same
transition. Runtime sandbox permissions supplement server-side authorization.

## Document contracts

Artifact is a closed union: interpreted runtime, native executable, managed
runtime. Classification follows deployed bytes; Native AOT .NET is native.
Each carries accepted bundle identity, trusted adapter digest, SDK provenance
and protocol. Dependencies identify immutable evidence; build acceptance must
verify the complete content graph, runtime image, schemas, flags and recipe.
Source/toolchain identity alone never grants execution rights. Runtime startup
must not build, restore packages or resolve mutable dependencies.

Input includes schema identities, opaque JSON business data and trusted binding.
Output is either a value or a structured error, never both. Schema validation
occurs before capability use and before result acceptance respectively.
Language-native schema generation is optional tooling; unsupported shapes fail
build validation. The schema permits JSON values structurally, but actual
workload schemas decide portable numbers, nullability, strings and binary/time
representations. No implicit language conversion is allowed. Native exception
messages/details require redaction before export; they do not set retry policy.

Logs are bounded batches with a positive batch sequence and structured level.
An adapter must redact secrets before emitting them; the parent must apply its
accepted bounds before persistence. Logs are observations, not audit authority.
Usage measurements are nonnegative safe integers with explicit units; omit usage
where inapplicable. A future reviewed metering profile may add other numeric
representations. This document does not imply exactly-once log delivery.

## Lifecycle and result frontier

Keep bifrost.runtime/v1 as the common family; control_profile/v1 is its existing
partial profile. The separate document schemas do not extend that negotiation.
Prepare/Prepared, provision readiness, workload/log/result/usage framing and
receipt correlation need an explicitly reviewed full profile with independent
per-direction ordering. Unsupported peers reject before credentials/effects;
there is no coercion to interpreter artifacts or silent fallback to P0.

Start, Cancel and their child-observed frontiers must preserve queued-message
semantics already documented in P0. A cancellation receipt does not erase
possible prior effects. A late Result can be retained as evidence but cannot
complete a cancellation-authoritative attempt. The parent checks accepted
session, attempt, artifact, committed Start and current fences within its durable
acceptance transaction. Duplicate message IDs require identical retained
observations; conflicting duplicates fail closed. Exactly one final projection
wins; transport receipts correlate to that durable decision, not mere parsing.
Lost acknowledgement, Stopped or process disappearance never proves safe replay.

The additive [execution-profile proposal](execution-profile.md) now specifies
frame names, wire bounds, correlations, receipt bytes and concurrency semantics.
Its canonical [identity binding](identity-binding.md) preserves original caller
provenance separately from effective scope. These are review candidates, not a
frozen protocol or implemented authority. Actual deadline/custody, receipt storage
and owner transaction integration remain acceptance gates.

## SDK and execution-plane boundary

Python, Go, TypeScript and C# expose idiomatic APIs over the same selector,
effective-scope, capability-denial, error, cancellation, deadline, serialization,
secret-disclosure and retry semantics. Credential-bearing redirects must remain
confined by accepted provider policy and fail before unintended delivery.
Runtime credentials cannot access ordinary lifecycle/control-plane operations,
even with hostile workload-process control. Ninja/Sopdet endpoint PowerShell
execution remains a separate execution plane.

## Acceptance and follow-up slices

1. Review this static foundation and close full-profile negotiation, limits,
   error taxonomy, binding custody and receipt/frontier decisions.
2. Add the closed artifact types and real document codecs beneath the existing
   Rust/Python contract branches, preserving every incumbent P0 vector.
3. Reconcile Python and Go through trusted adapters; prove no tenant effects
   before committed Start and valid bound provision using adversarial initializers.
4. Port actual workspace behavioral vectors into the shared format; execute both
   runtimes with an ordered HTTP oracle, secret checks and no-network preflight.
5. Prove owner transactions, cancellation/result races, closed/wrong sessions,
   lost ACK, descendant cleanup and mechanical writer exclusion before release.
6. Freeze reviewed interfaces only after Python/Go acceptance; independent .NET
   and future JavaScript isolate consumers retain the same lifecycle authority.
   Deno is not a planned primary target.

No new production runtime, credentials, lifecycle owner, deployment or endpoint
change is part of this foundation. Existing #1011 restrictions remain in force.
