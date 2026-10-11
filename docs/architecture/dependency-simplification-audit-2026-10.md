# Dependency and hand-rolled infrastructure audit (2026-10-08)

Status: **initial source-backed triage; comprehensive reachability analysis outstanding**. No runtime changes are authorized by this document.

## Objective and decision rule

Minimize behavior Bifrost owns, not dependency count or LOC in isolation. Compare (a) existing library feature, (b) other already-installed library/stdlib, (c) maintaining a narrow implementation, and (d) keeping status quo. Security-sensitive, complex protocol, and cross-platform logic should usually remain delegated to maintained libraries. Score *net maintenance cost*, including upgrade churn, upstream merge conflict area, security obligations, transitive footprint, and impact on Rust migration.

Do not infer usage from the presence of a dependency declaration. For every proposed removal establish import/runtime reachability, optional/plugin/dynamic imports, dev/CLI/test use, compatibility, and lockfile effects.

## Verified evidence (default branch, 2026-10-08)

- [pyproject.toml](../../pyproject.toml) specifies Python >=3.11, HTTPX and aiohttp, PyGithub and GitPython, pydantic-settings and python-dotenv, APScheduler and croniter, regex, libcst, PyJWT and cryptography, MCP/fastmcp, Pydantic AI/Harness, keyring and pathspec. These **overlaps are candidates**, not confirmed duplicates.
- [cron_parser.py](../../api/src/services/cron_parser.py) validates with croniter but hand-parses five cron fields to produce descriptions. Keep the five-field contract, timezone aliases and DST behavior fixed during any replacement. Compare cron-descriptor versus the existing code and UI representation; adding a package may not be worthwhile.
- [editor/search.py](../../api/src/services/editor/search.py) uses private CPython `re._parser` to reject risky patterns and separately uses timeout-capable `regex`. High-value **spike**, not an automatic removal: check catastrophic backtracking, request-wide budget, pattern/input caps, matching semantics, and timeout handling.
- [core/telemetry.py](../../api/src/core/telemetry.py) parses cgroup v1/v2 accounting directly while registering OTel observables. Compare with metrics instrumentation and collector capabilities; avoid substituting host-level or single-process measurements for container-wide usage.
- [execution/README.md](../../api/src/services/execution/README.md) documents PostgreSQL production dispatch leases, attempt fencing, one-shot process isolation, and Redis ephemeral context; RabbitMQ remains a compatibility transport. **Do not** replace durability/ownership semantics with agent-harness abstractions or remove legacy deployment support without a migration decision.
- [README.md](../../README.md) describes the MTG production PostgreSQL delivery path and Docker Compose RabbitMQ default. Actual deployed configuration requires separate verification.

## Ranked investigation backlog (priority is for investigation, not permission to delete)

| Rank | Area | Hypothesis | Next proof | Disposition pending evidence |
|---|---|---|---|---|
| P0 | Editor search regex | private `re._parser` complexity walk duplicates safeguards supplied by bounded `regex` | adversarial benchmark and request-wide resource limits; CPython compatibility matrix | SIMPLIFY_IN_PLACE |
| P0 | Cron description | hand-maintained syntax formatting duplicates an established presentation library | feature matrix over supported expressions; output snapshots, timezone/DST tests | REPLACE_WITH_LIBRARY or KEEP |
| P0 | Agent runtime | Bifrost tool/agent plumbing may duplicate pinned Pydantic AI/Harness features | full call graph, installed API compatibility and newer-release changelog mapping; isolate Rust-owned guarantees | REPLACE_WITH_LIBRARY or KEEP |
| P1 | HTTP clients | HTTPX and aiohttp may overlap in outgoing HTTP calls | import graph, streaming/WebSocket/server-specific usages, connection pooling and behavioral comparison | REMOVE_DEPENDENCY if parity |
| P1 | Config loading | dotenv might be redundant with pydantic-settings | catalog dotenv reads, process/CLI bootstrap and override precedence | REMOVE_DEPENDENCY if parity |
| P1 | GitHub/Git | PyGithub and GitPython may exceed their consumed API surface | method-level consumer count, subprocess safety, OAuth rate limits and git plumbing semantics | REMOVE_DEPENDENCY or KEEP |
| P1 | Cron scheduling | APScheduler may overlap croniter's scheduling computations | source-level consumer tracing; misfire/coalescing/timezone parity | SIMPLIFY_IN_PLACE or KEEP |
| P1 | Metrics | cgroup parsing duplicates available instrumentation | verify process vs cgroup scope and overhead | REPLACE_WITH_LIBRARY or KEEP |
| P2 | CLI dependencies | keyring, textual, pathspec may be inappropriate in base server image | production-only import graph and CLI extras packaging proof | MOVE_TO_OPTIONAL_EXTRA |
| P2 | libcst | heavyweight dependency possibly needed for few transformations | import reachability and formatting-preservation fixtures | KEEP unless trivial |
| P2 | JSON/filters | jmespath/jsonschema versus custom payload matching/validation | schema and permission model mapping, fuzz test | SIMPLIFY_IN_PLACE |
| P2 | Storage/transports | Redis/RabbitMQ features could be retired behind authoritative Postgres | production and Compose compatibility matrix; audit cancellation, logs, synchronous waits | SEPARATE MIGRATION |

## Required deliverables for implementation agent

1. **Reproducible inventory**: pinned direct dependency list, production vs dev/test/CLI grouping, transitive dependency count/size and Python version constraints; include current commit SHA.
2. **Reachability**: Python AST-based static import mapping plus `importlib`, entry-point, and config-driven import checks. Include external CLI tools, Dockerfile, scripts, CI, workflow SDK, and optional runtime features. Mark unresolved dynamic imports as UNKNOWN, not UNUSED.
3. **Custom code discovery**: locate bespoke retry/backoff, parsing, config, serialization, schema adapters, task queues, scheduling, rate limiting, Git and HTTP clients, agent state/tool routing/compaction, cache management, and metrics collection.
4. **API parity table** for each candidate, naming the **exact installed version**, candidate updated version, API symbols and divergent behavior. Treat prerelease MCP/FastMCP and Harness as independent compatibility risks, not free upgrades.
5. **Weighted ranking**: measured eliminated behavior (not just LOC); test burden, exploit/availability risk, lockfile/transitive cost, code ownership and upstream-sync conflicts, and Rust migration lifetime. Provide a KEEP rationale.
6. **Bounded proof PRs**: tests first; one cohesive change per PR; no mass upgrades or transport/protocol changes bundled together; rollback path, bench evidence where relevant.
7. **Evidence standard**: report `dependency -> actual consumers -> used symbols -> replaceable behavior -> test coverage -> decision` with repository paths and commit-based citations; distinguish verified findings from hypotheses.

## Acceptance criteria

- Inventory covers all declared direct dependencies, all extras/dev/runtime groups, and flags import resolution uncertainty.
- At least ten highest-ranked candidates have source-path and used-symbol evidence, parity analysis, proposed disposition, and explicit risk/roll-back criteria.
- Top five spikes have reproducible fixtures or benchmarks and a GO/NO-GO outcome.
- No silent behavioral changes to tenant isolation, authorization, attempt fencing, code execution isolation, cancellation, cron DST rules, regex DoS limits, or upstream integration contracts.
- Any code deletion records dependency and transitive lockfile delta; no new tiny custom replacement for mature security/protocol behavior without a demonstrated advantage.
