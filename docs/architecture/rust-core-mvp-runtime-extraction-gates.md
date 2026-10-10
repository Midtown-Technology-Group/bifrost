# Rust core MVP: Python runtime extraction gates

Status: source-derived C2-W decision record, not implementation authorization.
Procedure: mtg-engineering-flow 2026-09-30.1. No merge or production deployment.
This extends the [amendment](rust-core-mvp-amendment.md) and
[reference gates](rust-core-mvp-reference-gates.md).

## Evidence and disposition

The audit inspected business source at platform main
`86caddecd8bbcf1feea9b0f5ed7865c0ea724d7b`, including #1013. Subsequent fresh
fetch confirmed that head. Workspace main advanced to
`e8605dc8edb6df8a997c171b534b324ac7ebd8ec`; its own AST boundary check passed
2,107 boundary files / 1,984 standard authored files, zero forbidden imports,
and an empty allowlist. #1001/#1112 remain ancestors. The selected authored A/B
bytes are unchanged from retained `83c1cb034dbbcfa29eb1723506735b13b4788536`.

These are source findings. No runtime environment, secret file, managed identity,
or production credential was inspected in this extraction audit. Declared
platform API 0.1.0 / SDK package 1.0.0 versions do not identify installed artifacts.

**The existing worker/pool/template path cannot pass C2-W unchanged.** It mixes
runtime preparation with control-plane credentials and unfenced helper authority.
A narrow post-Start adapter remains a plausible experiment; its safety is unproved.
This blocks reuse of the current path as a ready runtime, not the whole Rust program.

## Preparation and dependency custody

`api/src/services/execution/process_pool.py` starts
`requirements_setup_helper.py` before the execution template. Its subprocess has
no explicit environment. `simple_worker.py` / `core/requirements_cache.py` fetch
mutable requirements and run pip, with per-package fallback after failures.
Package/build/interpreter startup hooks can execute code before any logical run's
Start. Moving that work outside a message named Prepare does not remove its
credential or side-effect authority.

`template_process.py` starts Python without an explicit environment and preloads
SDK/runtime modules before its scrub. Installed site hooks, import shadowing and
cached objects therefore need custody proof before interpreter startup. Later
scrubbing is useful defense but cannot establish that earlier code never retained
credentials. Pool recycling can install dependencies before draining active work;
requirements text and a package inventory are not immutable execution artifacts.

C2 must ratify one concrete dependency policy before implementation:

- Separately prepared dependencies under restricted installer custody, with actual
  image/interpreter/SDK/package provenance and defined mutable-update/coexistence
  semantics; or
- Potentially effectful dependency work after committed Start, with explicit
  failure-phase, cancellation and possible-execution semantics.

Neither option is approved here. Do not silently seal today's mutable environment,
remove partial-failure/fallback behavior, change import semantics, or let a package
installer inherit coordinator credentials. Package management remains Python-owned.

Prepare may inspect staged bytes and metadata and create reviewed machinery. It
must not import tenant modules, execute package hooks, discover registration by
import, probe integrations, validate providers, or call models. Module initialization
is execution. A lost Start acknowledgement remains possible execution, including
import or dependency effects; it never permits automatic replay.

## Credentials must be restricted before startup

The template's named scrub excludes other potential authority surfaces, including
Azure storage/credential-chain inputs, provider keys, GitHub signing material,
telemetry authorization, files, profiles and inherited descriptors. This is an
inventory of source-supported possibilities, not a claim they are configured.
`Settings` reads `.env`; deleting an environment variable can permit file fallback.
The inert signing sentinel does not prove that later Settings reconstruction cannot
reload a real key from an accessible file. Same-user process inheritance is not
proof that parent secrets or managed identity are inaccessible.

`worker.py` accepts a handed-down engine token but also retains authentication
fallback. A C2 adapter must require scoped provisioning and fail before effects
when absent; it must never mint a server-accepted grant. No installer, template or
child receives lifecycle DML credentials or signing authority. Negative proof
must cover actual inherited environment, files, cached settings, descriptors,
credential/profile fallbacks and identity endpoints, without logging their values.

A signing-free token is insufficient: current engine transport has broad HTTP
privileges. Pointing its SDK client at an allowed URL does not prevent bearer reuse
against another reachable origin. C2 must demonstrate non-bypassable denial of
lifecycle create/claim/retry/finalize/cleanup and other prohibited HTTP writes under
the same provision that preserves the selected unchanged SDK/source reads.
Existing CLI result gates must be tested as they stand, not described as allowing
arbitrary core-run finalization.

A new token purpose, endpoint restriction or authority projection is a material
authorization decision requiring a concrete separately reviewed proposal. The
AUTH-P1 approval is caller propagation, not approval of a new credential policy.
Do not substitute a tenant/service principal and call changed scope behavior parity.

## Read-shaped SDK calls can cause effects

The unchanged readiness workflow calls public `integrations.get` and
`get_mapping`. Their current implementation is HTTP. In
`api/src/routers/cli.py`, `_build_oauth_data` can request a client-credentials token
when `should_auto_refresh_token` and actual provider credentials/token URL permit
it, including cases where `oauth_scope` was omitted. Successful recovery updates
OAuth token/provider state and the SDK route commits it. `require_oauth=false`
does not prevent integration OAuth construction.

The authored read-only guarantee is descriptive, not an enforced no-effects
contract. Preserve the #1001 public recovery boundary; do not remove refresh,
replace SDK calls, or grant workspace access to backend helpers to make a test pass.
A synthetic fixture with a false refresh predicate proves that branch only. A true
predicate needs synthetic request/recovery characterization. Neither SDK lookup
belongs in Prepare, and read-only metadata cannot justify replay after Start.

Module loading currently precedes engine installation of the SDK ExecutionContext.
Moving context injection ahead of import changes observable scope/security behavior
and must be characterized and reviewed separately. Preserve original caller versus
transport identity; no inferred provider/admin privilege from execution target org.

## Runtime evidence must not bypass lifecycle ownership

Useful seam: the parent workflow consumer now owns terminal SQL and SDK-buffer/log
flush; engine finalization does not perform that transaction itself. Candidate C2
extraction begins before tenant module loading and retains loader/virtual imports,
context, Python invocation and evidence production after Start. It does not launch
`src.worker.main`, reuse pool SQL callbacks, or call Python control-plane admission.

Remaining traps include direct Redis log/pubsub writes with runtime-supplied IDs,
source-cache repairs and storage fallbacks, retained `_sync` / log-flush DML helpers,
and direct public SDK writes. An empty selected-workflow buffer is not proof that
these authorities are absent. Importing a protected helper must confer no lifecycle
write authority. Broad Redis credentials can forge publication or induce privileged
consumers even when SQL is restricted.

Logs/results/resources/source observations need reviewed, session/fence-bound
custody and the required existing event projection. Preserve public SDK methods;
change internal runtime adapters only after the contract is frozen. No general
Redis/WebSocket rewrite or bidirectional Python lifecycle callback mesh is approved.

## C2-W acceptance and stop criteria

Before acceptance, prove all of these in the supported isolated environment:

- Tenant initialization, dependency/site hooks and synthetic SDK/OAuth/model sinks
  record zero effects for Hello/Prepare/Prepared, pre-Start Cancel and process loss.
- Actual installer/template/child custody excludes valid signing/DML credentials
  and bypasses, including Settings reconstruction and file/identity fallbacks.
- The same runtime provision passes unchanged selected SDK/source behavior but
  denies prohibited direct/helper SQL, HTTP and Redis-induced lifecycle writes.
- Running authorization commits before every tenant effect; missing Start ACK,
  import failure, restart, timeout and loss after Start do not replay work.
- Source hashes/install/generation, original caller/scope, log ordering/duplicates,
  results and required committed events match the characterized reference.
- Dependency updates, old/new process coexistence and partial failures have a
  ratified contract; package inventory is reported honestly.

Stop C2-W rather than waive a gate if safety requires retaining broad coordinator
secrets in runtime custody, silently changing public SDK scope, weakening Prepare,
or granting hidden lifecycle writers. Current nominal pinned A and corrected B,
complete runtime/source/credential contracts, durable receipts, mechanical database
writer exclusion and safe mixed-writer rollback remain prerequisites to C3.
