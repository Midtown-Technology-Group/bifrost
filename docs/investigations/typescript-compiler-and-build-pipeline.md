# Native TypeScript compiler and build pipeline investigation

Date: 2026-10-08. Target: `Midtown-Technology-Group/bifrost`, frontend `client/`.

## Executive summary

The independent build-safety problem is confirmed: the production Dockerfile
invoked `npm run build`, which only bundled, while `build:full` ran a
source-mutating formatter. The implementation makes the authoritative build
require native TypeScript checking followed by the existing Vite build. It
preserves an intentional bundling-only command and all existing command names.
Rust remains outside the dependency graph; no compiler replacement or deployment
is part of this change.

**Compiler disposition: retain as an optional, advisory checker.** Full Rust
checking is about twice as fast with lower application-check RSS, and the benefit
survives a matching-source comparison. However, zero baseline diagnostics plus
one injected error class do not prove broad compiler correctness. Unsupported
platforms, provenance/version drift and incomplete watch readiness argue against
a required production dependency. No Rust alias or package is added.

**Build disposition: preserve orchestration with targeted fixes.** Production
builds now require native checking, formatting is read-only when verified, and
explicit incremental reuse makes unchanged developer checks much cheaper. Keep
sequential builds and current CI gates. Defer a blanket formatting CI gate until
its existing 637-file baseline receives an independently approved migration.

## Repository and tooling baseline

Application source baseline: `17fb2be097130013f64dc8331b911c8b8a200568`
(`origin/main`, inspected clean). Only package/build tooling, the local test
harness, this report and a reproduction utility change. Client package version
remains `0.0.1`; package-lock and application source are unchanged.

Execution used VM101, `bifrost-platform-test-debian13-01`, on the physical
`pve-t340` host named by repository policy. Worktree:
`/root/bifrost-ts-spike/repo`. VM: Debian 13, x86_64, eight host-model vCPUs,
16 GiB RAM, Intel Xeon E-2278G. Docker server: 29.7.2. The production Docker
base is the repository-pinned Node 26 slim digest
`sha256:c0753125a3789977aefe869cbebccf70e3cfd7ea84ca48547458f02e4f1d7146`:
Node **26.8.1**, npm **11.19.0**. Host Node 22.23.3/npm 10.9.9 were inspected
but did not run frontend validation or benchmarks.

Keeper session and access records were retrieved at use time. The named
`pve-t340.netbird.cloud` hostname did not resolve from the host itself; local
Proxmox state and VM configuration established the physical target. Commands
used the existing authenticated local Proxmox guest-agent transport, not a
replacement VM. VM101 was initially stopped and contained older exited stacks.
Those resources were preserved. This task's benchmark container was
`bifrost-ts-spike-check`.

Lockfile resolutions:

| Component | Installed version | Role |
| --- | --- | --- |
| React | 19.2.8 | Application runtime |
| Vite / Rolldown | 8.2.2 / 1.2.6 | Transform and bundle |
| TypeScript | 5.9.3 | JavaScript API compatibility |
| `typescript7` alias | `typescript@7.0.2` | Native semantic compiler |
| typescript-eslint | 8.68.0 | ESLint parser and rules |
| openapi-typescript | 7.13.0 | OpenAPI declarations |
| Prettier | 3.9.6 | Formatting |
| Vitest | 4.1.11 | Unit tests |

`npm ls typescript --all` confirms that OpenAPI generation, typescript-eslint,
its parser/project-service/type utilities, `ts-api-utils`, and `@shadcn/lint`
resolve 5.9.3. OpenAPI's peer range is `^5.x`; typescript-eslint's is
`>=4.8.4 <6.1.0`. These tools use TypeScript's JavaScript API. The native CLI
cannot replace that API dependency. Monaco uses its own browser TypeScript
worker; changing the CLI would not replace editor diagnostics.

The aggregate tsconfig references the application and Node-tooling projects.
Both are strict, use bundler resolution, skip library checking, and have
`noEmit`. Neither initially enabled `incremental`. Application checking
includes `src`, including unit tests and generated OpenAPI declarations;
Node-tooling checking includes `vite.config.ts` and its imports. Existing
native Go diagnostics were **zero**, exit **0**, in all three scopes.

## Compiler provenance and compatibility matrix

| Checker | Provenance | Scope of evidence |
| --- | --- | --- |
| Production Go | npm `typescript@7.0.2`, alias `typescript7`; native binary SHA-256 `4f2de678286401759b3fb4475bafe35b8f32b4b3a07d92642bbf37eadc9b34a4` | Installed production checker |
| Released Rust | `tsc-rs@0.1.0`, tag commit `72b339e412f2560549033cea8db337b1bd44c312`; Linux x64 archive SHA-256 `11dc30c9e69082acc9d9e05cd562e1334d361342b09950da58a6709adef4fda2` | Published binary, no install script executed |
| Matching-source Go | `microsoft/typescript-go` commit `dc37b5249ab60e2bbce936f71b883e6c8136167e`, 2026-06-19; built with Go 1.26.4, `CGO_ENABLED=0 GOAMD64=v1 go build ./cmd/tsgo` | Controls the Rust release's documented source pin |
| Current Rust source | Inspected main commit `3838d13535a9acaf8f68d6c50672ea5040cfb844` | Documentation/source inspection only; not compiled |

`go version -m` identifies the installed production binary's upstream revision
as `2bd066d87f5bafd315be9f40889d0a60b9e58e0b`, with `vcs.modified=true`,
Microsoft Go toolset `go1.26.4-1-microsoft`, `noembed`, trimpath and GOAMD64 v1.
This embedded metadata is more precise than assuming the later unified
repository's `v7.0.2` tag is the package's exact source tree.

The Rust release reports **7.1.0-dev**, not its npm version. The matching-source
Go build reports **7.0.0-dev**. Matching source does not erase this advertised
version difference, which can affect `typesVersions`. The oracle uses the same
released `.d.ts` library files alongside its binary so standard-library inputs
match Rust. Its binary SHA-256 is
`b87703992fea0c1af83c1bad0a15ae671c35446a411ee06b34b75e6bf9e49386`.

The published Rust profile uses Rust 1.95.0, release optimization, fat LTO,
one codegen unit, static musl and jemalloc, without PGO/BOLT. Comparing it to
upstream's PGO/BOLT headline measurements would be misleading. The custom Go
oracle uses ordinary optimized Go builds, not Microsoft's customized toolset.

There is an upstream provenance inconsistency: main's README claims
`microsoft/TypeScript` revision `673a5f17d713bdc8c7185f18a9c11e3c4ac5d781`
(2026-09-29), while main's `UPSTREAM.json` still selects `dc37b5249ab6`.
The stable v0.1.0 tag's machine-readable pin and `UPSTREAM.md` both select June.
No claim about current-source diagnostic parity follows from this experiment.

CLI packaging offers `tsc-rs -p ...`, `-b`, watch and separate `--lsp`/`--api`
entry points. npm's inspected `latest` is 0.1.0; `next` is 0.1.0-preview.2.
Released binaries cover Linux x64 and macOS arm64, not Windows or Linux arm64.
Documented limitations include build-order differences with missing project
references, extra emit in some workspace layouts, possible build-watch exit 70,
and editor-session memory growth. These are upstream warnings, not findings
that all occurred in BiFrost.

Primary sources:
[released source](https://github.com/pingdotgg/ts-rust/tree/72b339e412f2560549033cea8db337b1bd44c312),
[release packaging/profile](https://github.com/pingdotgg/ts-rust/blob/72b339e412f2560549033cea8db337b1bd44c312/.github/workflows/release.yml),
[release pin](https://github.com/pingdotgg/ts-rust/blob/72b339e412f2560549033cea8db337b1bd44c312/UPSTREAM.json),
[current README](https://github.com/pingdotgg/ts-rust/blob/3838d13535a9acaf8f68d6c50672ea5040cfb844/README.md),
[current pin metadata](https://github.com/pingdotgg/ts-rust/blob/3838d13535a9acaf8f68d6c50672ea5040cfb844/UPSTREAM.json).

## Diagnostic parity

Checks used `-p tsconfig.app.json`, `-p tsconfig.node.json`, and aggregate
`-b --force`, always with `--pretty false`. Build metadata was isolated and
cleared between independent runs. Full messages, multiline continuations,
locations, diagnostic codes, exit codes and unexpected output were retained
in scratch JSON. Normalization replaces only the disposable source prefix and
sorts complete diagnostic blocks; messages and codes are not rewritten.

| Scope | Go 7.0.2 | Rust release | Matching-source Go | Go-only / Rust-only / material differences |
| --- | --- | --- | --- | --- |
| Application project | 0 diagnostics; exit 0 | 0; exit 0 | 0; exit 0 | 0 / 0 / 0 |
| Node-tooling project | 0 diagnostics; exit 0 | 0; exit 0 | 0; exit 0 | 0 / 0 / 0 |
| Aggregate forced build | 0 diagnostics; exit 0 | 0; exit 0 | 0; exit 0 | 0 / 0 / 0 |
| Imported string → number fixture in watch | TS2322 at `(2,14)` in consumer | Same code, location and message | Same | 0 / 0 / 0 in the two completed error cycles |

No crash, exit 70, unexpected diagnostic output or unsupported CLI option
occurred in measured checking cases. Full diagnostic messages agree for the
positive control: `Type 'string' is not assignable to type 'number'.` The first
watch edit was not observed inside the bound, as detailed below; it is not
counted as successful diagnostic parity. No application-source change or
suppression was used to obtain these results.

A zero-diagnostic project alone is weak correctness evidence. Watch tests add
an imported dependency with a deliberate number/string mismatch, then restore
it, without editing application source or suppressing diagnostics.

## Benchmark methodology and measurements

Reproduction utility: [`scripts/benchmarks/typescript.py`](../../scripts/benchmarks/typescript.py).
It installs nothing and copies source into a temporary directory, symlinking
the existing node_modules. Outputs belong in scratch storage, not git.

Each applicable case has **ten measured repetitions and two separate warm-ups**.
The recorded runs shuffled checker order per repetition with seed 20261008.
The reproduction utility now orders checkers by SHA-256 of seed, scenario,
iteration and compiler name, avoiding a random-generator security finding while
retaining reproducible, varying order. JSON declares this ordering algorithm;
it does not reproduce the historical shuffle sequence. Full checks have
fresh processes and cleared project build metadata but warm filesystem pages;
true kernel/filesystem-cold checking was not measured because dropping caches
would disturb other guests. No dependency installs or compiler builds overlap
measured runs. The shared physical host remains a source of contention; VM
vCPU allocation is fixed and default compiler concurrency is retained. CPU
frequency controls are not exposed on this host, and other guests were not
stopped to manufacture an isolated result.

Timed checker scope includes GNU time, native process/Node-launcher startup,
configuration loading, checking, compiler cache writes, process shutdown and
stdout/stderr consumption. Source copying, cache deletion, incremental priming,
metric parsing and final temporary-directory removal are outside checker timing.
The reproduction utility aborts on any failed version query, priming run,
warmup or measured command; failed timings never enter successful summaries.
Version queries have a 30-second timeout, checking/build commands 180 seconds,
and timeouts kill and reap the command's process group. Pipeline RSS sampling
is stopped on success or failure. All measurements recorded here exited zero.
`scripts/benchmarks/test_typescript.py` exercises disposable source/cache
isolation, all checking/build modes, failed priming/warmups/measurements,
sequential short-circuiting, timeouts and memory-sampler cleanup. The existing
Sonar repository-tool Docker test step collects these tests and their coverage;
no quality-gate exclusion or rule suppression is added.
GNU time records peak child RSS and user+system CPU seconds. Go production
uses its existing Node launcher; Rust and the source oracle execute directly.
The approximately 20 ms launcher distinction matters for tiny Node-tooling
checks, not a multi-second application check.

`build-full` forces checking. `build-incremental` primes and then measures the
unchanged existing `-b` command. `build-incremental-enabled` primes and measures
`-b --incremental`. Persistent in-memory watch behavior is a separate test.
The current configs' build metadata alone did not make semantic checks
incremental; enabling reuse is evaluated explicitly.

p95 uses nearest rank: for ten measurements it equals the maximum. Dispersion
is sample standard deviation (SD) and median absolute deviation (MAD). These
are observed samples, not confidence intervals or a correctness proof.

| Checker / case | Median s | p95 s | Min s | Max s | SD s | MAD s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Go 7.0.2 / app-full | 4.699 | 4.788 | 4.470 | 4.788 | 0.099 | 0.052 |
| Rust release (first run) / app-full | 2.197 | 2.232 | 2.144 | 2.232 | 0.025 | 0.014 |
| Go 7.0.2 / node-full | 0.084 | 0.098 | 0.081 | 0.098 | 0.005 | 0.002 |
| Rust release (first run) / node-full | 0.036 | 0.039 | 0.035 | 0.039 | 0.001 | 0.000 |
| Go 7.0.2 / build-full | 4.717 | 4.928 | 4.562 | 4.928 | 0.114 | 0.093 |
| Rust release (first run) / build-full | 2.292 | 2.376 | 2.224 | 2.376 | 0.052 | 0.044 |
| Go 7.0.2 / build-incremental | 4.656 | 4.739 | 4.483 | 4.739 | 0.081 | 0.025 |
| Rust release (first run) / build-incremental | 2.260 | 2.302 | 2.219 | 2.302 | 0.027 | 0.020 |
| Go June source / app-full | 4.261 | 4.339 | 4.162 | 4.339 | 0.051 | 0.025 |
| Rust release (matched run) / app-full | 2.219 | 2.290 | 2.158 | 2.290 | 0.044 | 0.031 |
| Go June source / node-full | 0.058 | 0.061 | 0.051 | 0.061 | 0.003 | 0.001 |
| Rust release (matched run) / node-full | 0.037 | 0.039 | 0.036 | 0.039 | 0.001 | 0.000 |
| Go June source / build-full | 4.290 | 4.335 | 4.180 | 4.335 | 0.043 | 0.020 |
| Rust release (matched run) / build-full | 2.228 | 2.261 | 2.208 | 2.261 | 0.019 | 0.016 |
| Go June source / build-incremental | 4.268 | 4.400 | 4.108 | 4.400 | 0.076 | 0.029 |
| Rust release (matched run) / build-incremental | 2.258 | 2.488 | 2.231 | 2.488 | 0.095 | 0.019 |
| Go June source / build-incremental-enabled | 0.027 | 0.036 | 0.027 | 0.036 | 0.003 | 0.001 |
| Rust release (matched run) / build-incremental-enabled | 0.043 | 0.063 | 0.042 | 0.063 | 0.006 | 0.001 |
| Go 7.0.2 / build-cache-cold | 4.906 | 4.971 | 4.594 | 4.971 | 0.120 | 0.046 |
| Go 7.0.2 / build-incremental-enabled | 0.075 | 0.087 | 0.072 | 0.087 | 0.005 | 0.002 |

Every checker row returned exit 0 with zero diagnostics in all ten measurements.

| Checker / case | CPU seconds: median / p95 / min–max / SD / MAD | RSS MiB: median / p95 / min–max / SD / MAD |
| --- | --- | --- |
| Go 7.0.2 / app-full | 22.745 / 23.110 / 21.040–23.110 / 0.751 / 0.265 | 2118.275 / 2270.844 / 2094.137–2270.844 / 79.636 / 20.332 |
| Rust release (first run) / app-full | 8.880 / 9.040 / 8.790–9.040 / 0.079 / 0.055 | 1379.996 / 1382.113 / 1377.637–1382.113 / 1.711 / 1.533 |
| Go 7.0.2 / node-full | 0.305 / 0.380 / 0.280–0.380 / 0.031 / 0.015 | 74.078 / 76.988 / 71.551–76.988 / 1.772 / 1.230 |
| Rust release (first run) / node-full | 0.125 / 0.130 / 0.110–0.130 / 0.007 / 0.005 | 89.885 / 92.301 / 87.883–92.301 / 1.449 / 1.104 |
| Go 7.0.2 / build-full | 23.050 / 23.830 / 21.200–23.830 / 0.977 / 0.675 | 2131.072 / 2275.184 / 2116.258–2275.184 / 58.208 / 10.029 |
| Rust release (first run) / build-full | 9.210 / 9.570 / 8.970–9.570 / 0.187 / 0.110 | 1388.533 / 1392.867 / 1384.863–1392.867 / 2.418 / 1.812 |
| Go 7.0.2 / build-incremental | 22.550 / 22.750 / 20.600–22.750 / 0.765 / 0.195 | 2137.816 / 2273.129 / 2112.684–2273.129 / 72.410 / 20.410 |
| Rust release (first run) / build-incremental | 9.085 / 9.350 / 9.010–9.350 / 0.093 / 0.035 | 1388.471 / 1390.984 / 1385.582–1390.984 / 1.749 / 1.461 |
| Go June source / app-full | 20.645 / 21.240 / 19.560–21.240 / 0.493 / 0.290 | 1980.193 / 2063.965 / 1933.695–2063.965 / 39.890 / 27.477 |
| Rust release (matched run) / app-full | 8.970 / 9.080 / 8.810–9.080 / 0.087 / 0.075 | 1380.062 / 1382.500 / 1377.238–1382.500 / 1.917 / 1.664 |
| Go June source / node-full | 0.285 / 0.310 / 0.270–0.310 / 0.017 / 0.015 | 76.104 / 76.754 / 73.348–76.754 / 1.273 / 0.457 |
| Rust release (matched run) / node-full | 0.130 / 0.140 / 0.120–0.140 / 0.007 / 0.000 | 89.146 / 91.629 / 87.859–91.629 / 1.234 / 0.773 |
| Go June source / build-full | 20.785 / 21.390 / 20.240–21.390 / 0.392 / 0.300 | 1984.775 / 2021.613 / 1943.922–2021.613 / 30.524 / 29.236 |
| Rust release (matched run) / build-full | 8.970 / 9.170 / 8.870–9.170 / 0.090 / 0.060 | 1386.980 / 1391.801 / 1384.863–1391.801 / 2.106 / 1.518 |
| Go June source / build-incremental | 20.720 / 21.700 / 19.510–21.700 / 0.701 / 0.600 | 1994.578 / 2070.410 / 1948.523–2070.410 / 35.903 / 23.418 |
| Rust release (matched run) / build-incremental | 9.155 / 9.340 / 9.000–9.340 / 0.119 / 0.105 | 1389.258 / 1393.770 / 1387.758–1393.770 / 2.370 / 1.262 |
| Go June source / build-incremental-enabled | 0.020 / 0.030 / 0.020–0.030 / 0.005 / 0.000 | 26.680 / 28.707 / 24.605–28.707 / 1.285 / 0.174 |
| Rust release (matched run) / build-incremental-enabled | 0.035 / 0.060 / 0.030–0.060 / 0.009 / 0.005 | 36.932 / 38.332 / 36.926–38.332 / 0.591 / 0.002 |
| Go 7.0.2 / build-cache-cold | 22.890 / 23.310 / 21.140–23.310 / 0.694 / 0.360 | 2158.883 / 2316.660 / 2136.648–2316.660 / 64.657 / 15.941 |
| Go 7.0.2 / build-incremental-enabled | 0.080 / 0.090 / 0.070–0.090 / 0.007 / 0.005 | 51.463 / 51.656 / 49.418–51.656 / 0.655 / 0.021 |

## Sequential versus parallel builds

Vite alone, Go then Vite, and Go concurrent with Vite were measured with cleared
build metadata and a removed `dist` directory per repetition. Pipeline timing
includes child launch, output capture, metric parsing, executor setup/join and
combined-result collection. Preparation and final cleanup are excluded. All
children are waited for; each child's exit status is recorded. The experimental
harness is not a production task runner and does not declare an artifact
validated merely because Vite succeeded.

A second pipeline run samples descendant process-tree RSS every 50 ms. Shared
pages are counted per process, filesystem cache is excluded, and peaks between
samples can be missed. GNU-time individual RSS is also retained; the sum of
individual child peaks is an estimate, not a measured simultaneous peak.
Sampler overhead is included consistently in the second run.

| Run / mode | Wall s: median / p95 / min–max / SD / MAD | CPU s: same statistics | RSS MiB: same statistics |
| --- | --- | --- | --- |
| First (child RSS estimate) / bundle | 2.430 / 2.473 / 2.382–2.473 / 0.024 / 0.013 | 6.825 / 6.980 / 6.660–6.980 / 0.123 / 0.130 | 1719.875 / 1743.496 / 1695.625–1743.496 / 15.410 / 11.791 |
| First (child RSS estimate) / sequential | 7.131 / 7.323 / 6.985–7.323 / 0.096 / 0.063 | 29.445 / 30.060 / 28.430–30.060 / 0.528 / 0.430 | 2136.637 / 2273.934 / 2102.898–2273.934 / 60.306 / 19.428 |
| First (child RSS estimate) / concurrent | 5.574 / 6.406 / 5.335–6.406 / 0.325 / 0.158 | 31.960 / 36.930 / 29.830–36.930 / 2.087 / 1.385 | 3902.059 / 3940.660 / 3768.645–3940.660 / 59.089 / 32.057 |
| Second (sampled tree RSS) / bundle | 2.398 / 2.555 / 2.322–2.555 / 0.068 / 0.034 | 6.655 / 6.970 / 6.500–6.970 / 0.159 / 0.080 | 1711.561 / 1733.238 / 1675.109–1733.238 / 17.946 / 12.861 |
| Second (sampled tree RSS) / sequential | 7.062 / 7.327 / 6.824–7.327 / 0.134 / 0.073 | 29.005 / 30.670 / 27.840–30.670 / 0.840 / 0.440 | 2164.357 / 2276.988 / 2083.715–2276.988 / 77.810 / 75.836 |
| Second (sampled tree RSS) / concurrent | 5.641 / 5.986 / 5.290–5.986 / 0.217 / 0.151 | 31.940 / 34.150 / 28.990–34.150 / 1.620 / 1.180 | 3301.959 / 3414.500 / 3176.152–3414.500 / 85.162 / 73.139 |

All pipeline child exits were zero in the success cases. In the sampled run,
concurrency saved **1.421 s (20.1%)**, beyond the observed timing dispersion,
but increased median sampled peak RSS from **2164 MiB to 3302 MiB** and CPU
work from **29.01 s to 31.94 s**. The first run independently showed a
1.56-second saving. Concurrency therefore has a real cold-build benefit on this
VM, rather than only a favorable single run.

The production choice remains sequential: roughly 1.4 seconds per cold build
does not justify more failure/artifact orchestration and a 53% memory increase
here. Warm incremental checks leave almost no useful checking time to overlap.
These pipeline comparisons used the pre-change non-incremental checker command;
the separate cold-cache measurement quantifies the final explicit incremental
check's startup cost. Both layouts consume the same Vite configuration.
Parallel failure handling was not promoted to production: semantic failure with
Vite success would still have to invalidate the bundle; bundler failure must
likewise fail the combined gate. The actual adopted sequential command was
fault-injected instead.

The repository already separates cacheable CI quality, unit-test and image
jobs, with explicit dependency gates. Actual CI job-placement/startup latency
was not benchmarked as equivalent to local process concurrency. This PR keeps
those dependency edges and status-check names. There is no new task runner,
checker plugin, compiler/bundler coupling or permanent Rust dependency.

## Current and implemented workflow

| Command | Implemented behavior |
| --- | --- |
| `npm run build` | Native type check must succeed, then Vite/Rolldown bundles |
| `npm run build:fast` | Intentional Vite-only bundle; not a deployment gate |
| `npm run build:prod` | Existing alias to authoritative build |
| `npm run build:full` | Existing alias to authoritative build; no formatting mutation |
| `npm run typecheck` | Native compiler on both referenced projects |
| `npm run tsc` | Legacy alias, preserving forwarded checker arguments |
| `npm run lint` | Existing ESLint command |
| `npm run format` | Intentional Prettier source writes |
| `npm run format:check` | Read-only Prettier verification |
| `npm test` | Existing Vitest command |
| `npm run dev` | Existing Vite development/HMR command |
| `npm run generate:types` | Existing OpenAPI/TypeScript 5.x command |

A failed semantic check short-circuits the shell's `&&`, prevents bundling and
returns failure to Docker. Existing `dist` can remain after a failed local
check; file existence never proves that a build succeeded. Docker copies
artifacts from a successful builder stage, so failure cannot publish a final
production image. Build output and compiler metadata are writable artifacts;
application source is not rewritten.

## CI, Docker and deployment impact

Consumers inspected: `client/package.json`, production/dev/Playwright
Dockerfiles, all Compose files, `test.sh`, `debug.sh`, `.github/workflows/ci.yml`,
nightly browser tests, Sonar coverage, candidate promotion and release image
publication. There is no frontend root-workspace task runner.

Previously, Docker's builder ran Vite only. PR/merge-group lint separately
checked types, and candidate-image jobs depended on that status. However,
standalone Docker builds and protected-main image rebuilds could bundle without
checking their own source. The release workflow builds through the same client
Dockerfile after manifest verification. All now inherit the authoritative
semantic build; no known repository platform deployment consumer directly
calls `vite build` or `build:fast`. Independently authored Solutions app builds
are a separate product surface and were not altered.

The local `ci` image inherits the type-checked builder, so its CMD now runs only
lint and unit tests. `client_quality_checks` builds that target and runs scoped
ESLint without another type check. Thus the local intended pipeline checks types
once. CI's standalone type-check status remains: it verifies the reviewed
candidate/required quality lane, while each Docker builder validates the actual
image source. These are deliberately retained independent proofs, sometimes
for different review/merge/promotion trees. No required gate was removed.

Dependency downloads remain cached by setup-node keyed to package-lock; image
builds retain their existing GitHub Actions layer caches and immutable candidate
promotion. Changing npm script content invalidates the dependency layer once
because package.json is an input; it does not change lockfile resolutions.
Upstream DigitalOcean `deploy-dev` remains guarded by
`github.repository == 'gobifrost/bifrost'`; the fork's Azure boundary is unchanged.

Verified in Node 26 Docker on VM101:

- `build:fast` and authoritative `build` returned 0 and produced byte-identical
  bundle trees. Hashing source before and after build, lint and formatting
  verification found no changes (build metadata and dist excluded).
- After a successful incremental build primed metadata, a new TS2322 file
  caused `build`, `build:prod` and `build:full` to return 1 before Vite started.
  The previously generated bundle tree was unchanged. Removing the fixture
  restored checking success; `tsc -- --force --pretty false` forwarding passed.
- `docker build --target production --tag bifrost-ts-spike-production client`
  succeeded. The same Dockerfile with the TS2322 fixture in a disposable
  context failed, did not start Vite, and produced no rejected production image.
- Full client ESLint returned 0 with 1624 existing warnings. `format:check`
  returned 1 for 637 files without rewriting them. Those findings are preserved;
  no formatting CI requirement is falsely reported green.
- Script syntax and `git diff --check` passed. Repository shell/planner,
  API quality, generated-contract and remaining candidate gates are executed
  through `./test.sh pre-pr`; the exact final candidate's receipt and deferred
  comprehensive suites are recorded in the PR, rather than stamping a result
  from an earlier source onto this report.

Debug identity before boot: host VM101, worktree above, project
`bifrost-debug-cbfb514f`, DOWN with no project containers/volumes. The task's
`./debug.sh up` was interrupted while building images: the physical host's
kernel recorded global OOM killing QEMU PID 176714 in `101.scope`. The guest
also reached only 188 MiB free on its 158-GiB filesystem. No unrelated guest,
resource allocation, old stack or shared volume was changed to obtain proof.

Consequently live application API calls, browser behavior, HMR transitions
and actual OpenAPI regeneration were **not verified** here. Vite configuration,
`dev`, Monaco and generator commands/dependencies are unchanged; that is static
evidence, not a substitute for runtime proof. The T3 browser also failed its
initialization with an explicit AppArmor sandbox error. The branch-only CI
pre-PR lane is the supported remaining validation path and cannot publish dev
or release images under its event/ref conditions.

Cleanup was performed from the original VM worktree with `./debug.sh down`.
Successful Docker readback found no containers, networks or volumes for
`bifrost-debug-cbfb514f`; the task benchmark container had already been stopped
and removed. Older exited stacks and volumes were preserved. No debug stack is
retained for review. Task image tags and only task-created, unshared cache
records are cleaned separately; no global prune or unrelated image deletion is
part of this investigation.

## Cache and incremental findings

Project `.tsbuildinfo` files were already ignored by git but were not excluded
from Docker context. The PR adds `*.tsbuildinfo` to `.dockerignore`, preventing
host metadata from entering a fresh production image. Docker therefore checks
clean image inputs, while developers can reuse locally generated caches.

Vite production bundling does not replace TypeScript checking. Its existing
configuration, transforms, chunk groups and output target are unchanged.
Development dependency prebundling is separate from the measured production
build. No remote compiler-cache upload or artifact reuse outside the repository's
existing provenance rules is introduced.

The implemented checker is `tsc -b --incremental`. Production Go measured
**4.906 s** for a fresh incremental cache and **0.075 s** for an unchanged,
primed cache, versus **4.656 s** for unchanged `-b` without explicit reuse.
The apparent cold-cache overhead versus the earlier 4.717-second forced check
comes from separate runs and is not a controlled regression estimate. It is
small compared with the saved warm checking time. Docker gets no incoming host
cache; each builder checks its own source before accepting output.

A cached build rejected a newly added TS2322 fixture, rejected it through all
three production aliases, and returned to success after removal. No optimistic
`assumeChangesOnlyAffectDirectDependencies` setting is enabled. The two projects
retain separate default build-info files. No CI persistent build-info cache is
added; source/ABI/toolchain cache keys and cold correctness remain simple.

## Deeper integration and developer experience

Inspected `vite-plugin-checker` source commit
`cb12c655728ade8c36cb6cabaf0b47de82ea6904` (package 0.14.5), particularly its
[TypeScript checker](https://github.com/fi3ework/vite-plugin-checker/blob/cb12c655728ade8c36cb6cabaf0b47de82ea6904/packages/vite-plugin-checker/src/checkers/typescript/main.ts).
Its development path detects absence of the legacy `ts.sys` API and spawns a
native `tsc --watch`, including correct `-b` project arguments. It exposes a
`typescriptPath` module selector, not a general custom-executable setting.
Its production `buildBin` still names `tsc`; one cannot assume that changing the
development module selector makes build mode use the `typescript7` alias.

Thus current native-7 development support exists in source, but alias selection,
actual published-package behavior, Vite compatibility and shutdown behavior need
an integration experiment before adoption. Rust's API/launcher shape is another
compatibility question, not evidence supplied by a working CLI. No plugin was
installed. Editors/Monaco/LSP were not switched. There is no demonstrated
BiFrost developer-feedback problem that justifies adding a checker to Vite and
duplicating editor/terminal diagnostics.

Build-watch tests were bounded to three number/string change cycles per checker,
30 seconds per expected summary and five seconds for SIGINT shutdown. Both Go
versions and Rust missed the **first edit immediately following the initial
zero-error summary** within that bound. Two subsequent error/fix cycles produced
the same TS2322 and returned to zero errors. This shared observation does not
isolate a Rust-port defect; initial watcher readiness remains unresolved, and
is a blocker to claiming reliable plugin/watch integration. No timeout was
lengthened and the failed first cycle is retained rather than counted as green.

| Watcher | Completed later error/fix cycles | Error / recovery latency | RSS initial → final MiB | Shutdown |
| --- | --- | --- | --- | --- |
| Go 7.0.2 via existing Node launcher | 2 / 2 | about 9.4–9.6 s / 9.4–9.6 s | 2109 → 2137 | exceeded 5 s; process group forcibly cleaned |
| Rust release | 2 / 2 | about 2.2 s / 2.2 s | 1387 → 1404 | exit 0 in 0.064 s |
| Matching-source Go | 2 / 2 | about 3.9 s / 4.0 s | 2050 → 2452 | exit 0 in 0.064 s |

These short runs reveal retention, not a leak classification or a long-session
memory-growth rate. Compiler/watch background processes were bounded and
cleaned up. CLI results supply no Monaco or LSP compatibility proof.

## Limitations, risks and recommendations

- Keep native Go 7.0.2 as the required compiler and TypeScript 5.9.3 for API
  consumers. Re-evaluate a newer Rust release with unambiguous source/version
  provenance and broader error-bearing fixtures before proposing adoption.
- Use the pinned external Rust binary only for advisory experiments. Its
  roughly 2.5-second full-check saving exceeds observed variability, but the
  unchanged-build difference after cache reuse is only tens of milliseconds.
- Keep authoritative builds sequential. A future CI resource/latency budget
  may justify independent type and bundle jobs, but would need explicit final
  artifact gating and a fresh contention/failure benchmark.
- **Formatting-gate blocker:** read-only checking finds 637 baseline files;
  introducing a full-tree CI gate now would knowingly break the existing tree.
  Do not suppress these findings or mass-format them in this PR. An approved
  separate formatting migration is a prerequisite to enforcing that new gate.
- **Watch-integration blocker:** resolve the reproducible initial-edit
  readiness gap across both engines and verify shutdown before adding a Vite
  overlay. The observed failed cycles are part of this report, not waived tests.
- True filesystem-cold runs, isolated CPU frequency control, current Rust-main
  execution, Windows/macOS testing, broad language conformance, long-session
  leak analysis and editor/LSP integration were not measured.
- T3 browser initialization was attempted after status inspection and failed
  because AppArmor blocks its browser sandbox. Host security policy was not
  changed. Automated suites and actual API/HTTP proof are reported separately
  below; neither is mislabeled as successful browser interaction.
- Full unit, backend integration, product Playwright and MCP suites remain
  required CI work when the affected planner selects comprehensive validation.
  This investigation does not waive red checks, authorize deployment or merge.

## Reproduction

Use the repository's Docker lane on its dedicated test VM or CI, the pinned
Node Docker base, an `npm ci` installation, Python 3 and GNU time. Download the
v0.1.0 archive from the GitHub release, verify its digest above, and extract the
binary **with the adjacent lib*.d.ts files**. No npm install hook or downloaded
shell installer is needed. Keep every experimental binary outside the client
lockfile and production image.

From the repository root, with absolute installed paths substituted:

```sh
python3 scripts/benchmarks/typescript.py --client client \
  --compiler 'go=node /absolute/client/node_modules/typescript7/bin/tsc' \
  --compiler 'rust=/absolute/tsc-rs-0.1.0-linux-x64/tsc' \
  --builds > /tmp/typescript-results.json

# Repeat only the explicitly incremental scenario when evaluating cache reuse.
python3 scripts/benchmarks/typescript.py --client client \
  --compiler 'go=node /absolute/client/node_modules/typescript7/bin/tsc' \
  --scenario build-incremental-enabled > /tmp/incremental-results.json
```

For the matching oracle, download Go 1.26.4 (official archive SHA-256
`1153d3d50e0ac764b447adfe05c2bcf08e889d42a02e0fe0259bd47f6733ad7f`),
check out the exact June commit, and build:

```sh
CGO_ENABLED=0 GOAMD64=v1 GOTOOLCHAIN=local go build -o /tmp/go-oracle/tsgo ./cmd/tsgo
# Put the Rust release's adjacent lib*.d.ts files beside this oracle too.
# Add --compiler 'matched=/tmp/go-oracle/tsgo' to the benchmark invocation.
```

To reproduce safety checks, use a disposable client copy with installed
node_modules. Hash source and bundle contents before/after `build:fast` and
`build`. Add `src/__spike_fault.ts` with
`export const broken: number = "invalid";`, invoke `build`, `build:prod` and
`build:full`, and require nonzero exit, TS2322, no Vite startup and unchanged
previous bundle contents. Remove the fixture, then check both `npm run typecheck` and
`npm run tsc -- --force`. For watch recovery, use two exported fixture modules: change
an imported dependency from number to string and back three times, verify
TS2322 then zero errors each cycle, and terminate the entire watcher process
group with a bounded SIGINT/SIGKILL cleanup. Never edit production source to
make compilers agree.

Raw timings, compiler binaries, diagnostic logs and temporary build artifacts
remain scratch evidence and are deliberately not committed.
