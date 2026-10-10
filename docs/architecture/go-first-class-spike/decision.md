# First-class Go: bounded spike decision and G0 design

Decision: **CONTINUE SPIKE**. Current checkpoint: 2026-10-09.
The initial G0 design below is retained for its decisions and exact historical
source pins. [Runnable G1/G2 results](results.md) now record actual artifacts, tests,
SDK execution, cancellation and cold/warm timings. First-class acceptance and
Rust-owned durable E2E remain unproved.
No production deployment, vendor operation, C2/C3 acceptance or merge is included.
Current SDK/package version: **0.0.0-spike.2**. MTG skills package2026-10-09.1;
Engineering Flow/PR Stewardship2026-10-06.1. Historical G0 used Engineering Flow
2026-09-30.1, package2026-10-01.5 and mtg-grill-with-docs. The separately inspected
Rust W0 foundation declares0.1.0; it is not an execution owner.

## Current verified outcome and exact remaining decision

[Latest executed proof](rust-session-conformance-proof.json) binds candidate
`ae9b76c18d2b097e53c0799391377e8149fdbe8c` to successful hosted diagnostic/native
runs38001923899/38001923981. Go, Python and Rust independently match68 session
scenarios and695 fixture steps per language. Structural/wire validation, all six
peer encoding directions, receipt preimages and unchanged P0 also pass. This is
specification conformance, not a supervisor or durable owner.

The ordinary Go readiness application still uses one typed SDK capability and
ordinary control flow, interfaces, errors and context. The native run passed414
Go tests, build/static/security checks, synthetic HTTPS SDK use and cooperative
cancellation. The same9,920,235-byte binary digest is retained across builds.
Latest cold compile is12.474s, warm edit313.27ms, edit→artifact592.78ms and local
execution median3.776ms (20 samples). There is no measured real Rust lifecycle
latency or equivalent Python end-to-end baseline. The fast warm compile supports
further investigation; isolated whole-source tests/scans remain separate build
costs, and arbitrary hostile multi-tenant builder isolation is not accepted.

Does Go depend on the BiFrost contract or Rust implementation? **The authored
application and candidate adapter codec depend on BiFrost contracts**, with no
Rust linkage, types, scheduler or database knowledge in the workload. Could
another conforming supervisor execute the same complete artifact? **Not yet
proved**: peer codecs are independently implementable, but the local application
harness is not the complete profile. Does authoring feel like a Go application?
**Yes for the implemented workload**; metadata extraction remains deliberately
narrow and actual deployment registration is absent.

Thomas has explicitly released the [common owner integration interfaces](owner-integration-proposal.md)
at `b8b44cbc0` for isolated implementation. [Exact approval](isolated-implementation-authorization.json)
retains all security/race, schema/lock-order and uncertain-commit/recovery gates.
The worktree now reconciles platform main `f11c6966d7b8553e4662cb6ab3414a0f8da3a2f2`
through local merge `a48f8df0964000cd86d8ac95741f0e968b17b9ff`; no PR was merged.
Runtime acceptance is not supplied by the interface release. Real native
registration/trusted adapter custody,
finite provision/live-session admission, Rust Start/cancel/durable Result and
existing Execution API readback, actual-schema writer exclusion and cleanup all
remain mandatory. No Go-specific owner or synthetic durable store is proposed.

The sections below are the initial G0 design and historical observations, not
current test counts, source heads or unresolved-contract status. The completion
audit and current proof retain the later executable evidence and actual gaps.

## Initial G0 source and evidence

Both named repository mains were fetched before design and rechecked at runtime
integration handoff; [latest gate readback](rust-gate-readback.json) retains the
advanced #1011/workspace pins independently of this initial snapshot. Existing checkouts were
clean and remain unchanged. Documentation lives on `spike/go-first-class-g0` in
`/home/thomas/src/bifrost-go-first-class-spike`, created from platform main.
Inspection host: `pve-t340`; no physical-host runtime tests or containers started.

| Repository / reference | Initial G0 observed SHA | Disposition at that observation |
| --- | --- | --- |
| Midtown-Technology-Group/bifrost main | `e58db4955ddd30177bd613f1d85b7e203ad7832a` | Source baseline |
| MTG-Thomas/bifrost-workspace main | `c1856d2fbc7530c65c67adfd28e4896ec402ccbf` | Source baseline |
| #1005 staged RFC | `2a70092eb773750d6bd082659c2a46b428748654` | Open, provisional |
| #1006 device STOP | `05bbb1db5426e1b531ed38c37f0aa82dfa71d3f0` | Open; device-specific STOP retained |
| #1008 differential foundation | `09539559961dd22ed3fd3f9179de84bc8f3bd772` | Open; no transferred parity acceptance |
| #1009 Rust W0 foundation | `5ad5010400bf1ade5d1a96cda22684a3f4d6cf35` | Open; no workflow lifecycle implementation |
| #1011 workflow/agent amendment | `9e6b7dcb1d03cbc5ca64e10bcf9de02a8312604f` | Open; authority-bearing implementation gated |
| #1015 C1-P0 partial protocol | `9f35278f90ba3757318ac9f07895bda9957330de` | Open; partial profile only |
| #1018 caller prerequisite | `d82219f5aada66d879f2da71b386f50675c66d4d` | Open; caller fix distinct from credential policy |
| #1024 private credential foundation | `6419069da195b053c885ab349f431ff4fae62098` | Open; unwired, no source permissions |
| #1026 credential reference tests | `e1f4c358efa08cbe8c1562dd8e409002aec9ee31` | Open; reference characterization, not public ingress |

[source-evidence.json](source-evidence.json) records observation time, PR URLs,
head/status/check snapshots, and inspected file digests at exact source pins.
Current #1005/#1006/#1008/#1009/#1015 report failing CodSpeed analysis; #1011,
#1018/#1024/#1026 report successful or skipped checks. None of these observations
is merge authorization, installed-source proof or runtime acceptance. No checks
were retried or thresholds changed. Historical local pre-PR failures described
by upstream packets retain their dispositions; this spike did not reproduce them.

The latest #1011 body reconciles platform e58db495, while some document sections
retain f770094 and older source pins. Treat those as historical claims rather
than current-main tests. Main has no `core-rs` or `runtime_protocol` implementation;
the Rust foundation and partial codecs are in unmerged candidates.

## First-action reconciliation

Source read: #1011 amendment, runtime extraction gates, runtime credential proposal
and private credential foundation; #1015 closed schema and Python control/session
code; current-main workflow registration, immutable Solution deployment closure,
integration SDK and credential-bearing OAuth transport; workspace readiness source.

Current registration candidates use Python path/function/decorator identity.
`PinnedWorkflowRuntime` carries path, function name, source hash, compiled manifest,
deployment/source resolution and runtime bounds. `verify_runtime_evidence` requires
queue, durable and authoritative manifest evidence to agree. Preserve that custody
and Solution-owned identity. Native artifact support belongs in a reviewed shared
deployment/runtime descriptor, not a binary smuggled through a resource or Python
source field. Rust must consume language-neutral registration, never a Go AST.

The common lifecycle is the intended architecture, not an available execution
service. #1015 implements Hello/Start/Heartbeat/Cancel/Stopped, big-endian length
framing and closed validation. `PreparedBinding` is an architect-provided input,
not actual Prepare/Prepared messages. `StartAuthorization` asserts a prior durable
commit; it neither implements nor proves that commit. Result/LogBatch, workload
input, credentials, durable receipts and process supervision remain outside it.
`StoppedObserved` is expressly insufficient to prove process exit or finalization.

Rust authority stays unchanged: entity/source resolution, caller authorization,
admission, execution/attempt/session identities, committed Start, cancellation,
recovery/retry, fenced durable projection, audit and final outcome. Go computes
and reports evidence. No Go tables, queue authority, DML, Redis, signing, broad
application bearer, delivery token or finalization capability is proposed.

## Representative workflow and authoring API

Choose a bounded integration-readiness workflow inspired by
`features/utilities/workflows/check_integration_readiness.py`, not a line-for-line
port. Input: integration name and required configuration keys. Output: readiness
and missing key names only. Default scope comes from trusted caller/effective-org
provisioning. No arbitrary organization override in the first scenario. A second
synthetic tenant demonstrates foreign-org denial under the same credential.

Minimum SDK capability: existing POST `/api/sdk/integrations/get`, bound to one
admitted integration and scope, with the exact admitted Solution selector.
Do not add Devices merely to mimic the sample. Mapping lookup is a later slice.
Integration data may contain secret values; inspect presence without logging or
returning values. There is no claim that an integration lookup is effect-free:
current `_build_oauth_data` can refresh OAuth and commit provider/token state.
Synthetic false/true recovery fixtures are separate required cases. No live
vendor action is part of this scenario.

Candidate API below is design notation, not an installed or compiled package:

```go
type Input struct {
    IntegrationName string   `json:"integration_name"`
    RequiredKeys    []string `json:"required_keys"`
}
type Output struct {
    Ready       bool     `json:"ready"`
    MissingKeys []string `json:"missing_keys"`
}

type IntegrationReader interface {
    Get(context.Context, string, *bifrost.GetIntegrationOptions) (*bifrost.Integration, error)
}

func Run(ctx context.Context, in Input, integrations IntegrationReader) (Output, error) {
    if strings.TrimSpace(in.IntegrationName) == "" {
        return Output{}, errors.New("integration_name is required")
    }
    cfg, err := integrations.Get(ctx, in.IntegrationName, nil)
    if err != nil {
        return Output{}, fmt.Errorf("integration lookup: %w", err)
    }
    missing := make([]string, 0)
    for _, key := range in.RequiredKeys {
        if cfg == nil || !cfg.HasConfigValue(key) {
            missing = append(missing, key)
        }
    }
    sort.Strings(missing)
    return Output{Ready: cfg != nil && len(missing) == 0, MissingKeys: missing}, nil
}

func main() {
    // Provisioning and SDK startup happen only after authorized process launch.
    client, err := bifrost.RuntimeClient()
    if err != nil { log.Fatal("runtime provisioning unavailable") }
    bifrost.Workflow(func(ctx context.Context, in Input) (Output, error) {
        return Run(ctx, in, client.Integrations)
    })
}
```

`HasConfigValue` must have documented handling of empty strings, arrays/maps,
booleans and numeric zero rather than guessing Python truthiness. Prefer typed
OAuth fields and `json.RawMessage` for arbitrary config to avoid numeric rounding.
Define sanitized SDK error types supporting `errors.Is`/`errors.As`; never append
raw error response bodies or credentials. Transport must honor context deadlines,
bound response sizes, require HTTPS for credentialed remote use and reject all
redirects, including same-origin redirects. Server-side bound grants are authority;
client-side scope checks are convenience only. SDK retry behavior requires the
current operation's safe-retry disposition, not a universal POST retry policy.

Client instances are explicit dependencies; avoid mutable package-global clients.
Handler tests use small interface fakes with ordinary `go test ./...`, no server.
Cases: valid/missing config, absent integration, lookup error, context cancellation,
and no returned secret values. Separate SDK HTTP tests cover actual response
decoding, request scope/selector, cancellation and non-delivery on redirects.
SDK internals do not redefine loops, branches, concurrency or error handling.
Ordinary packages, interfaces, generics, standard/third-party libraries and editor
tools remain usable within build/execution policy.

## Critical native initialization finding

Go initializes package variables and executes package `init` functions before
`main`. A tenant binary cannot safely emit Hello and wait for Start from a wrapper
in `main`: user/dependency initialization has already executed. Go's specified
[program initialization](https://go.dev/ref/spec#Program_initialization) establishes
this ordering. Inferring safety from a scanner or banning explicit `init` is
insufficient: package-variable initializers and dependencies also execute.

Candidate shared solution: a separately built, trusted, per-execution adapter
performs effect-free metadata/artifact verification and common protocol handshake;
only after committed Start may it launch the exact tenant executable. There is
one tenant executable process per attempt, with a trusted adapter process if the
shared supervisor design needs it. No long-lived tenant host, plugins, ABI loading
or Go embedding. The trusted adapter must not link or import tenant packages.
Its post-Start workload/result transport must itself be part of the frozen common
runtime contract, not a hidden Go lifecycle protocol. Rust owns outcomes even if
the adapter crashes or tenant code forges reports.

This is **an unresolved shared launch decision**, not permission to implement a
second state machine. An alternative common direct-native launch model could
authorize Start before exec, but would require reviewed changes to today's
Hello-before-Start ordering. Do not implement that exception for Go. Missing Start
ACK, initializer crash, adapter loss or coordinator loss after possible launch
remain possible execution; only Rust decides recovery. If neither shared model
fits without Go-specific lifecycle authority, reject first-class Go.

## Registration and schema decision

Prefer an explicit small manifest for G1. Example conceptual fields: portable
workflow identity/name, `runtime=go-native/v1`, build entrypoint `./cmd/workflow`,
timeout, input/output schema paths and declared capability inputs. Existing
Solution tooling owns installed IDs; do not manually register or edit root exports.
Manifest is build input; the executable entrypoint is the accepted artifact,
not a source directory to compile on admission.

Typed declaration extraction is optional later. Use a trusted build-time
parser/type checker, inspect declarations and concrete handler signature without
executing tenant code. Do not run `go generate`, import a tenant binary, execute
reflection helpers or evaluate arbitrary initialization to discover metadata.
Even metadata tooling consumes hostile source and needs builder isolation.

Bind manifest/schema digests, source closure, entrypoint/handler types and artifact
digest in one attested descriptor; admission checks that descriptor against reviewed
deployment identity. A binary echoing an embedded digest is not independent proof.
Changes to manifest, schema, source, tags or dependencies invalidate build identity.
Static signature checking catches manifest/type drift; end-to-end typed codec
fixtures catch schema/JSON drift. Rust sees only approved neutral metadata.

Start with explicit JSON Schemas checked against a conservative static Go type
profile. Evaluate automatic build-time generation as a separate tool, not runtime
reflection. Schema extraction must match `encoding/json`, including omission,
field conflicts and custom encoding behavior.

| Go type feature | Safe initial policy / extraction requirement |
| --- | --- |
| Scalars and named scalar/struct types | Resolve underlying types, exported fields, JSON tags and target integer widths; do not infer validation constraints |
| Pointers / `omitempty` | Distinguish absent property from explicit null; a pointer alone does not mean required or optional |
| Slices / arrays | Distinguish nil slice null from empty array; fixed arrays have length bounds |
| Maps | Initially string keys and known value type; reject custom/TextMarshaler keys unless reviewed |
| `interface{}` / `any` | Reject inferred schema; explicit schema plus encoding tests is necessary |
| Custom JSON/Text marshalers | Reject inference, including methods on nested dependencies; explicit reviewed schema/codec tests |
| Generics | Resolve concrete instantiations; reject unresolved type parameters |
| Interface unions | No automatic union inference; explicit discriminator and verified codec required |
| `time.Time` / duration | Reviewed special cases: JSON timestamp behavior versus duration's underlying integer; do not invent a duration string |
| Recursive structures | `$defs`/`$ref` with bounded extraction; reject unsupported cycles; runtime output/depth bounds remain necessary |
| Embedded fields / `,string` | Match JSON conflict/visibility and string encoding exactly or fail with file/type diagnostic |

## Build and immutable artifact model

Compilation is exclusively build/deployment-plane work. `bifrost run .` is a local
build tool followed by a local supervisor, not Rust admission invoking Go tools.
`bifrost deploy .` uses the same recipe plus reviewed deployment and attestation.
No runtime compiler, `go run`, `go get`, `go test` or dependency installation.
Initial target: Linux native, CGO disabled, one declared architecture per artifact.

Use conventional `go.mod`/`go.sum`, `cmd/workflow`, `internal/readiness`, tests and
manifest. Pin a verified toolchain and builder image digest before measurements;
set `GOTOOLCHAIN=local`, `GOWORK=off`, controlled GOPROXY/GOSUMDB, explicit environment
and flags. Reject network/toolchain auto-download or mutable local replacements;
permitted local replacements and embed files must be inside the hashed closure.
Set `-mod=readonly` for module builds; vendored mode is a separately identified
recipe with a hashed vendor tree. Verify go.mod/go.sum bytes are unchanged across
the complete pipeline. Readonly module mode does not itself prohibit downloads or
guarantee go.sum immutability; [module commands](https://go.dev/ref/mod#build-commands)
document the distinction.

Candidate pipeline: trusted archive ingestion/path/size checks; controlled module
fetch into quarantined content-verified cache; isolated `go test ./...`, `go vet
./...`, pinned `govulncheck ./...`; static metadata/schema validation; `go build
-mod=readonly -trimpath -buildvcs=false -o <output> ./cmd/workflow`; binary scan and
build-info extraction; digest/attestation creation outside the hostile builder.
All stages use pinned target/CGO/toolchain and never silently repair dependencies.
govulncheck provides reachability-aware vulnerability information, not proof that
code is safe; record scanner and vulnerability database identity/time. See
[Go vulnerability management](https://go.dev/doc/security/vuln/).

The artifact descriptor must bind: source-tree digest, reviewed repo/ref/commit and
deployment/source identity, manifest/input/output schema digests, artifact SHA256,
Go toolchain and toolchain digest, GOOS/GOARCH/architecture tuning, CGO=false, module
graph and module content/checksum identity, SDK version/content identity, all
effective build flags/tags, recipe/version, build entrypoint, executable entrypoint,
builder image/isolation identity/version, validation results and attestation.
Attestation signer is a separate trusted verifier, not a key mounted into tenant
tests. Hostile test code must not be able to overwrite final source/output or forge
successful stages: fresh verified source mounts and separate output custody are
necessary. Equal source or module hashes alone do not prove reproducibility.
Test two independent clean builds before making that claim; compare actual bytes
and metadata and report differences rather than hiding them.

### Builder isolation and caches

Candidate baseline: disposable nonroot container with read-only root and source,
bounded private temporary/output storage, dropped capabilities, no-new-privileges,
seccomp/AppArmor, CPU/memory/PID/disk/wall limits, and no Docker socket/host mounts.
Tests execute arbitrary tenant code; container isolation suitability for hostile
multi-tenant workloads must be accepted, not inferred from CGO=false. Evaluate
stronger sandboxing or disposable VM/microVM where the shared-kernel threat model
requires it; measure their overhead without disabling policy for speed.

No production DB/Redis, runtime SDK grant, vendor/customer credentials, signing
keys, cloud/managed identity, cluster-admin access, home profiles or arbitrary
internal egress. Module fetch has only controlled proxy/checksum-service egress;
compile/test can be offline after immutable dependency preparation. Tests never
receive proxy credentials or trusted attestation identity. Cache contents can
include private source and need tenant/access isolation as well as integrity.

Module download cache: verified immutable module bytes, trusted publishing only.
Compiler cache: writable scratch per build; trusted read-only seed imported into
scratch or approved overlay, never a globally tenant-writable cache. A read-only
GOCACHE directory cannot simply serve as a normal writable Go build cache. Final
artifact cache: attested CAS entries revalidated against all accepted inputs.
Do not promote hostile cache entries merely because their names look like hashes.

Cache identity includes full source/manifest/schema/embed/vendor/replace closure,
dependency graph/content, SDK, exact toolchain, target tuning, CGO, tags/flags,
recipe and trusted builder identity. Artifact reuse also checks accepted policy
and validation freshness; a new vulnerability policy may require rescan without
recompile. None of these is an execution/attempt/session identity.

## Common runtime changes needed before G3/G4

| Finding in current candidate | Shared change or acceptance gate |
| --- | --- |
| Hello artifact requires `interpreter.implementation/version` | Real language-neutral runtime/toolchain evidence; never pretend Go is an interpreter |
| `requirements_lock_sha256` is Python-shaped | Neutral dependency evidence with kind/identity; preserve honest Python environment evidence |
| Artifact ID is only a nonempty string; image digest nullable | Trusted accepted native digest, manifest and builder/adapter evidence; Hello is a report, not attestation |
| Only partial control messages are frozen | Scenario-driven input/context, Prepare/Prepared, LogBatch/Result and receipt contracts, with common fences |
| Go initialization precedes SDK handshake | Ratify common trusted-launch boundary with zero pre-Start tenant effects |
| Start duration is positive in C1-P0 | Specify finite workflow profile first; timeout-zero policy must follow shared decisions, not special Go behavior |
| Python exception type/traceback fields | Neutral failure categories plus optional diagnostic detail; panic is evidence, process exit is independent |
| Existing source/path/function custody | Reviewed immutable native artifact registration without Python import/cache assumptions |

Preserve framing/version/session/direction/sequence/correlation rules, duplicate-key
and lexical integer rejection, UTF-8/depth/size bounds and cancellation ambiguity.
Plain Go `encoding/json` with DisallowUnknownFields alone does not reproduce this
closed codec. A future adapter must pass shared malformed and valid vectors and
real bidirectional Python/Rust/Go decoding. Do not extend frozen closed fields
under an existing profile without a compatibility decision/capability negotiation.
Do not invent `bifrost.go.runtime/v1`.

Native supervisor acceptance must prove digest verification and launch of the same
immutable bytes without path-swap/TOCTOU, explicit environment/FD provisioning,
filesystem/identity/network confinement and resource limits before exec. No ambient
authority even during initializers. Pre-Start cancellation starts no tenant process.
After Start, cooperative context cancellation is supplemented by bounded process
termination and cgroup descendant cleanup/reaping. Stopped/result is not OS exit
proof. Crash, OOM, output flooding, ignored cancellation and spawned descendants
are required cases. Rust binds observations to its supervised handles/fences,
performs durable projection and serves existing APIs; binary claims cannot confer
trusted identity or final outcome.

Credentials reuse the dedicated common runtime-purpose architecture. #1024 is a
private foundation with closed integration-get/mapping-get, empty source permissions,
finite grants that cannot renew and timeout-zero 600-second revalidation windows.
It is not public ingress/provisioning. Do not hand Go an engine/application token,
invent a go-runtime account, or expose a raw workflow claim/delivery secret.
Positive capability use and negative ordinary-app/admin/lifecycle/foreign-org/
wrong-attempt/expired/revoked/upgrade tests must use the same actual provision.
Current OAuth `session.post` omits `allow_redirects=False`; the documented provider
redirect-confinement gate remains unresolved. SDK redirect rejection alone cannot
prove server-side provider target non-delivery. No OAuth repair is made here.

## Local loop and performance acceptance plan

Proposed loop: edit -> bounded syntax/type/metadata validation -> normal scoped
tests -> cached native build -> descriptor verification -> isolated local supervisor
-> post-Start SDK/log/result -> observed outcome. An immutable reused artifact skips
compilation. Local fixtures use synthetic orgs/integrations and no vendor secrets.
Production-deployment review, scan and attestation remain separate from exploratory
local iteration; both share build inputs/recipe. A reusable builder image is
materialized once, not rebuilt for every edit. No functioning `bifrost run/deploy`
command is claimed by this packet.

Measure in supported hosted CI or verified dedicated VM106 Linux Docker lane,
recording VM/worktree/Compose identities, toolchain/image/scanner pins, CPU/memory,
kernel/isolation mode, sample count and exact source/artifact digests. Do not use
physical-host measurements as runtime acceptance or restart VM101 APIs.

| Required metric | Method | Initial G0 result (current proof in results.md) |
| --- | --- | --- |
| Fully cold build | Empty module/compiler/artifact caches, include module fetch and validation phases | Not measured |
| Compiler-cold with warm modules | Verified module cache, empty compiler/artifact caches | Not measured |
| Small-edit warm rebuild | One identified handler edit, warm compiler/modules, invalidate artifact cache | Not measured |
| Dependency-cache behavior | One dependency change and repeat build; report hits/misses/downloads and unchanged module files | Not measured |
| Binary size | Actual executable bytes, debug/stripped policy recorded | Not measured |
| Startup | OS launch to adapter/workload readiness, initialization included | Not measured |
| Edit-to-runnable | Source-save to verified executable artifact and to completed local workflow, separately | Not measured |
| First execution | Accepted Start to committed result and API observation | Not measured |
| Memory / SDK overhead | Peak cgroup RSS, SDK request timing against synthetic API | Not measured |
| Cancellation | Request to cooperative return and to verified descendant exit/reap | Not measured |
| Artifact re-execution | Same digest repeated with no Go tools installed in runtime | Not measured |

Report median/p95/raw samples and failures; no performance superiority claim.
Suggested exploratory thresholds to ratify before G2: small-edit runnable artifact
median <=2s/p95 <=5s, cold offline compile <=30s, synthetic first run <=1s excluding
build. These are proposals, not user-specified or measured acceptance. Security
validation timing is reported separately and in total deployment latency.
Compare only identical read-shaped workload/input/API/limits on the same machine;
Python mutable-environment cold provisioning is distinct from warm interpreter
startup, as Go build time is distinct from native startup. Current Rust-owned
Python extraction is unaccepted, so no shared E2E performance comparison exists.

## Python comparison

| Concern | Python today / proposed extracted runtime | Proposed Go | Shared contract |
| --- | --- | --- | --- |
| Authoring | Normal Python functions/decorators | Normal Go functions/packages and small main wrapper | Capability API and registration |
| Dependencies | Current mutable installation/cache; custody unresolved | Modules and pinned pre-execution build | Honest dependency/deployment evidence |
| Materialization | Source/import and virtual imports after Start in extracted design | Exact native artifact, initialized only after Start | Reviewed adapter and accepted artifact |
| Lifecycle | Incumbent Python owns it today; Rust ownership is target | Rust ownership required before E2E acceptance | One admission/attempt/cancel/recovery/finalization model |
| SDK | Existing public Python SDK and scope semantics | Small typed client/interfaces | Same operation/caller/grant policy |
| Results | Current parent consumer projects evidence | Go reports evidence, never finalizes | Rust fenced durable projection and existing APIs |
| Testing | Python unit/reference and supported Docker lane | Ordinary go test with fakes; separate integration | No live instance needed for handler unit tests |
| Startup effects | Site/dependency/import hooks require extraction work | Package initialization before main | No user effects before committed Start |

Go exposes hidden assumptions in interpreter/requirements metadata, source/function
registration, import-based entrypoints, SDK ambient context and pre-Start workload
startup. These require shared abstractions; they do not justify weakening the
Python MVP, changing its mutable dependency behavior or porting agent/model loops.

Future Go/WASI may serve constrained CPU/data transformations with portability or
stronger sandbox confinement. It is not part of this spike's acceptance. SDK HTTP,
ordinary packages, process/resource control and toolchain support need their own
evidence; do not promise native compatibility or mandate WASM to solve today's
launch gate.

## Executed scope, blockers and next package

G0 executed: live main fetches, exact PR/source reconciliation, authoritative source
 audit, representative workflow selection and authoring/build/local-loop design.
 G1/G2 now implement the small SDK, ordinary-Go handler/tests, static schema profile,
 isolated builds/security scan/experimental attestation, native local SDK execution,
 cancellation and cold/warm measurements. See [results](results.md) for exact proof.
 Rust admission, common runtime protocol participation, actual restricted credential
 ingress and durable lifecycle projection remain unexecuted; no local fixture or
 measurement file is relabeled as those proofs.

 Static checks and supported hosted CI passed for the spike source; the first CI
 harness noexec failure was corrected in a new signed candidate, without security
 profile bypasses or rerun-until-green. The branch is published and committed, but
 no PR/pre-PR gate, merge, deployment or full platform suite is claimed. Primary
 checkouts are preserved and no debug/test VM stack was created.

Exact blocking prerequisites for G3-G5:

1. Shared trusted pre-Start launch contract that handles native initialization and
   has no Go-specific lifecycle transition, authority or replay exception.
2. Scenario-complete runtime artifact/workload/caller/context/log/result/receipt
   contracts and process custody; current C1-P0 lacks them.
3. Public restricted credential ingress/provisioning with closed request values,
   caller/external policy, stale-session denial, non-upgrading renewal and SDK/provider
   redirect target non-delivery. Private #1024 and test-only #1026 do not supply it.
4. Mechanical owner-aware writer exclusion, actual separate DB identities through
   PgBouncer, incumbent hidden-writer denials, recovery/mixed-writer/in-flight rollback
   acceptance, and implemented Rust workflow admission/finalization under them.
5. Reviewed native artifact/registration custody and production builder acceptance.
   Prototype toolchain/image pins, schema profile and developer-loop timings are now
   verified in hosted CI; hostile multi-tenant isolation, production attestation
   trust, interactive run/deploy ergonomics and reviewed deployment remain unaccepted.

G1/G2 now have independent runnable evidence and need no lifecycle authority. Do not make G4 a Go-specific substitute for
unaccepted C3. The smallest next step is a shared launch/artifact decision packet
for #1011/C1-P describing the trusted adapter, initialization-negative fixture,
source/artifact binding and compatibility vectors. The existing G1 SDK/handler and G2 isolated artifacts can then connect through
the accepted generic adapter while public credential/writer-exclusion work
proceeds through its own gates.

**Did Go fit naturally beneath the same Rust-owned execution model as Python?**
Architecturally plausible, operationally unproved. Capability calls and computation
need no Go lifecycle authority. Initialization and Python-shaped artifact fields
are concrete shared-boundary work; no Go-specific lifecycle code was added.

**Does the authoring experience feel like Go applications running on BiFrost or
a BiFrost DSL using Go syntax?** The proposed design is unequivocally Go applications
running on BiFrost: ordinary functions, explicit types, interfaces/fakes, errors,
context and control flow. There is no step graph or language redefinition. The prototype and normal tests support that authoring assessment; production
usability and Rust E2E acceptance are still unproved.

Final decision: **CONTINUE SPIKE**, with the exact blockers above. No demonstrated
language limitation currently warrants rejection; no measured/end-to-end evidence
currently warrants first-class adoption. Runnable computation and build evidence
now exist; shared Rust lifecycle acceptance remains the decisive blocker.
