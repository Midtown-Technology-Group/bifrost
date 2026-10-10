# Finite restricted SDK issuer candidate

SDK remains `0.0.0-spike.2`. This implementation is covered by the isolated
interface release at `b8b44cbc01b1fb6be86f4fba050e93eb0da55924`; it does not accept
a runtime or enable production dispatch.

`api/src/core/runtime_sdk_credentials.py` is byte-identical to private S
`6419069da195b053c885ab349f431ff4fae62098`, SHA256
`8243b113f12c63819eaf7a93eea8ac5b9816990892b1868714a2ab33298c06e5`.
Its CRED-P1 ordered preimages, claim schema and audience checks are unchanged.
The retained tests use the existing common runtime ORM and account explicitly
for its additional owner/Start fields; no private migration or ORM is copied.

The access-only facade checks caller, source and operations preimages against
the grant, the exact reference, organization/install consistency and a finite
unexpired deadline. It permits one organization-scoped integration-get policy.
It exposes access token plus expiry, with bearer data excluded from repr.
The original private core still contains its refresh alias and optional expired
renewal decoding; neither is exposed by this facade or connected to a route.
Renewal remains unapproved.

These are private issuer consistency records, not public authoring APIs or
OpenAPI DTOs. No issuer, ingress, grant writer or lifecycle hook is enabled.
Before signing, a trusted issuer must authenticate the committed Rust owner,
Start, immutable grant/source admission and live session under the common lock
order. Supplied matching digests cannot establish those facts. Signing keys
remain outside the build and runtime workload environments.

Verification uses the existing hosted actual-schema writer lane: full API unit
suite, quality checks, actual database security/race tests and disposable stack
teardown. Trigger/source inventory additions cover these new files; selectors,
roles, resource limits, timeouts and acceptance conditions are unchanged.
Static Ruff and diff checks passed locally. Hosted checks and live issuer/SDK
integration are pending; this candidate is not runtime acceptance evidence.

## Dedicated finite SDK request boundary

`verify_finite_integration_get` verifies the dedicated CRED-P1 JWT without
expired-token mode, rechecks all parent-loaded immutable preimages, and requires
JWT issue/expiry seconds to match the committed grant's initial finite interval.
The request uses the existing SDK fields `name`, `scope`, `solution` and optional
null `oauth_scope`. It is bounded to 8 KiB of strict UTF-8 JSON; duplicate fields,
unknown fields, default/global scope, noncanonical selectors, extra authority
context and OAuth overrides are rejected. The returned immutable intent contains
no credential and is explicitly not admission or custody evidence.

The actual-schema provision test now passes the verified intent into the real
Rust SDK-admission transaction after committed release. This remains a component
test with synthetic source/custody; no HTTP listener or capability fetch has been
enabled. A trusted parent still must load authoritative rows, derive the fence,
prove live guardian custody/current source and caller entitlement, obtain Rust
admission, and invoke the stable integration API without vendor effects. Those
requirements remain gates on the end-to-end slice.

The new negative tests cover request ambiguity and scope expansion, changed
caller/source/policy/grant preimages, invalid signatures, expired finite time and
a correctly signed token whose expiry exceeds the retained grant. The ordinary
application access/refresh rejection tests remain. Full hosted API tests and
actual-schema checks are required for this revision; local checks are formatting,
lint and source inspection only.
