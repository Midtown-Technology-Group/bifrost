---
type: operations-guide
title: Development, Test, and Verification Lanes
description: Describes isolated development and test stacks, supported test entry points, contract generation, and verification evidence limits.
tags: [development, testing, verification, ci]
sources:
  - id: openwiki-source-33b1f81c9d8cf13263763671
    resource: repo://client/package.json
  - id: openwiki-source-f317ee207e1653d2033c81a4
    resource: repo://CONTRIBUTING.md
  - id: openwiki-source-f011a030ad13c406633402c0
    resource: repo://docker-compose.test.yml
  - id: openwiki-source-3314ba1db105e15769b2f32c
    resource: repo://docs/dev/ci-test-sharding.md
  - id: openwiki-source-ab020f2fc3594eaf2a194005
    resource: repo://docs/dev/delivery-lanes.md
  - id: openwiki-source-d3968563e820b43fb83a2fc6
    resource: repo://docs/investigations/typescript-compiler-and-build-pipeline.md
  - id: openwiki-source-357a1ac9413cdcd5189e8f59
    resource: repo://scripts/benchmarks/test_typescript.py
  - id: openwiki-source-b92edbf78b8b7768fa6b105c
    resource: repo://scripts/benchmarks/typescript.py
  - id: openwiki-source-99ba3eee13af09dff2711d92
    resource: repo://scripts/lib/pre_pr_stage_evidence.py
  - id: openwiki-source-f292591afd09474822a31fc0
    resource: repo://scripts/lib/test_helpers.sh
  - id: openwiki-source-06dfbbee2ae2d2a513ed6819
    resource: repo://test.sh
generated: { by: "bifrost-atlas", at: "2026-10-18T05:31:16.659Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T05:31:16.659Z
---

# Development, Test, and Verification Lanes

Bifrost uses repository wrappers to run tests in isolated Docker compositions and to keep worktree state separate. The default test composition is distinct from the long-lived debug stack, and CI further divides browser and backend end-to-end suites into independently provisioned stacks. See [deployment topology](deployment-and-infrastructure.md), [platform architecture](../architecture/platform.md), and [operations and observability](observability-and-operations.md).

## Development and test environments

The development environment is started and inspected through `debug.sh`, which assigns a project and volumes per worktree. For verification, `test.sh` selects `docker-compose.test.yml` by default and derives a test project name from the worktree. That test composition has no host-published ports and uses ephemeral database, Redis, and object-storage data so runs do not share the debug stack's state. PgBouncer is included to exercise the application's pooled database path. The wrapper's stack commands manage the lifecycle, while test commands require the test stack and API to be ready (`test.sh`, `docker-compose.test.yml`).

The test compose `init` service applies migrations and warms the module cache before API and worker services start. The `test.sh` wrapper provides unit, backend end-to-end, smoke, reliability, client, MCP conformance, quality, and pre-PR commands; backend suites and selected E2E paths are passed through the wrapper rather than running host pytest. A targeted pytest path may be supplied after the wrapper selector (`test.sh`).

## Wrapper scope and stack ownership

`test.sh` is the repository entry point for API quality, backend unit/E2E, client Vitest and Playwright, MCP conformance, repository hygiene, and Kubernetes experiments. The fast API `unit` selector excludes tests marked `slow`; `all` includes them, while a path after the selector passes focused pytest targets through the same Docker runner ([supported selectors](../../test.sh#L10-L40), [unit and all selectors](../../test.sh#L420-L430)). The test harness gives each worktree a separate Compose project and serializes stack-mutating/test commands with worktree-local locks, because a concurrent suite could reset shared test state underneath another run ([project naming](../../scripts/lib/test_helpers.sh#L5-L25), [runner lock](../../test.sh#L343-L360), [stack command lock](../../test.sh#L1125-L1138)).

`./test.sh pre-pr` is a clean-candidate gate: it refuses a dirty checkout, fetches `origin/main`, requires that base to be an ancestor of `HEAD`, and records the exact candidate SHA and affected-plan hash before running stages. Its default mode executes affected targets and leaves lanes selected as comprehensive to required CI; `--full` sets comprehensive scope and runs the full local client, quality, generated-contract, unit, E2E, browser, and image stages. It checks the candidate and worktree again before reporting success ([pre-PR candidate requirements](../../test.sh#L1022-L1094), [scoped stages](../../test.sh#L948-L996), [full stages](../../test.sh#L998-L1020)).

Stage reuse is conditional evidence, not a way to turn a skipped command into a pass: the ledger snapshots source SHA/status, Compose configuration, images and environment digest, tool versions, plus browser configuration for browser stages. A completed stage is reusable only when its context and relevant signature still match and the worktree is clean; otherwise the stage must run again ([stage snapshot](../../scripts/lib/pre_pr_stage_evidence.py#L39-L80), [reuse checks](../../scripts/lib/pre_pr_stage_evidence.py#L151-L173)).

## Verification lanes and generated contracts

Choose checks according to the surface: backend unit tests for isolated logic; backend E2E for API/database/queue boundaries; client unit tests via Vitest; and Playwright smoke or broader E2E for browser behavior. The wrapper also exposes API quality checks (Pyright and Ruff), repository hygiene checks, MCP conformance, and required `pre-pr` checks. The entry point describes `pre-pr --full` as exhaustive, so a scoped command should not be reported as the full suite (`test.sh`).

API contracts are generated from the running API's OpenAPI document into the TypeScript declarations. `client/package.json` defines `generate:types` against `OPENAPI_URL` with a local default; the repository contributor guidance requires regeneration after API contract changes. Generated output therefore depends on which API instance supplied the schema, and source-level presence alone does not prove it matches a deployed server (`client/package.json`, `CONTRIBUTING.md`).

## TypeScript and bundling benchmark harness

`scripts/benchmarks/typescript.py` is a comparative performance harness, not a normal correctness gate. It requires Linux, Python 3, GNU `/usr/bin/time`, a client checkout with `node_modules`, and explicitly supplied checker commands; by default it separates two warmups from at least ten measured runs and writes JSON results to stdout ([harness prerequisites and CLI validation](../../scripts/benchmarks/typescript.py#L1-L8), [options and minimum sample count](../../scripts/benchmarks/typescript.py#L180-L205)). It copies client source to a disposable temporary directory, links the installed dependencies, and measures selected full-project TypeScript scenarios and optionally Vite build pipelines. Reports retain per-run elapsed time, RSS, CPU time, exit status, output digest, and summary statistics ([scenario definitions and checker measurement](../../scripts/benchmarks/typescript.py#L86-L145), [temporary-copy and JSON report lifecycle](../../scripts/benchmarks/typescript.py#L180-L228)). The safety regression suite checks that source/cache/build outputs stay isolated in the fixture, validates scenario selection and minimum sampling, and exercises timeout termination and failure propagation ([benchmark safety tests](../../scripts/benchmarks/test_typescript.py#L33-L80), [isolation and failure regressions](../../scripts/benchmarks/test_typescript.py#L82-L170)). Benchmark output measures a specific toolchain and host; it is not evidence that CI or release gates passed.

### Historical compiler and VM101 evidence (2026-10-08)

The October 8, 2026 investigation kept native Go type checking in the production build before Vite and retained Rust as an optional advisory checker. It also kept TypeScript 5.9.3 for tools that use TypeScript's JavaScript API, which the native command-line compiler cannot replace. The Rust comparison did not establish broad correctness: source/version provenance and initial watch readiness remained unresolved, so the report made no claim of reliable watcher or editor/LSP integration. Treat these as findings from that investigation and verify current source before applying them to present-day changes ([build disposition](../../docs/investigations/typescript-compiler-and-build-pipeline.md#L7-L26), [JavaScript API compatibility](../../docs/investigations/typescript-compiler-and-build-pipeline.md#L65-L70), [watch readiness and recommendation](../../docs/investigations/typescript-compiler-and-build-pipeline.md#L445-L481)).

The public report records a prior VM101 investigation on the physical `pve-t340` host. It says Keeper access records were retrieved at use time, the named host did not resolve from the operator's host, and local Proxmox state established the target before using the existing authenticated local Proxmox guest-agent transport. This is dated historical evidence of one prior access method. Check current repository policy, Keeper authentication, VM identity and state, and authorization before relying on any VM path ([historical target and access notes](../../docs/investigations/typescript-compiler-and-build-pipeline.md#L35-L49)).

## CI sharding and evidence

Browser coverage runs across two GitHub runners, each with its own Docker stack and a single Playwright worker. Tests remain serial within a stack because specs mutate shared workspace and global settings; setup and MCP settings are project prerequisites. Whole files are assigned across four browser projects, and the aggregate gate depends on the full matrix. Backend E2E uses four independent stacks; measured per-file weights balance assignment but do not select or skip tests. For complete browser evidence, inspect both shard artifacts; a single shard's acceptance ledger only reports its own cases (`docs/dev/ci-test-sharding.md`).

Configuration and test definitions describe intended verification, not results. This documentation run does not boot a stack or execute tests, so it makes no claim about current CI or runtime health. When a test is run, report the exact wrapper command, selected suite, and resulting artifact or exit status; do not infer broader coverage from a focused lane.
