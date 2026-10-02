# Runnable Go spike results

Decision: **CONTINUE SPIKE**. G1/G2 authoring, build and local execution are proved;
Rust admission and durable finalization remain blocked on the existing shared
runtime frontier. This is runnable evidence, not first-class acceptance.

Latest continuation: SDK0.0.0-spike.2 now provides typed `bifrost.Workflow(Run)`;
executable initialization canary, actual pooled identity/race tests and an observed
warm-edit output change pass in run36959239986 at430ab81f. See the
[full completion audit](completion-audit.md) for all18 requirements, exact current
source frontier, artifacts/timings and remaining protocol/security/authority gates.
The subsequent cascade regression failed as intended in36962051890; the immutable
composite-owner FK correction passes all jobs in36962353236 at8dbb5169, including
41 direct/15 pooled checks. The audit now points to that final executable candidate.

## Continuation: shared contract and writer-exclusion code

The spike continued beyond the original G1/G2 checkpoint below. Exact additional
tested candidate: `ba71c3e6acc75bce374e1693e449440345b50b18`.
[Hosted run 36956313136](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36956313136)
passed both the isolated build/runtime/interchange job and PostgreSQL ownership
job. Artifact source is not relabeled as a later documentation commit.

- Independent standard-library Go implements the published C1-P0 codec and
  parent-input session validator. All 99 wire, seven binary and 35 session vectors
  from unchanged #1015 `9f35278f90ba3757318ac9f07895bda9957330de` pass. Overall Go
  tests report 167 passing named tests/subtests, including the earlier suites.
- Actual Go encodings are accepted by the unchanged Python and Rust codecs;
  Python and Rust encodings are accepted by Go. Each direction validates nine
  owned synthetic golden frames. The original Rust contract crate also passes
  its four suites over the same corpus, with locked dependencies and offline
  execution after separate trusted fetching.
- A disposable PostgreSQL candidate passes 33 actual-login checks across Python,
  Rust and summary roles: positive ownership writes/readback, foreign finalization
  and deletion, immutable identities, bounded summary fields, and denied role,
  trigger, replication-setting, TRUNCATE and custom-setting bypasses. Runtime roles
  are neither table owners nor superusers/BYPASSRLS. This is a reduced fixture,
  not an accepted migration, pool identity proof or hidden-writer inventory test.
- The exact original workflow binary is unchanged: SHA256
  `9c853f132b8cdd3d30c2521179f46f0cc883765fcd122602f2ab28b6aeac6356`.
  It still runs 20 original and 20 edited samples plus cancellation in isolation.

New-run timings: cold compile with pre-resolved modules 11.517s, independent cold
11.006s, edited warm compile 275.97ms, edit-to-artifact 510.27ms, median execution
3.606ms/p95 3.962ms, median first SDK request 3.195ms and cancellation 0.315ms
(one cancellation sample). Module resolution/verification was 189.70ms. Runner
variation prevents claiming an optimization over the historical run below.
These remain local-authoring measurements, excluding a full lifecycle adapter.

Evidence is retained under
`/home/thomas/src/bifrost-go-spike-artifacts/ba71c3e6acc75bce374e1693e449440345b50b18/`,
including binaries, signed descriptor, test/scanner output, all four interchange
receipts and PostgreSQL check details. All actual tests ran in hosted CI; no
physical-host runtime, application stack, vendor operation or production change.

Two failures produced specific fixes without changing/omitting vectors:
run36955502455 found the Go binary reader classified zero-length framing incorrectly;
run36956068660 found rustup attempted a write to the immutable toolchain image.
The codec now preserves `InvalidFrame` for zero-length framing, and interchange
selects the installed exact toolchain explicitly. Successful evidence above is
from the corrected source candidate, not a rerun of either failing candidate.

Current architectural source readback: #1011
`59baf40f15103d1d4f579c1f5337eb5405df4882`, platform main
`e58db4955ddd30177bd613f1d85b7e203ad7832a`, workspace main
`9941427941587d253540942b714c5e2b7abfc410`. #1024/#1026 remain at their recorded
open heads. #1011 remains architecture-only and still gates authority-bearing
C2/C3 acceptance. The active goal is not closed by this checkpoint.

The concrete next contract change is in
[native-contract-revision.md](native-contract-revision.md): a common native
artifact observation and trusted pre-Start adapter that does not link tenant
packages. It preserves ordinary Go initializers by launching them only after
validated Start and bound provision. Current P0 mandates interpreter fields and
rejects Result; neither can be bypassed by relabeling Go or a fixture file.
Full native/Result/provision schema ratification and actual session/Start/receipt
authority with real writer exclusion remain the specific G3–G5 gates. The shared
workflow and agent model is retained; no smaller Go lifecycle was introduced.

## Original G1/G2 checkpoint

Procedure: mtg-engineering-flow 2026-09-30.1; MTG package 2026-10-01.5.
SDK/prototype version: **0.0.0-spike.1**. Go: **1.27.1**, Linux/amd64, CGO disabled.
No production, vendor mutation, credential expansion, merge or lifecycle schema
change. No Rust lifecycle code was added or replaced.

## Working artifacts and source

Exact tested platform candidate: `d2b21248656edf42a2deb1b64843c3698f1c01e7`, on
`spike/go-first-class-g0` in Midtown-Technology-Group/bifrost. The branch name is
historical; it now contains independently inspectable G0 and G1/G2 prototype work.
The candidate includes platform main `e58db4955ddd30177bd613f1d85b7e203ad7832a`.

[Hosted proof run 36953092175](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36953092175)
passed all its steps in 1m46s, including artifact upload. That duration includes
runner/image/tooling preparation and is not the local edit loop. Download the
run's `go-native-spike-d2b21248656edf42a2deb1b64843c3698f1c01e7` artifact for:

- `workflow`: compiled representative readiness workflow, **9,906,681 bytes**,
  SHA256 `9c853f132b8cdd3d30c2521179f46f0cc883765fcd122602f2ab28b6aeac6356`.
- `probe`: compiled local supervisor/HTTPS fixture harness; no Go installation
  is needed to execute its already-built workflow.
- `workflow-warm-edit`: separately identified binary from the small behavior edit.
- `descriptor.json`, dependency graph/build info, source closure and schema digests,
  test JSON, scanner output, both execution sample sets and timing files.
- Experimental Ed25519 descriptor signature and public verification key.

The same artifact is retained locally under
`/home/thomas/src/bifrost-go-spike-artifacts/d2b21248656edf42a2deb1b64843c3698f1c01e7/go-native-spike-d2b21248656edf42a2deb1b64843c3698f1c01e7/`.
Binary/source hashes and the descriptor signature were independently verified
after download without executing any workload on the physical Proxmox host.

Source entry points: [SDK](../../../spikes/go-native/client.go),
[ordinary handler](../../../spikes/go-native/internal/readiness/readiness.go),
[workflow main](../../../spikes/go-native/cmd/workflow/main.go),
[local supervisor](../../../spikes/go-native/cmd/probe/main.go),
[isolated build recipe](../../../spikes/go-native/scripts/measure.sh), and
[usage](../../../spikes/go-native/README.md).

## What actually ran

The handler takes normal Go structs, `context.Context`, a small consumer-owned
interface and an ordinary error return. It uses loops/branches and validates
integration name/configuration presence. Unit tests inject an interface fake.
The SDK uses the current POST `/api/sdk/integrations/get` request shape with an
explicit provisioned scope and optional Solution selector; no arbitrary workflow
input can change that scope through this handler.

The compiled process receives local fixture provisioning over FD3, typed JSON
input over stdin and emits typed JSON output. The supervisor makes an actual
HTTPS request/response round trip through the SDK to a synthetic scoped endpoint.
No live BiFrost grant, application token, customer credentials or vendor endpoint
is used. The fixture token is not an issued restricted runtime credential.
Successful runs return readiness without credential values; a blocked SDK request
is cancelled through SIGTERM -> Go context cancellation -> verified child exit.

This local mode is explicitly **not** `bifrost.runtime/v1`. A durable measurement
JSON file written by the local harness is **not a durable BiFrost execution result**.
There was no Rust admission, attempt/Start transaction, result receipt/projection,
existing execution-API readback or Go-specific lifecycle authority.

## Tests, validation and custody

Supported lane: GitHub Actions `ubuntu-24.04`, exact candidate above. Physical host
`pve-t340` was used only for source/tooling inspection and evidence readback, never
for workload/runtime tests. Keeper-backed VM access was unavailable; hosted CI is
the repository-supported alternative. No VM101/API stack was touched or restarted.

Actual commands in isolated containers:

```text
gofmt -l .                     # empty list required
go vet ./...
go test -count=1 -json ./...
go build -mod=readonly -trimpath -buildvcs=false ...
go mod download; go mod verify
go list -m -json all; go mod graph
govulncheck -json ./...
```

GOFLAGS supplies `-mod=readonly`; GOTOOLCHAIN=local, GOWORK=off, GOOS=linux,
GOARCH=amd64 and CGO_ENABLED=0 are explicit. go.mod/go.sum are read-only throughout.
Conventional go-cmp v0.7.0 is a checksum-identified **test-only** third-party module;
the workflow runtime binary links the standard library and local SDK only.
Tests report 24 passing named test/subtest results across SDK, handler and static
schema packages. Command packages have no unit tests; the process harness exercises
the workflow command. There are no intentional skipped test cases.

The SDK tests cover request scope/selector, precise JSON numbers, null/invalid/
oversized responses, sanitized HTTP errors, context cancellation including body
reads, configuration presence and all five redirect statuses with zero target
delivery. These are SDK/fixture tests, not production issuer/ingress authority proof.

Static extraction parses application source without running initializers. The
single-file profile supports string/bool/slice struct fields and JSON tags, checks
explicit schema drift and rejects unsafe types, generics and custom serialization.
It conservatively represents nullable slices. Cross-file types/methods, pointers,
maps, interfaces, custom marshalers and richer numeric/time types need a stronger
extractor or explicit reviewed schemas; no misleading general extraction is claimed.

Builder image is the pinned amd64 official image
`golang:1.27.1-bookworm@sha256:966278043a40889499db9b0cd196fc789c37c385d41bd9a10cb1e7764af60cdc`.
Scanner is govulncheck **v1.8.0**, source/symbol mode, vulnerability database
last modified **2026-10-01T20:24:15Z**; it returned success. The descriptor records
the actual scanner image ID. This is not a proof of hostile-code safety.

Tests run offline, nonroot, with read-only source/modules, dropped capabilities,
no-new-privileges, bounded CPU/memory/PIDs and private temporary scratch. They
cannot access the artifact output, final compiler cache, Git credentials, private
keys, production services or managed-identity endpoints. Module fetch is a separate
trusted tooling phase using only the official proxy/checksum service. Compiler
cache reuse is limited to compilation, not writable tenant test caches. No shared
final artifact cache or production admission policy is claimed.

Execution runs in a scratch image with only read-only workflow/probe binaries,
no compiler/shell/module cache or external network, a private result directory,
2 CPUs, 128 MiB memory and 64 PIDs. The output directory cannot mutate accepted
binary mounts. No broad signing/SQL/Redis/delivery authority is provisioned.
Full hostile multi-tenant isolation, OOM/output-flood/descendant/recovery stress
and writer-exclusion negative tests remain unproved.

Attestation uses a new ephemeral Ed25519 key only in the trusted build-plane
verifier, after workload/test containers have exited. The key is never mounted
into a builder or workload and is deleted on cleanup. Returned public key/signature
verify the exact descriptor binding source, module graph, schemas, recipe, builder,
toolchain/SDK and artifact digests. This is experimental provenance without a
production trust anchor or reviewed-deployment acceptance.

Two independent cold compiler caches and restored warm inputs produced byte-equal
workflow binaries under this pinned recipe. That is measured repeatability on
this lane, not a cross-environment reproducibility promise. The edited binary has
its own source/artifact identity and local execution evidence; full baseline
tests/scanning are not transferred to that edited development artifact.

## Measurements

| Metric | Actual result | Scope |
| --- | --- | --- |
| Module fetch + checksum verification | 77.1 ms | Empty private module cache; image pull excluded |
| Cold compile | 16.126 s | Empty compiler cache, prepared modules, 2 CPU/3 GiB builder |
| Independent cold compile | 15.963 s | Separate empty compiler cache, same pinned inputs |
| Small-edit warm compile | 373.8 ms | Real source change reverses missing-key ordering |
| Restored-input warm compile | 329.7 ms | Equal original artifact bytes |
| Edit to executable artifact | 560.3 ms | Edit + container launch + compile/type validation; full test/scan excluded |
| First local execution | 5.33 ms | Process launch through JSON output/child wait |
| Local execution median / p95 | 4.81 / 5.00 ms | 20 runs of the same artifact, synthetic HTTPS SDK call included |
| Launch to first SDK request median / p95 | 4.22 / 4.34 ms | Initialization, provisioning, TLS and request arrival included |
| Peak child RSS median / p95 | 10,916 / 10,916 KiB | Per-process Linux Rusage; supervisor/container overhead excluded |
| Cooperative cancellation | 0.40 ms | Signal to child wait/reap; one blocked SDK call |
| Binary size | 9,906,681 bytes | Default Go debug/build info retained; no size stripping |

Both baseline and edited artifacts execute 20 times without compilation; all raw
samples are retained. Cold/warm build counts are small and cancellation is one
sample; these are developer-loop evidence, not production capacity benchmarks.
The complete deployment-validation pipeline is longer than the subsecond edit
compile path. SDK-only timing, pure supervisor overhead, forced process-tree
cleanup and a fair current-Python/Rust-controlled comparison were not measured.
No Go superiority claim is made.

## Smallest blocker to the requested Rust durable vertical slice

Fresh readback pins platform main at e58db495 and workspace main at
`9941427941587d253540942b714c5e2b7abfc410`. The workspace advance changes NinjaOne
alert handling/tests only; the selected readiness reference bytes are unchanged.
#1011 advanced to **951c47f17f2d3c412044b4fb87beb70b64a1a251**. Its new freeze audit
and authority sequence remain explicit design candidates, with no C2/C3 release.
#1015 remains **9f35278f90ba3757318ac9f07895bda9957330de**. See
[rust-gate-readback.json](rust-gate-readback.json) for exact file hashes.

**There is no accepted shared Result acceptance/durable-finalization seam for a
Rust-controlled workflow.** The actual P0 Rust/Python code supports only
Hello/Start/Heartbeat/Cancel/Stopped, with asserted parent Start/prepared bindings.
It cannot accept a workload result or finalize an Execution. Main contains no
Rust workflow coordinator, and the inspected domain crate is an empty W0
foundation. A new Rust filesystem/SQLite result store would be a different proof,
not the requested BiFrost durable lifecycle result.

The existing C3 gate also requires database-enforced writer exclusion and live
session/close custody before authority-bearing implementation. The latest audit
requires child-validated Start **and** actual bound provision before tenant effects.
Go initialization before main still needs a reviewed common trusted-launch mapping.
Private #1024 remains unwired and cannot provide SDK ingress by itself; provider
redirect confinement remains separate. None was bypassed with broad tokens,
unrestricted test roles, Go-specific state or new credentials.

Smallest next package: ratify the common workflow Result/receipt/projection and
native trusted-launch binding at the #1011/C1 boundary, paired with the existing
writer/session gates; then connect this exact identified Go artifact through the
generic adapter and prove Rust admission, fenced durable projection and existing
Execution API readback under the same restricted provision. No production or
merge authority is implied.

## Developer experience and decision

The authored logic is recognizably Go: functions, packages, typed structs,
interfaces, error wrapping, context and normal tests. A third-party test dependency
works with normal module checksums. The measured edit/build path is subsecond.
The current explicit local-mode FD3 harness remains prototype ceremony; production
Workflow wrapping, local CLI/debug and reviewed deployment must follow the common
runtime contract before claiming first-class ergonomics.

**Did Go fit naturally beneath the same Rust-owned model as Python?** Computation
and SDK authoring fit without new lifecycle authority. Common Rust integration
is still unproved because its shared authority boundary is not implemented/accepted.

**Go applications or a Go-syntax BiFrost DSL?** Unequivocally Go applications
running on BiFrost capabilities. No step graph, workflow DSL or language
replacement was introduced.

Final decision: **CONTINUE SPIKE**. The authoring/build/performance evidence is
cautiously positive; a durable Rust-owned vertical slice is the remaining
acceptance boundary. No PR, merge or production deployment was performed.
The ordinary platform pre-PR/broad suites were not run: this is a disposable
spike branch with supported scoped CI evidence, not a mergeable product candidate.
All created workload containers use --rm, scratch/key cleanup completed and
the hosted job completed. No debug stack, VM or pre-existing volume was created,
reset or left running. Existing primary checkouts remain unchanged.
