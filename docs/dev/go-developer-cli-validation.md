# Go developer CLI: executed validation

The bounded developer-tooling goal is complete for the existing readiness
workflow. Use the [CLI commands and supported environment](go-developer-cli.md).
The product decision remains **CONTINUE SPIKE**: this local runner supplies no
production admission, durable lifecycle writer or additional runtime acceptance.

## Source and proof

Tested implementation: `ab664a968f2a5bacedb9d0d084ef8b0060f9b211`, branch
`spike/go-artifact-tools`, recipe `go-developer-cli/v1`, SDK `0.0.0-spike.2`.
The accepted spike source and its frozen evidence were not changed.

[Exact-source hosted CI](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/38990241480)
passed both tooling checks and the complete developer loop. The same scripts
also passed as existing nonroot `dev` (UID 1000) in Docker 29.7.2 on VM101,
`bifrost-platform-test-debian13-01`, Debian 13.7, kernel
`6.12.111+deb13-cloud-amd64`. Source checkout was clean at the tested revision.
Application code was neither compiled nor executed on physical pve-t340.

VM evidence is retained outside the accepted packet at
`/home/thomas/src/bifrost-go-spike-artifacts/go-developer-cli-vm101-ab664a968/`.
Its `evidence.tar.gz` SHA256 is
`765e59f269bc0e579e14dd80daaface895d9b2765fa6b7a38d9589d6e74e49ce`.
`verification.json` independently audits retained raw files; `files.sha256.json`
identifies the retained files. These local hashes identify bytes, not a signing
or production trust authority.

The audit checked all 147 source files against Git for cold/restored builds and
against the exact one-line behavior edit for the edited build. It rechecked all
three archive pins and their contents, scanner streams, test events and protocol
observations without running exported executables.

## Observed behavior

Each of five Go test runs passed 546 individual tests, with no failed or skipped
test cases. Six packages reported no test files. The metadata/archive suite
passed 23 tests. Builds also passed gofmt, vet and source/symbol govulncheck.
The scripts are `spikes/go-native/scripts/tooling-check.sh` and
`spikes/go-native/scripts/developer-loop-check.sh`.

The CLI exercised `test`, isolated readonly `build`, digest-pinned `inspect`, and
`run` without a compiler. Three runtime batches passed five cases each: success,
same-binary success, missing integration configuration, cancellation before
private material, and cancellation during an SDK call. Wrong archive pins were
rejected before execution. Ordinary results and logs used the existing common
protocol and synthetic bounded SDK fixture.

The original workflow executable SHA256 remained
`5f9c912ec6055882e0d443378cb93bb87b58eb7ab2a49eb5e14d338d72865edd`,
matching the previously accepted workflow. Its cold-build archive identity is
`sha256:95e60fca316b3fdd7362cffb1f759db86d61b01296587c4ea60389d79d94a59c`.
A real edit changed missing-key sorting from ascending to descending. The edited
executable was different and returned `["zeta", "alpha"]` instead of
`["alpha", "zeta"]`. Restoring the source reproduced the original executable.
Reexecution used the already-built artifact and performed no build/install work.

## Measured developer loop

Single VM samples; milliseconds rounded below. Cold means fresh module and
compiler caches. The pinned toolchain image and scanner build layers were
already cached. This is not a fully cold machine benchmark.

| Measurement | Cold | Behavior edit | Source restored |
| --- | ---: | ---: | ---: |
| Workflow compilation | 14,160 ms | 356 ms | 377 ms |
| Full guarded build | 59,246 ms | 12,880 ms | 13,059 ms |

Full build includes tests, scanning, cache copying and Docker work. The subsecond
compiler result is not total edit-to-run latency. Five-case local runner batches
took 6.53 seconds initially, 2.02 seconds on reexecution, and 3.82 seconds for the
edited artifact. Ordinary success/missing cases took approximately 148–178 ms,
including adapter startup, TLS/SDK fixture and reporting; process startup alone
was not measured. Held-call cancellation deliberately waits about one second.
Remote deployment was not implemented or measured.

The code and normal Go tools remain recognizable. Cached compilation is fast;
the approximately 13-second guarded rebuild and adjacent helper/image dependency
are the present developer-experience costs. The recipe supports this module,
not arbitrary Go application installation. Docker isolation here is a local
spike boundary, not qualified hostile multi-tenant production isolation.

## Failure corrected and cleanup

Earlier retained candidates failed before receiving a protocol frame. Stage
diagnostics located session-directory creation: Docker's auto-created directory
in an empty image was root-owned mode 0755. A nonroot red/green fixture confirmed
the cause. Explicit tmpfs mode 1777 fixed it; a regression assertion failed
before the fix and passed afterward. No protocol/security check was waived.
Earlier failures and candidate dispositions remain in the external evidence trail.

All seven invocation container inventories were empty. The acceptance script
removed its own scanner/runtime cache tags. Task directories
`/home/dev/bifrost-go-cli-126e8a302` and `/root/bifrost-go-cli-126e8a302` were
removed after evidence export; host transfer temporaries were removed too.
The shared pinned Go base image remains cached by explicit agreement.

VM101 remains running under the Rust owner's explicit acknowledgment
`RUST-VM101-ACK-20261017-01` for their active
`/home/dev/bifrost-rust-r1-f75da956b` checks. That owner will check for other active
work before eventual shutdown. Their worktree and unrelated resources were
preserved. The Go task has no remaining owned containers or active workload.

No merge, deployment, live vendor operation, credential expansion, renewal,
common schema/writer change or common-interface release occurred. Go still uses
BiFrost contracts rather than Rust implementation details; the same artifact
runs under the independent local supervisor. General recovery, common-core
integration and first-class production adoption retain their existing gates.
