# Rust device control-plane MVP: execution plan and evidence ledger

Status: STOP before W1/W2. W0 candidates are unaccepted; no traffic switch, merge or deployment.
Date: 2026-10-01. Architect and builders: Sol. Procedure package 2026-09-30.2
(engineering-flow 2026-09-30.1). This plan executes only Waves 0–2 of
[RFC #1005](https://github.com/Midtown-Technology-Group/bifrost/pull/1005).
The RFC is provisional: open at `2a70092eb773750d6bd082659c2a46b428748654`.
The latest user specification takes precedence over its Muse agent assumption.

## Baselines and gates

- Platform fetched main: `d39aa0adf15ba12dfde5f2f39628cac88c2e98a0`.
  Prerequisite #1001 is merged at this head.
- Workspace fetched main: `749197f6425a54a899bc596c9c33cdb2e6f9d2e2`.
  Prerequisite #1112 (`a05030ed42a7d2da808c1ec8020c050d87e19b31`) is an ancestor.
- Exact workspace-head audit: 2,095 authored Python files, no internal-import
  findings, empty allowlist; no workspace edit is authorized by this package.
- RFC CI is green except CodSpeed Performance Analysis. Owned repair
  [#1003](https://github.com/Midtown-Technology-Group/bifrost/issues/1003) remains
  blocking. No retry-until-green, threshold change or performance waiver.
- Supported local host and Docker daemon identify as `pve-t340`; existing Linux
  Docker lane. Record exact worktree/project and API endpoint with each run.
- Azure production configuration readback selects PostgreSQL for API/worker/scheduler.
  Process-level confirmation remains outstanding; see the go/no-go report.
- Real Go authority: `Midtown-Technology-Group/sopdet` main
  `8120838b113d9980d533af69ada385f7fa405138`, unchanged. Client/backend acceptance
  remains unproved; existing Go tests do not provide that proof.

W0 acceptance requires independent architect review, supported-image/service
startup and operational tests, Python reference characterization, and deliberately
introduced response/DB/event drift caught by the comparator. Do not implement
business behavior before those gates pass. Missing live configuration or Go
proof is a blocked gate, not permission to infer evidence.

## Current-main reconciliation

The clean-candidate gate fetched platform main
`c527ca1e4c0e1dc338b42542799290f6eba52ede` (#1004) during execution.
Candidates include this head. Its four changed files concern pinned workflow
module imports and related tests; device/auth/schema/instructions are unchanged.
The pinned F-01 source citations remain applicable. Workspace main is unchanged.

## Scope and Python ownership

Rust owns only a service foundation and, after gates, POST heartbeat, claim,
running, logs and result beneath `/api/device/`. Existing device tables,
constraints, public wire contracts and current Python behavior are authoritative.
No Alembic/schema ownership transfer or Rust migration history.

Python retains device registration/administration and control-key issuance,
job creation/list/cancel, watchdog scheduling, WebSocket authentication/fanout,
all workflows, process pools, dependency installation, virtual imports, SDK,
agents/model runtimes, MCP, scheduler, PlatformJobs, generic work delivery,
OAuth, storage and frontend. Sopdet stays unchanged. Runtime/workspace code needs
no Rust-specific accommodation. No PyO3, RabbitMQ transport or WinRM/PSRP.

## Packages, dependencies and ownership

### W0-A: production-shaped bootstrap

Scope: four RFC crates (`bifrost-contracts`, `bifrost-domain`, `bifrost-db`,
`bifrost-core`), pinned toolchain/lock, service configuration, bounded PostgreSQL
pool, internal health/readiness, sanitized tracing/OTLP, graceful shutdown,
container build and additive Rust CI/security/license checks.
Own: `core-rs/**`, `.github/workflows/rust-core.yml`. Architect owns shared
Compose/test.sh/affected-planner integration; do not edit those concurrently.
Dependencies: baseline source/config/schema; no W0-B code dependency.
Interfaces: importable application constructor/config/database primitives;
readiness checks existing device schema without applying migrations. Publish
exact signatures before W1. No device business logic in W0.
Contracts: existing deployment variable meanings and secret discipline; fail
startup on invalid config, readiness on unavailable/incompatible schema;
bounded tasks/DB resources; do not replace the public Python health route.
Non-goals: device routes/domain mutations, auth rewrite, deployment, migration.
Commands: cargo fmt/clippy/test using workspace manifest; cold container build;
bootstrap DB/readiness/shutdown checks; clean-candidate `./test.sh pre-pr`.
Stop/report: incompatible toolchain/dependencies/image, missing schema knowledge,
security-critical custom framework need, or CI changes that weaken current gates.

### W0-B: executable reference and differential harness

Scope: common endpoint scenarios, independent seeded snapshots, shared-DB race
mode, response/DB/event capture, explicit UUID/time normalization and deliberate
mutant detection. Python reference first; Rust unavailable is explicit until W2.
Own: `api/tests/parity/**`, `contracts/parity/**`. Architect owns shared harness
startup and CI integration; request changes rather than editing shared files.
Dependencies: Python source/tests/current Alembic schema; no Rust route dependency.
Interfaces: adapters take base URL/HTTP transport and evidence capture interface;
scenario outputs compare status/body plus committed DB and event observations.
Contracts: exact auth/error/state behavior, meaningful absence/null/order/scope,
no normalization of lease/fence/retry semantics; synthetic credentials only.
Non-goals: changing Python expected behavior, production mirroring, mock Go proof.
Commands: scoped `./test.sh tests/parity/...`, existing device E2E;
`./test.sh quality api`; clean-candidate `./test.sh pre-pr` in supported lane.
Stop/report: comparator cannot observe committed effects, drift gets normalized
away, unresolved source/schema/contract disagreement or unsafe fixture isolation.

### W1: typed state and SQL (blocked on W0 acceptance + delivery readback)

Own domain device types and SQL repository modules/integration tests; architect
freezes method signatures and transaction/event boundaries first. Preserve claim
ordering/SKIP LOCKED, 60-second claimed lease, fresh token/session, key-secret
bcrypt, touch commits, running replay, fence/error precedence, logs and result
limits. No unrequested session-matching check absent from reference behavior.
Acceptance: state/SQL concurrency, competing Python/Rust claims, reclaim/fence,
duplicate/conflicting logs, cross-device isolation and commit-failure tests.
Commands: cargo gates, scoped SQL/parity tests, pre-pr. Stop on unknown state,
weakened authority, lock/transaction-order changes or schema duplication.

### W2: five-route compatibility candidate (blocked on W1 review)

Own thin HTTP/device-auth handlers, required post-commit Redis hints, and bounded
routing integration. Preserve Pydantic coercion/validation/error/status behavior.
No generic Redis/WS rewrite. Rust libraries and Python fixtures compare actual
HTTP implementations, not copied mock internals.
Acceptance: complete parity matrix, real unchanged pinned Sopdet, Python
create/cancel/watchdog coexistence, faults and reverse route switching while a
job runs without data conversion or replay. Commands: scoped device E2E/parity,
real Go commands derived from its repo, cargo gates, quality/pre-pr.
Stop on spawn ambiguity replay, inability to safely share current schema,
credential forwarding, contract accommodation or unsafe rollback.

### Architect: integration and acceptance

Own this ledger, shared CI/test-stack/ingress wiring and frozen interfaces.
Each package uses an isolated worktree/branch and narrow PR referencing #1005.
No merges or production changes without explicit authorization. Separate reviewer
covers sensitive code with evidence; builders do not approve their own work.

## Evidence to maintain

- Executable specification and route/domain parity matrix with test references.
- Known differences, including rejected or separately approved reference bugs.
- Baselines, deployed transport readback, Go ref and test commands.
- Rust/Python ownership diagram and rollback runbook.
- Fixed benchmark workload/criteria before measurement; same host/environment,
  raw-enough startup/RSS/CPU/latency/DB/build and engineering-cost evidence.
- Go/no-go report: CONTINUE only on all user acceptance criteria; unresolved
  critical parity/security or external proof prevents declaring success.

Current recommendation: **STOP this device MVP before W1/W2**. The reference
protocol permits reclaim after possible spawn, contradicting the required invariant.
Do not silently port or fix it. Finish reviewable W0 foundations, then require a
separately reviewed protocol correction before resuming. See
[rust-device-mvp-go-no-go.md](rust-device-mvp-go-no-go.md).
