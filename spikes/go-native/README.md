# Native Go authoring/build experiment (0.0.0-spike.2)

Temporary Lane 4 spike. This module demonstrates an ordinary Go handler, a small
public-HTTP capability client and an isolated native build/developer loop. It is
not registered or deployed, does not issue runtime credentials, and does not
implement Rust admission or the complete `bifrost.runtime/v1` execution profile.
The independent [control codec](runtimecontrol/README.md) implements the published
partial wire/session profile and exchanges encodings with Rust and Python.

Authoring starts in `internal/readiness`: normal functions, structs, errors,
context, interfaces and fakes. `go test ./...` is ordinary Go testing. The test
dependency go-cmp demonstrates conventional third-party modules; the production
binary uses the standard library only. Source tests in the supported lane run
without network, credentials or artifact/cache write access.

The application entrypoint is ordinary typed Go:

```go
func Run(ctx context.Context, in readiness.Input) (readiness.Output, error) {
    client, err := bifrost.ClientFromContext(ctx)
    if err != nil {
        return readiness.Output{}, err
    }
    return readiness.Run(ctx, in, client.Integrations)
}

func main() { bifrost.Workflow(Run) }
```

The helper infers generic types and supplies context cancellation, bounded typed
IO and capabilities without defining a workflow DSL. This entrypoint currently
requires `--local`; it does not pretend to be the unfrozen full runtime adapter.
Ordinary unit tests call the underlying handler with an interface fake.

The SDK makes the existing integration-get HTTP request with supervisor-supplied
scope and selector. HTTPS and redirect rejection are mandatory. Unit tests and
the local process harness use a synthetic HTTPS endpoint, not an actual restricted
runtime grant or production BiFrost. This is conceptual capability parity for the
selected readiness path; OAuth helpers, general retries and broad SDK parity are
not implemented.

In a supported disposable Linux Docker test lane:

```sh
bash spikes/go-native/scripts/measure.sh /absolute/disposable/evidence-directory
```

This downloads the pinned test module through the official module proxy, verifies
formatting, runs vet/tests offline, builds CGO-disabled Linux/amd64 artifacts,
measures a behavior-changing source edit and warm rebuild, executes each artifact
20 times against the HTTPS fixture in a scratch runtime without a compiler, tests
cooperative SIGTERM cancellation, and runs pinned govulncheck. No host Go runtime
test or full application stack is required. Do not use this script on the physical
Proxmox host or an arbitrary customer machine. The task's hosted-CI workflow is
the selected execution lane.

`cmd/workflow --local` accepts provision over inherited FD3 and typed input on
stdin; output is JSON on stdout. `cmd/probe` launches an already-built executable
with an explicit environment. Neither wrapper is a production runtime protocol.
The manifest is a design/build input and explicitly cannot register an entity.
Schemas are explicit and checked against a constrained build-time AST extractor
for single-file struct types containing strings, booleans and slices. Unsupported
types, generics, serialization hooks and cross-file ambiguity fail explicitly.
Business constraints such as a nonempty name remain explicit schema/handler rules.
The extractor's supported profile limits metadata inference, not the Go language;
the current proof does not yet supply a general build command for arbitrary types.

An isolated executable canary uses both a package-variable initializer and
`init()`, then blocks in main without receiving any Start input. Both effects
occur before the main gate. The trusted adapter must therefore delay application
process launch until validated Start and actual bound provision; moving checks
into this helper cannot provide that authority boundary.

Measurements, source digests, dependency graph, binary build info, scanner/image
identity and an experimental ephemeral Ed25519 descriptor signature are written
to the requested evidence directory. Its private key never reaches a builder or
workload and is deleted at task cleanup; this is not a production trust anchor.
Production attestation, stronger hostile multi-tenant isolation, independent cold
reproducibility, process-tree/resource stress, Rust result projection and real
restricted credential positives/negatives remain separate acceptance gates.

See [the decision packet](../../docs/architecture/go-first-class-spike/decision.md)
for the reconciled shared architecture and exact Rust integration blockers.

The CI artifact includes both compiled binaries. On a disposable Linux/amd64
developer test machine, the downloaded authoring proof can run without Go:

```sh
chmod +x workflow probe
./probe --artifact "$PWD/workflow" --output "$PWD/local-execution.json"
```

That starts synthetic HTTPS capability fixtures, executes the same binary 20
times, and exercises cancellation. It writes local measurement evidence, not
BiFrost durable lifecycle state. Use the pinned scratch Docker execution from the
measurement script for the isolated acceptance proof.
