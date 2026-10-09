# Native execution contract revision for shared runtime review

This is a concrete shared-contract proposal, not a runtime release or authority
grant. Reconciliation on 2026-10-09 uses platform main
`135a305811568362347e9d485ef992e9467f634f` and workspace main
`5208ab06ad922638e992c32fea0b7e807d93fd1e`. Architecture #1011 is
`93ec9421b23b0b4a1493ab45f15f55ded3377d2e`; P0 #1015 remains
`9f35278f90ba3757318ac9f07895bda9957330de`. Private grant #1024 remains
unwired at `6419069da195b053c885ab349f431ff4fae62098`.

Main now contains the proposed language-neutral document foundation. Its
[contract](https://github.com/Midtown-Technology-Group/bifrost/blob/135a305811568362347e9d485ef992e9467f634f/docs/architecture/runtime/contract.md)
and [review](https://github.com/Midtown-Technology-Group/bifrost/blob/135a305811568362347e9d485ef992e9467f634f/docs/architecture/runtime/review.md)
are authoritative over this spike's earlier artifact example. The foundation
resolves document shapes, including native identity and success/error Result;
it explicitly does not negotiate a full wire profile or release authority.
[Repeatable retained-evidence reconciliation](shared-schema-reconciliation.json)
checks the actual workflow and module-graph digests against the signed producer
descriptor. It reports partial coverage, never a native registration document.

## Architectural dependency

Authored Go depends on ordinary capability APIs and a typed authoring helper.
Only the runtime adapter depends on the published BiFrost wire contract. The
supervisor implementation may be Rust, Go or Python; workload code imports no
coordinator implementation. The workflow is the existing structured integration
readiness check, with loops, errors, a consumer-owned interface and context.

The ordinary handler API stays `Run(context.Context, Input) (Output, error)`.
SDK `0.0.0-spike.2` now implements the local authoring entrypoint
`bifrost.Workflow(Run)` with generic type inference. Tests call the domain handler
directly with its consumer-owned interface fake. Explicit schemas and manifests
remain the
initial deployment mechanism; the constrained AST extractor is optional and
fails on unsupported shapes. Supported Go language features are not limited by
that optional extractor: authors can supply explicit schemas for richer types.

## Remaining contract and launch gaps

1. P0 `RuntimeArtifact` requires an interpreter and a Python requirements-lock
   field. Main now supplies a separate native document variant and structural
   vectors. Negotiation/framing remain missing: never send that document as P0
   Hello or report Go as an interpreter.
2. Ordinary Go package initializers run before main. A linked application cannot
   send Hello/Prepared and await Start before those effects. Rewriting init,
   relying on Go runtime internals, or banning only `func init` would fail normal
   Go authoring and miss variable initializers and dependency hooks.

## Shared native document mapping

Retain the existing interpreted observation unchanged for its existing profile.
Use the native document fields published on main, including toolchain and
dependency evidence omitted by the original spike example. The following is
schematic, with placeholders rather than accepted deployment claims:

```json
{
  "kind": "native-executable/v1",
  "artifact_id": "sha256:<accepted bundle digest>",
  "image_digest": null,
  "adapter_sha256": "<64 lowercase hex>",
  "executable_sha256": "<64 lowercase hex>",
  "build_evidence_sha256": "<64 lowercase hex>",
  "platform": {"os": "linux", "architecture": "amd64"},
  "sdk": {"distribution": "bifrost-go", "version": "0.0.0-spike.2"},
  "runtime_protocol": "bifrost.runtime/v1",
  "toolchain": {"implementation": "go", "version": "go1.27.1"},
  "dependencies": {"kind": "go-module-graph", "digest": "sha256:<graph digest>"}
}
```

Every key is required; image digest alone is nullable. Each digest identifies
different bytes. Bundle identity uses the reviewed deployment artifact's existing
accepted content identity, not a new unreviewed JSON hashing convention. Build
evidence binds source, manifest/schemas, dependency graph, recipe, toolchain,
target and scanner. Native artifact observation is not limited to Go. Compiler
details remain build evidence rather than Rust-specific launch instructions.

The parent must compare the observation to its accepted immutable descriptor and
actual launched adapter/executable bytes. Child-supplied digests do not elect an
artifact or a deployment. Old coordinators reject an unsupported artifact/profile
before Start; there is no coercion into the Python observation. Exact capability
negotiation and interpreted/native union representation must be ratified together
with the full common schema; this example does not make P0 accept native Hello.

## Proposed launch boundary

Use one first-party trusted adapter per execution, which does not link tenant
packages. Before Start it can read staged immutable bytes and verify hashes; it
cannot execute the application, install dependencies, obtain source through an
SDK grant, or deliver a usable credential to tenant code. It implements the
common Hello/Prepare/Prepared messages using the ratified shared profile.

After both exact committed Start and actual bound private provisioning arrive,
the adapter execs the already-built tenant application as its supervised child.
The child inherits typed input and the narrow capability provision over private
descriptors. The authoring helper invokes Run, emits typed output and ordinary
errors, and responds to context cancellation. It needs no execution table names,
Rust types, scheduler assumptions, signing or lifecycle authority. This typed
authoring IO seam is subordinate to the common lifecycle adapter, not a second
Go lifecycle protocol.

The two-process boundary is a deliberate deviation from the preferred single
executable process: it allows ordinary initializers while preventing pre-Start
application effects. Both processes and descendants belong to the same enforced
resource/process custody. Adapter exit alone cannot prove workload exit. The
coordinator owns cancellation, kill deadlines, actual wait/reap and durable
outcome. No Go plugin, shared library, FFI or long-lived tenant host is introduced.

The deployment bundle pins both adapter and application. A local Go/Python
supervisor can launch that same bundle and use only published frames. Authored
code and application bytes stay unchanged. This stronger portability proof still
needs execution; the current local-only binary and partial control codec are
separate proofs and must not be combined into a claim that it already happened.

## Common result and authority prerequisites

Do not invent a smaller Go Result. Use the shared workflow outcome and preserve
the separate agent outcome, observations, usage and existing durable projections.
Result is an observation bound to the accepted Start, session and observed
private child channel. The coordinator derives logical/attempt/caller identities;
the child cannot choose another execution. Byte/depth limits, status/error
consistency and omission/null mapping require shared reference ratification.

Rust admission and Start commit must precede effects. Persistent session close,
private provision admission/delivery, SDK request admission and report receipt
acceptance need the common reviewed fence/lock sequence. Same receipt identity
with different payload fails; repeated same payload returns the existing receipt
without replay. No event is published before its durable commit. Stopped is
advisory, and lost acknowledgement never makes replay safe.

The selected SDK scenario needs one closed integration-get operation with an
exact admitted organization/Solution/name selector and a finite session/attempt
grant. It needs no provider credential, OAuth override, mapping mutation, source
permission, renewal, lifecycle/admin route or broad application token. Synthetic
fixture authorization is not acceptance of real public ingress purpose/actor
policy. The existing OAuth recovery/redirect issue cannot be hidden by selecting
a fixture without OAuth.

Mechanical writer exclusion needs actual non-owner runtime roles through the
real pool. The new PostgreSQL fixture tests a candidate mechanism; it does not
replace the reviewed additive Alembic migration, real hidden incumbent writers,
ancillary metering/annotation/delete policy or mixed-owner rollback proof.

## Executable acceptance sequence

1. Ratify the shared native observation, capability negotiation, staged custody,
   provisioning and complete Result/log schema using independent schemas/vectors.
   Preserve the already frozen P0 profile and both workflow/agent contracts.
2. Test an ordinary Go initializer that writes a synthetic canary. With no Start,
   wrong Start, missing/wrong provision and closed session, it must never execute.
   Matching Start/provision must permit the same unmodified application.
3. Admit through Rust's generic coordinator using an accepted deployment, actual
   typed attempt and distinct authenticated SQL role. Enforce writer exclusion
   against the existing incumbent paths and verify pool identity readback.
4. Execute the bounded SDK operation using the dedicated admitted grant; reject
   ordinary app routes, changed organization/name, expired/closed session and
   credentialed redirects without target delivery. Preserve source identities.
5. Accept common logs and Result into durable receipt/projection transactions.
   Observe via the existing Execution API. Run cancel-before-Start, cancellation
   during SDK wait, stale Result, cancel/Result race and actual descendant cleanup.
6. Execute the exact deployment bundle again without rebuilding, then through a
   separately implemented local supervisor. Compare semantic wire observations
   and output; remeasure the small edit loop and startup with the adapter included.

The native document shape is now supplied by shared main. The smallest
architecture blocker is the still-unratified full-profile negotiation/framing
and pre-Start launch/provision delivery boundary. The smallest durable execution blocker is
the absent accepted session/Start/receipt authority backed by actual writer
exclusion. Implementing Go-specific substitutes would not close either gate.

## Concrete reconciliation and next implementation boundary

Eight of eleven native fields can be mapped from retained build evidence or
the documented family/classification. Three remain deliberately absent:
accepted bundle `artifact_id`, actual trusted `adapter_sha256`, and an explicit
accepted runtime-image/null choice. Builder/scanner images cannot fill runtime
image identity, the local probe cannot fill adapter identity, and the application
digest cannot fill bundle identity. The graph digest identifies observed module
relationships, not complete artifact acceptance or trusted dependency closure.

The signed local descriptor has no Start/session binding. Main's workload input,
success/error Result, LogBatch and usage documents require the complete parent
binding; Result/log/usage also require message and committed-Start correlation.
Consequently stdout must not simply be wrapped as a valid runtime Result. Static
provision metadata contains no bearer secret and proves no issuer, finite grant,
closed-session denial or fresh SDK admission. Its caller_id cannot stand in for
original/effective role/org authorization custody; that remains owner evidence.

Next shared implementation needs exact profile negotiation, directional framing,
Prepare/Prepared and authenticated provision delivery, plus receipt/fence/race
semantics. Then the trusted adapter can consume those contracts, gate tenant
launch and report observations. Do not invent a local lifecycle wrapper around
these documents to call the real Rust vertical slice complete. Actual Rust
Running/Cancel SQL proof is useful prerequisite evidence, but it does not supply
Go deployment admission, runtime credential ingress or durable Result authority.
