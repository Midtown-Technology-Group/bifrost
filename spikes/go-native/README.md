# Native Go authoring/build experiment (0.0.0-spike.1)

Temporary Lane 4 spike. This module demonstrates an ordinary Go handler, a small
public-HTTP capability client and an isolated native build/developer loop. It is
not registered or deployed, does not issue runtime credentials, and does not
implement Rust admission or `bifrost.runtime/v1`.

Authoring starts in `internal/readiness`: normal functions, structs, errors,
context, interfaces and fakes. `go test ./...` is ordinary Go testing. The test
dependency go-cmp demonstrates conventional third-party modules; the production
binary uses the standard library only. Source tests in the supported lane run
without network, credentials or artifact/cache write access.

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
Schemas are explicit; automatic schema generation is not claimed.

Measurements, source digests, dependency graph, binary build info, scanner/image
identity and unsigned provenance are written to the requested evidence directory.
Production attestation, stronger hostile multi-tenant isolation, independent cold
reproducibility, process-tree/resource stress, Rust result projection and real
restricted credential positives/negatives remain separate acceptance gates.

See [the decision packet](../../docs/architecture/go-first-class-spike/decision.md)
for the reconciled shared architecture and exact Rust integration blockers.
