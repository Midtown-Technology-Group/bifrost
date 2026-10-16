# Go developer CLI

`spikes/go-native/bifrost-go` provides a local developer loop for the existing
readiness workflow module. Authored code remains ordinary Go: typed handlers,
context, functions, packages and normal tests. Python 3 orchestrates the trusted
build plane; neither authored Go nor the public SDK imports Rust internals.

Use a supported nonroot Linux/amd64 Docker development machine or the hosted
`Go developer tools` lane. Do not run builds/tests on the physical Proxmox host.
The commands require Python 3 and Docker; the host does not need Go or Rust.
Keep outputs and the cache outside the source directory.

```sh
cli="$PWD/spikes/go-native/bifrost-go"
cache="$HOME/.cache/bifrost-go"

"$cli" test --cache "$cache" --output /tmp/go-tests
"$cli" build --cache "$cache" --output /tmp/go-build

# Take the archive's artifact_id from /tmp/go-build/developer-build.json.
"$cli" inspect /tmp/go-build/native-bundle.tar --sha256 sha256:<archive-digest>
"$cli" run /tmp/go-build/native-bundle.tar --sha256 sha256:<archive-digest> \
  --output /tmp/go-run
```

Each output directory must be empty. Failed commands retain phase diagnostics
there and exit nonzero. `inspect` needs no Docker and never extracts or executes
the archive. Run outputs retain protocol observations, results and cancellation
checks as JSON; command stdout is also JSON.

## Build behavior

`test` and `build` snapshot the module so a concurrent source edit cannot change
the inputs halfway through compilation. `--source /path/to/module-copy` supports
an edited copy of this module. The source digest identifies actual snapshot
bytes; a Git commit is recorded when available, including for a dirty checkout.
The snapshot digest, rather than the commit alone, identifies a developer edit.
This recipe expects the existing module's `cmd/workflow`, adapter, schemas and
explicit manifest; it is not yet a general installer for arbitrary Go modules.

The pinned Go 1.27.2 container resolves/ verifies standard module dependencies
through the official proxy and checksum service. Source and dependency mounts
are readonly during offline compilation and tests. CGO is disabled and module
updates are forbidden. No production database, vendor, SDK or signing credentials
are supplied. Build/test containers receive no Docker socket.

Compiler caches are private to the invoking user and keyed by toolchain,
recipe and dependency declarations. Go's compiler cache handles source changes;
final artifact identity additionally binds the complete source digest. Trusted
compilation prewarms test packages without executing them. Tests execute against
a disposable copy of the compiler cache, so test code cannot poison the cache
used by later builds. Each invocation serializes cache access. This is a bounded
local developer recipe, not accepted hostile multi-tenant production isolation.

A build runs gofmt, vet, ordinary `go test ./...` and pinned govulncheck. The full
scanner stream is checked for reachable findings before deployment metadata or
an archive is emitted. It builds the workflow, trusted adapter and local tools,
then reuses the canonical immutable bundle recipe. `descriptor.json` identifies
source, toolchain, manifest, dependency graph, schema and executable bytes;
`native-bundle-descriptor.json` identifies the archive separately. A source digest,
a workflow digest and an archive digest are different identities.

`developer-build.json` reports workflow compilation and full-command timings,
including whether a warm compiler cache was present. Full-command latency also
includes tests, scans, cache copying and Docker work. The CLI does not invent
producer authentication or a production trust anchor from these local reports.

## Local execution

`run` validates the supplied archive pin, stages only verified fixed entries and
uses the adjacent prebuilt protocol supervisor/vectors from that build output.
Their recorded hashes are checked and snapshotted before execution. The pinned
empty runtime image must already exist from the build plane. Keep the full build
output together; copying the archive alone does not copy these local tools or
prepare another machine's runtime image.

No source, module cache, compiler, dependency installer, external network or
live credentials are available to the run container. The same already-built
workflow executes through the language-neutral protocol with synthetic SDK
fixtures. The local supervisor exercises success, same-binary reexecution,
missing configuration, cancellation before private material and cancellation
while an SDK call is held. It does not provide Rust admission or durable result
projection. Rust remains authoritative for those production responsibilities.
This is a readiness-workflow local runner, not a generic live vendor simulator.

For the existing example's behavior-changing edit from ascending to descending
missing-key order, build the edited module copy and use `run --descending` to
select that expected fixture behavior. Normal source tests still run unchanged;
this option does not waive test, schema or protocol validation.

Containers and temporary source/private test-cache directories are disposed on
success or failure. `cleanup.json` records the exact invocation's container
inventory. The explicit developer cache and its named scanner/empty runtime
images are retained for iteration; `image-references.json` identifies them.
The hosted acceptance loop removes its own cache image tags and disposable
cache after verifying all invocation inventories. It preserves borrowed images
and never performs a global Docker prune.

The accepted Go spike and its evidence remain unchanged. This CLI adds no
public runtime messages, restricted operations, renewal, lifecycle writer,
production dispatch, deployment or merge authority. Common-interface changes
remain with the Rust workstream; a local report is not runtime acceptance.
