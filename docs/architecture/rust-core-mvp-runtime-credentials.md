# Runtime SDK credential decision proposal

Status: **narrow prerequisite scope approved; interfaces and security acceptance
pending**. The user authorized dedicated runtime SDK audience/purpose, closed
attempt/session grants, non-upgrading renewal and associated positive/negative
authorization tests. This does not authorize broader auth redesign, merge,
deployment, vendor use or C2/C3 acceptance. This document alone does not freeze
issuance/storage/crypto/schema or open authority-bearing implementation gates. Procedure: mtg-engineering-flow 2026-09-30.1; skills package
2026-10-01.2. This implements a decision gate in the
[runtime extraction audit](rust-core-mvp-runtime-extraction-gates.md), not a
change to the public Python SDK.

## Source and compatibility authority

The initial credential source audit used platform main
`86caddecd8bbcf1feea9b0f5ed7865c0ea724d7b`. The latest fetched main is
`f10da7c27568e65ee36d5699b72ef3a44ea7a3d8`; its reviewed multi-scope
root-table delta requires exact signed execution-org and reviewed-grant
selection in the interface freeze. Historical audit evidence is not a proof
of that unimplemented credential path.
The separately approved original-caller correction is unmerged
[AUTH-P1 #1018](https://github.com/Midtown-Technology-Group/bifrost/pull/1018),
`d82219f5aada66d879f2da71b386f50675c66d4d`. That approval does not authorize this
new credential policy. Workspace main
`e8605dc8edb6df8a997c171b534b324ac7ebd8ec` retains the selected A/B bytes from
`83c1cb034dbbcfa29eb1723506735b13b4788536`. A source SHA256 is
`f49b1b935ef2467f66eccf3c4b2750773f664d1f0e41f8ff6b08e3f11035a1de`;
selected agent declaration SHA256 is
`b79b5064f375098dabc119083cf2ff16778b683722b5df394f6cb8a06ca22914`.
These are source identities, not deployed runtime or dependency artifacts.

Current engine tokens have general application audience and transport-superuser
authority. The public SDK's local caller context is a separate projection. Passing
an existing engine token to a child without a signing key does **not** deny HTTP
lifecycle writes. A different ordinary tenant token would change SDK scope,
configuration/secret cascading and provider behavior. Neither is an acceptable
shortcut.

## Recommended bounded authorization change

Approve a separately reviewed **workflow-runtime SDK purpose**, working name
`workflow-runtime-sdk/v1`, with a dedicated audience outside ordinary application
access. Keep allowed existing SDK handlers' engine transport projection and
response behavior, but require an attempt/session-bound capability before that
projection is constructed. The agent model process receives no BiFrost SDK bearer;
its model/tool reports use the supervised runtime protocol. Each core-authorized
workflow child receives a distinct workflow grant.

Rust remains the admission/lifecycle authority. It constructs the grant from
committed authorized work, never from a runtime request for privileges. Python
continues to serve the selected existing SDK/source operations and their existing
OAuth recovery; it does not authorize child admission or decide run outcomes on
Rust's behalf. This is a narrow one-directional compatibility surface.

Proposed private binding (the wire/SQL representation still needs review):

| Binding | Required authority |
| --- | --- |
| Credential ID and grant digest | Trusted coordinator-issued, immutable; runtime cannot expand it |
| Logical workflow and domain attempt ID/number | Existing committed workflow attempt, separate from delivery lease |
| Supervisor session/incarnation and Start | Stored, committed Start and live authorized session |
| Original caller ID/org/flags and effective org | Separate identities; AUTH-P1/current recheck rules preserved |
| Workflow/install/deployment or release/source manifest | Exact accepted immutable evidence, with original global-source permission |
| Operation values | Explicit admitted integration, scope, selectors and source namespace |
| Expiry and renewal | Finite signed expiry; same grant/attempt/session only |

Prefer a signed grant reference rather than exposing the raw workflow claim
secret to the runtime. The trusted server resolves it to the actual stored fence.
The runtime receives neither signing authority nor a delivery token. The schema,
signing owner/key/algorithm distribution and grant storage are not frozen here;
any additive schema follows the existing Alembic history and mechanical owner/role
guards. No second migration history or new parallel logical-job system.

## Closed selected-operation policy

| Selected operation | Bound request and preserved behavior |
| --- | --- |
| A `integrations.get` | Existing POST `/api/sdk/integrations/get`; admitted name and scope, exact Solution selector or its absence; current response/cascade/errors and safe retries |
| A `integrations.get_mapping` | Existing POST `/api/sdk/integrations/get_mapping`; same admitted name/scope and null entity selector, only if its inputs permit this branch |
| B workflow children | Cove Data Protection integration get, global scope, independent child attempt/install grant; both unchanged capacity and preview tools remain in the authored catalog |
| Immutable source | Existing module read/resolve handlers, exact accepted manifest/install/source permission and returned bytes/hash; full namespace/package semantics require characterization |
| Renewal | Existing POST `/auth/refresh`, same access_token/refresh_token response fields, same dedicated purpose/audience and stored grant |

These are operation classes with bound values, **not call-count budgets**. Do not
remove SDK/provider retries to simplify proof. No general integration-name, entity,
organization, source-root or route wildcard. Local ContextVar scope checks are
preserved but are not confinement: raw HTTP must enforce the grant too.

Neither selected source calls HTTP developer context, SDK resources or requirements.
They are denied for this profile unless a separately characterized source closure
requires a concrete additional operation. The runtime cannot use SDK workflow/agent
admission, cancellation, retry, finalization, logs/results/usage/steps/approvals,
admin, WebSocket or MCP routes. Logs and outcomes are evidence on the supervised
channel; the coordinator performs their fenced durable/event projection.

A workload needing an unsupported capability remains Python-owned **before**
admission. No new public error is introduced for an existing Python-owned request,
and no unsupported-operation discovery after Start permits fallback or replay.
The public SDK and authored workspace/runtime code require no Rust accommodation.

## Non-bypassable verification and renewal

Ordinary app token decoders reject the dedicated audience by default. Do not
broaden `decode_token` globally to both audiences. A reviewed ingress path validates
signature/algorithm/issuer/audience/purpose/expiry and the stored grant, current
attempt/owner/session/Start before allowing an exact matched operation. Existing
Pydantic request parsing plus bound-value comparison preserves null/default/scope
semantics. Client flags, headers, proxy paths, duplicate query fields or a local
context object cannot confer approval. Authority-store failure denies the request
before handler effects.

Inside an accepted integration handler, preserve the existing engine transport
principal and effective-org projection. Grant scope derives from the trusted
original caller and admission evidence, never transport superuser claims or a
runtime-selected organization. This does not redesign global `_resolve_sdk_org_id`
or legacy human/service/engine credentials.

Source handlers currently perform manual app-audience JWT decoding and active
attempt checks. Adapt only the accepted new-purpose authority path; keep ordinary
routes rejecting it. Inventory manual JWT consumers, cookie/refresh/MCP/WebSocket
and alternative credential paths; a route wrapper alone is insufficient.

Current `renew_engine_access_token` copies claims and reissues using the default
application audience. Reusing it after merely accepting the new audience could
**downgrade a restricted credential into general authority**. New-purpose renewal
must retain audience, purpose, grant and all bound identities, deny stale/revoked or
wrong sessions/attempts, and never fall through to ordinary-user refresh, account
login or broader engine issuance. Freeze finite/no-timeout lifetime policy against
actual source before implementing issuance; do not invent a universal revocation
policy. Legacy renewal remains unchanged.

Credentialed remote HTTP requires HTTPS and disabled automatic redirects. Actual
301/302/303/307/308 target non-delivery tests cover SDK/source/renewal/provider
transport. Ordinary app audience rejection protects old replicas; all accepting
replicas need the reviewed purpose policy. Egress confinement supplements this
policy, not substitutes for it.

Source review at main `f770094eb` found an unresolved provider-transport gate:
`api/src/services/oauth_provider.py:327` posts the credential-bearing payload
through aiohttp without `allow_redirects=False`. Locked aiohttp 3.14.3 defaults
to following redirects, including 307/308 body replay. The selected public
integration recovery path reuses this helper. The successful normal synthetic
exchange in #1026 does not establish redirect target non-delivery. Public H/R
cannot pass the transport gate until a narrowly reviewed correction or explicit
scope disposition preserves allowed recovery and proves zero credential-bearing
delivery to 301/302/303/307/308 targets. No shared OAuth implementation change,
provider call or transport acceptance is authorized by this source finding.

## Effects, source and dependency custody remain gates

Allowed `integrations.get` can perform client-credentials OAuth recovery and commit
token/provider state, including an omitted oauth_scope branch with an entity-
templated token URL. Preserve the #1001 public recovery behavior. Characterize
synthetic true/false refresh predicates and their committed rows/transport; do not
claim read-only metadata prevents effects or replay. These effects occur only
after Start, in the trusted SDK API, never in Prepare.

`module_cache_sync` attempts `/api/sdk/modules-index`, but current main has no
matching router. Do not invent an endpoint. Removing broad runtime Redis/object-
store authority requires reviewed manifest/index/generation/fallback extraction;
source pinning does not freeze shared workspace generation or dependency packages.
The current worker/pool cannot be reused unchanged.

A selected-profile trusted prebuilt interpreter/SDK/dependency artifact is a
candidate custody experiment, **not an approved change to general mutable
requirements semantics**. Alternatively dependency hooks may need explicit
post-Start possible-execution handling. This choice remains a separate gate.
No installer/template/child may inherit coordinator credentials before scrub.
Isolation begins at exec: explicit environment, restricted identity/filesystem,
no service .env/profiles/signing/DB/Redis/storage secrets or managed-identity
bypass, and only reviewed descriptors. No actual secret values are inspected or
printed by this source proposal.

Provider slots go only to the agent model process; child SDK/Cove credentials go
only to that child. Existing Pydantic provider chain, retry/failover, model/profile
configuration, tool validation and usage remain Python-owned. Raw provider or Cove
credentials can retain broader vendor-account privileges; slot names and authored
read-only labels do not enforce account-level read-only authority. Synthetic
bounded transports are required for this MVP; no live model/vendor activity is
authorized by this decision.

## Acceptance and authorization requested

A separately approved auth prerequisite must pair unchanged selected SDK positives
with real denials using the **same provision**:

- Wrong integration/org/entity/install/root, forged local caller and raw-client
  bypass: no unauthorized handler effect or foreign state/secret disclosure.
- Lifecycle SQL/helpers, HTTP and Redis-induced writes: denied mechanically;
  exact allowed SDK/source/recovery behavior still succeeds.
- Purpose/audience stripping, ordinary-token conversion and all refresh/alternate
  auth paths: no upgraded credential or unreviewed principal.
- Stale/wrong attempt/session, expired grant and authority outage: denied before
  effects; no resurrection through renewal.
- Earliest startup/site/package hooks and pre-Start cancellation/loss: no tenant,
  SDK/OAuth/model/vendor effects and no ambient credential custody.
- Result/log/tool/usage evidence, duplicate/conflict receipts, mixed-writer races
  and in-flight rollback: original fences and ambiguity rules remain intact.

Stop if valid selected SDK behavior requires an unrestricted token, runtime must
inherit lifecycle/signing/Redis authority, new-purpose renewal can upgrade it,
source fallback needs broad credentials, authored tools must change, or Prepare
still executes tenant hooks. Permission for this prerequisite does not accept C2,
freeze the full runtime protocol or authorize C3, merge, deployment or vendor use.

Approved work package: **CRED-P1**, the separate narrow runtime SDK-purpose
prerequisite with closed operation grants and unchanged allowed SDK behavior.
Before builders, the architect freezes exact issuance/crypto/grant storage,
handler and renewal interfaces and their negative tests. Dependency custody,
provider scope and complete source/log projection remain separate gates. The
AUTH-P1 and CRED-P1 approvals do not establish C2/C3 acceptance.

The architect has frozen only the [CRED-P1-S private foundation](rust-core-mvp-credential-foundation.md): two grant/operation tables, closed tokens and private issuance/revocation/renewal helpers. Source permissions are empty; HTTP integration and real runtime/session/caller/source acceptance remain gated. This narrower first packet does not authorize a source-storage port or general runtime provisioning.
