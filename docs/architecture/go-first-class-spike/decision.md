# First-class Go spike decision

**CONTINUE SPIKE** — SDK `0.0.0-spike.2`, 2026-10-11.

Ordinary Go authoring, immutable native build, portable common-protocol local
execution and a real Rust-owned SDK/Result/cancellation slice now have runnable
artifacts. The latest fully inspected live/security proof is
`c739480ce7f79168d5df173158147f7be2d94700`: nine real-owner executions, actual
closed-Result crash recovery and uncertain release, 13,898 passing backend unit
tests,126 baseline and 248 guard cases, API quality and verified task teardown.
[Requirement-by-requirement audit](completion-audit.md).
[Historical design and source decisions](decision-history.md).

Thomas's approval at `b8b44cbc01b1fb6be86f4fba050e93eb0da55924` authorizes this
isolated implementation. It supplies neither runtime acceptance nor production,
merge, deployment, expanded credentials, renewal, profile freeze or more agents.
Python-owned executions and production dispatch remain unchanged.

## Working application and component boundaries

The representative workflow checks an organization-scoped integration's readiness
and required configuration keys, returning structured readiness/missing-key data.
The SDK exposes a typed integration reader; ordinary functions, interfaces,
control flow, errors and context do the work. Unit tests inject that interface.
The entrypoint is an ordinary main with `bifrost.Workflow(Run)`.

The public SDK and handler are independent of Rust. A separately compiled trusted
adapter implements the proposed language-neutral `bifrost.runtime/v1` profile.
The build plane tests/scans ordinary modules and packages an immutable archive;
the Rust owner admits and launches that accepted archive without compiling source.
No direct database/storage access, plugins, embedding, FFI or Go lifecycle DSL is
part of authored code. Tenant package initialization waits for committed Start
and verified restricted provision; the trusted adapter does not import the tenant.

The archive binds source/build evidence, actual adapter/workload digests, toolchain,
Linux/amd64/CGO0, module graph, SDK/protocol/recipe identity and explicit schemas.
A real migrated association binds it to an existing workflow/Solution deployment.
The workload binary remains 9,942,367 bytes at SHA256
`5f9c912ec6055882e0d443378cb93bb87b58eb7ab2a49eb5e14d338d72865edd` across executions.
Its archive identity changes when adapter/build evidence changes; source identity
must never be substituted for either binary or archive identity.

Metadata uses explicit neutral JSON. The static schema profile supports this
representative typed input/output; it is not a general Go schema generator.
Pointers/omitted/null fields, interfaces, custom marshalers, map keys, instantiated
generics, recursion, time types and tagged unions need explicit reviewed schemas
and codec tests where inference cannot be truthful. Unknown encoding behavior
must fail rather than emit a misleading schema. Rust consumes metadata, not Go AST.

## Evidence and developer experience

[Source-bound live/guard proof](nine-case-proof.json) covers nine
executions of the same Go binary: ready, missing-key, cancellation during a held
SDK response, lost Result COMMIT acknowledgement, closed-Result and closed-cancel owner crashes and
lost release COMMIT acknowledgement without material/SDK delivery, plus resource-
settlement ACK loss reconciled by exact readback without replay, and open-session
owner loss closed through the existing Rust cancellation/settlement writers. Rust alone owns
admission, Start, cancel, durable result and source settlement. Authenticated
existing Execution API readback confirms durable outcomes. SDK authority is finite,
attempt/session/operation-bound, rejected by ordinary routes, and has no lifecycle,
admin, vendor credential or renewal authority.

Ordinary Go tooling and tests work. The small wrapper and consumer-owned interface
feel like application code. The cold standard-library compile is noticeable;
warm edits are fast. The custom build/registration/protocol harness is still a
spike rather than a polished `bifrost run .` product. Static errors intentionally
avoid disclosing credentials through arbitrary application diagnostics.

| Measured concern, source `c739` | Observation |
| --- | --- |
| Module resolution/verification |85.578ms; small representative dependency graph, not arbitrary package resolution |
| Cold compile with resolved modules |16.899s |
| Independent cold compile |16.660s |
| Single-file warm compile |375.890ms |
| Restored warm compile |366.193ms; original byte equality verified |
| Edit to artifact |572.389ms; broader test/scan preparation excluded |
| Original-owner fixture executions |2.941–3.575s; includes Docker/SQL/fixture pauses/drain |
| Closed-Result closing actor |2.632s; excludes original execution before SIGKILL |
| Binary size |9,942,367 bytes |
| Remote deploy latency / fair Python lifecycle comparison |Not implemented/measured |

Separate authoring-only process startup/SDK/result samples are retained in producer
artifacts. They cannot stand in for Rust control-plane overhead. The build recipe
uses resource-limited, non-root containers with readonly inputs and no runtime or
production authority; hostile multi-tenant production isolation remains unaccepted.

## Python comparison and protocol findings

| Concern | Python | Go spike | Shared BiFrost contract |
| --- | --- | --- | --- |
| Authoring | Ordinary Python | Ordinary Go | Capability API, caller/input/result contracts |
| Source | Dynamic source | Standard Go module | Deployment/source identity |
| Dependencies | Python environment/import behavior | Build-time module graph and native artifact | Honest materialization evidence |
| Materialization | Import/exec through extracted runtime | Accepted adapter starts native binary | Start/provision, process custody and resource limits |
| Lifecycle | Incumbent ownership preserved; migration remains gated | Isolated real Rust owner transactions | Common attempts, cancellation, receipts and durable outcome |
| SDK | Python capability API | Typed integration reader | Same operation/auth/caller semantics |
| Result | Runtime execution evidence | Common Result/Receipt | Authoritative durable projection and existing Execution API |
| Local tests | Ordinary Python/fakes | Ordinary go test/fakes and common protocol probe | Independently implementable contracts |

Interpreter/requirements fields, import-based entrypoints, ambient SDK context and
native pre-main initialization were hidden language assumptions. The native
association, independent descriptor and separate trusted adapter address them
without exporting Rust types. Independent Go/Python/Rust codecs and vectors define
the proposed wire independently of serde. The profile still requires acceptance;
conformance is not production permission.

Does Go depend on language-neutral contracts or Rust internals? **The workload
and SDK depend on BiFrost contracts.** Rust implements isolated authority today.
Could another conforming supervisor execute the same artifact? **Yes in the
local proof:** the independent Go protocol probe executes the unchanged adapter/
workload with common messages and synthetic decisions. This supplies no production
security or durability claim. Does authoring feel like a Go application running
on BiFrost? **Yes for this workload.** General registration and local CLI UX need
more work before first-class product adoption.

## Exact remaining gates

The nine-case runtime and its source-bound writer/security suites are verified.
The corrected scanner and Go1.27.2 artifact now pass source-bound gates, including
resource-settlement ACK-loss reconciliation. The actual Docker create-response-loss component at `50ccb` is independently
verified; that candidate's full live/security lanes remain pending. Prove
interrupted partial-cleanup behavior without replay or reassignment,
and submit the concrete schema/identity/immutability/writer/common-lock-order
and runtime evidence for required acceptance.
[Audit](completion-audit.md) lists each unresolved case explicitly.

No current evidence supports ADOPT AS FIRST-CLASS LANGUAGE. The promising Go
application/build experience and existing vertical slice justify continuing the
bounded spike; they do not justify bypassing any protocol or security gate.

## Security audit correction and remediation

The older Go1.27.1 scan emitted 199 findings, including 164 reachable traces,
but its descriptor incorrectly treated JSON-mode exit status as a clean scan.
The SDK HTTP path was affected. That old behavioral proof was not accepted.
The corrected parser passed 11 hosted regressions and rejected the actual old
stream before descriptor creation. Go1.27.2 is pinned by verified image digest.
The replacement binary has a new explicit identity; the unchanged replacement
serves all nine cases. Its retained scanner stream contains zero findings.
[Toolchain change and guarded rejection](toolchain-remediation.json) preserves
both the old scan denial and rejection by the old byte pin. The initially missed
API fixture pin failed before admission with verified teardown; its correction
passed the complete seven-case run and this fresh eight-case run. No waiver,
retry-until-green, timeout increase or credential expansion qualified them.
