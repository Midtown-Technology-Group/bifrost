# Canonical runtime identity and authorization binding

Status: proposed reconciliation. One definition file:
[binding.schema.json](../../../contracts/runtime/v1/language-neutral/binding.schema.json).
Workload/provision documents now reference its `legacy` definition instead of
keeping two copies. Their accepted decoded shapes and all #1074 vectors remain
unchanged. The new negotiated profile references `adapter`; tenant projection
references `tenant_context`. These definitions do not change P0.

| Boundary | Facts retained | Authority meaning |
| --- | --- | --- |
| Parent only | original caller ID/org plus literal authenticated role/provider/external flags, snapshot/policy/source digests, current eligibility, exact operation selectors, claim/lease tokens, ownership/fences, process/channel handles, issuer/credential material, accounting org | fresh authorization and custody facts from their actual owners; never inferred from binding UUIDs |
| Adapter binding | logical kind/ID, typed attempt ID/number, Solution/deployment/artifact, session and both supervisor/runtime incarnations, `original_caller {caller_id, organization_id}`, `effective_scope {kind, organization_id}` | exact correlation and rejection evidence; no grant, role, broad token, SQL authority or child-selected caller |
| Tenant context | logical/attempt/Solution/deployment/artifact identities, caller ID, effective scope | read-only informational projection; no original org, role flags, session/issuer keys or provision contents |

Original caller organization can differ from effective execution organization.
Example: caller from organization A executes an explicitly admitted global
workflow. Original org remains A; effective scope is `{kind:global,
organization_id:null}`. Null original org means **positively known original global
provenance**, not missing history. Unknown provenance cannot populate this binding;
stop neutral admission and retain the existing owner policy. Neither null permits
a global operation. An organization scope must carry its nonnull UUID; global
must carry explicit null. No omission/default/coercion to global is allowed.

The legacy `organization_id` preserves #1074's effective-scope meaning; it has no
original-caller org or incarnation evidence. It is unsuitable for execution-profile
admission on its own. Conversion needs parent-owned original snapshot, both actual
incarnations and explicit effective-scope decision. Never copy legacy effective
organization into original provenance. No implicit converter is introduced.

The parent derives tenant context mechanically from its adapter binding, and
checks exact equality at Prepare. The tenant cannot change it to authorize a new
scope or caller. Public Python `ExecutionContext` currently mixes identity, scope,
authorization flags, logging/checkpoints/config/secrets and platform helpers.
A Python compatibility shim must preserve characterized authored behavior using
server-side capabilities and observations without serializing that object into
the common profile. If a workload relies on omitted privilege flags, original org,
mutable platform objects or unreviewed operations, admission stops until a narrow
shim/context decision is reviewed. Do not guess flags or enlarge shared types.

Grant creation and every credential-bearing request independently check original
caller eligibility, effective admitted selectors, immutable install/source,
Start/attempt/session/fences, expiry/revocation and current close state. Local SDK
ContextVar checks and binding equality cannot replace ingress authorization.
Engine transport privilege must not determine original caller privilege. A GLOBAL
scope or nullable SDK scope argument must not silently become permission.

This PR supplies no new grants, credential purpose, role/ingress path or runtime
context mutation. #1011 and its caller/credential prerequisite acceptance remain
explicit release gates, including finite versus no-timeout renewal semantics.
