# Rust core W0 foundation (0.1.0)

W0-A of [RFC #1005](https://github.com/Midtown-Technology-Group/bifrost/pull/1005).
Baseline: `d39aa0adf15ba12dfde5f2f39628cac88c2e98a0`; procedure
`mtg-engineering-flow` 2026-09-30.1. This package supplies internal operational
endpoints and importable primitives. Device business behavior remains gated on
W0 acceptance and the separate Python/Sopdet compatibility decisions.

The workspace graph is core → contracts/domain/db, db → domain, domain →
contracts. Contracts contains the internal health response; domain intentionally
has no device transitions yet. Alembic remains the sole migration owner.

## Configuration and lifecycle

Configuration has no `Debug` implementation. Startup errors name invalid keys,
never their values; driver errors and SQL are not logged. Database credentials
must be supplied at runtime. The SQLAlchemy `postgresql+asyncpg://` scheme is
normalized only at the leading scheme; PostgreSQL URL options keep SQLx meanings.

| Variable | Default | Bound / meaning |
| --- | --- | --- |
| `BIFROST_DATABASE_URL` | required | PostgreSQL host/user/database URL; shared Python convention |
| `BIFROST_DATABASE_POOL_SIZE` | 5 | 1–32; shared Python convention |
| `BIFROST_DATABASE_MAX_OVERFLOW` | 10 | 0–31; combined pool ceiling ≤32 |
| `BIFROST_RUST_BIND` | `0.0.0.0:8001` | Rust service only; never changes Python listener |
| `BIFROST_RUST_DATABASE_ACQUIRE_TIMEOUT_MS` | 2000 | 50–10000 |
| `BIFROST_RUST_READINESS_TIMEOUT_MS` | 3000 | 50–10000; complete readiness operation |
| `BIFROST_RUST_SHUTDOWN_TIMEOUT_MS` | 10000 | 100–30000 per HTTP drain and pool close |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | existing OTLP gRPC HTTP/HTTPS collector, no credentials/query/fragment/path |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` | other protocols rejected |

OTLP credential headers are not supported in W0 and fail configuration validation
if provided. TLS uses rustls ring and native CA trust for DB and OTLP, matching
the runtime image's CA bundle. No credential-bearing outbound HTTP client exists;
future clients must reject every redirect without a second request, including
same-origin redirects. HTTP redirects are not an endpoint discovery mechanism.

`GET /health` returns process liveness. `GET /ready` requires a live pool connection,
a nonempty Alembic version, existing device table types/nullability, PK/FK/check
constraints, the unique active-job index and SELECT access. The schema capability
floor is `20260924_device_job_logs` and its migration ancestors. New unrelated
columns/revisions can coexist; required capabilities cannot disappear. Checks
operate in a read-only transaction with a 2-second transaction-local statement
timeout. Statement caching and persistent named statements are both disabled for
PgBouncer transaction pooling. Future repository queries through `pool()` must
also set `.persistent(false)`; cache capacity alone does not prevent SQLx0.9
from creating named prepared statements. W0 uses
a static catalog query, not SQLx query macros; device SQLx offline metadata is a
later repository package's acceptance gate.

Readiness returns a sanitized 503 during outage, incompatible schema, timeout or
drain. Connections are lazy, capped, idle-recycled after 60 seconds and
lifetime-recycled after 300 seconds. HTTP requests have a 64-request concurrency
limit and reject overload; Tokio has two worker threads. Axum still owns socket
acceptance and HTTP connection tasks: this package does not establish a measured
production ingress connection budget. Keep these endpoints internal; public Python
health, route ownership, auth, watchdog and device handlers are unchanged.

SIGTERM/SIGINT stop intake, fail readiness, drain requests and close the pool.
The two configurable shutdown phases each have a deadline. OTLP flush is bounded
by an additional 8 seconds: SDK0.33 traces use a 2-second deadline, while metrics
shutdown internally uses 5 seconds despite its supplied timeout argument. Set
the container stop grace period above the combined deadlines (default 28 seconds).

JSON logs/traces record router templates, server-generated request IDs, service,
version/build SHA, status, duration, DB wait/transaction duration and drain/exporter
shutdown outcomes. Caller URLs, query strings, headers, bodies and raw driver/
exporter errors are excluded. Only authored sanitized tracing targets are enabled;
`RUST_LOG` cannot turn on dependency diagnostics. Optional OTLP traces use a bounded
512-span queue/128-span batches; request duration metrics use only bounded route/
status labels. No business metrics or new monitoring backend are introduced.

## Frozen bootstrap interfaces

- `Config::from_env()` / `Config::from_pairs(IntoIterator<Item=(String,String)>)`
  return `Result<Config,ConfigError>`.
- `DatabaseConfig::new(&str,u32,Duration)` returns `Result<DatabaseConfig,DatabaseError>`.
- `Database::new(&DatabaseConfig)` returns a lazy `Result<Database,DatabaseError>`;
  `readiness().await -> Result<(),ReadinessError>`, `pool() -> &PgPool`,
  `close().await -> ()`.
- `AppState::new(Database,Duration) -> AppState`, `application(AppState) -> Router`,
  `serve(TcpListener,AppState,impl Future<Output=()>+Send+'static,Duration).await`
  returns `Result<(),ServeError>`. `begin_shutdown()` marks drain/readiness state.

## Verification and evidence limits

Use the dedicated Linux Docker lane on `pve-t340`, preserving existing stacks:

```bash
docker build --target checks -f core-rs/Dockerfile core-rs
docker build --pull --no-cache --build-arg BIFROST_BUILD_SHA="$(git rev-parse HEAD)" \
  -t bifrost-rust-w0-bootstrap:0.1.0 -f core-rs/Dockerfile core-rs
./test.sh stack up
./test.sh rust bootstrap
./test.sh pre-pr
```

The shared `rust bootstrap` integration command runs `cargo test --locked --all
--features live-db` in the checks image against the worktree's isolated,
Alembic-migrated PostgreSQL through PgBouncer. `BIFROST_RUST_TEST_DATABASE_URL` is
mandatory with `live-db`; missing infrastructure fails, never skips. Its schema
mutations occur only in rolled-back transactions on the disposable database.
Default `cargo test --all` covers invalid config, unavailable DB, request-secret
sanitation and shutdown (including an in-flight readiness request); it does not
claim migrated-schema proof.

Rust toolchain/MSRV are both pinned to 1.98.1, the verified full graph baseline,
not a claimed lower compiler floor. Registry/official crate manifests establish
[SQLx0.9's Rust1.94 minimum](https://docs.rs/crate/sqlx/0.9.0) and compatible
[OpenTelemetry0.33](https://docs.rs/opentelemetry-otlp/0.33.0)/
[tracing-opentelemetry0.34](https://docs.rs/tracing-opentelemetry/0.34.0). Direct
versions and the complete Cargo lockfile are pinned; Docker base images use
immutable official multi-platform digests. Rust CI adds Docker fmt/clippy/test,
live-schema integration, cold image, and cargo-deny0.20.2 advisory/license/source
checks without replacing Python/client gates.

At initial implementation, `pve-t340` Docker26.1.5 on kernel7.0.14-19-pve denies
AF_UNIX stream creation under `docker-default` AppArmor. Official Rust images fail
in Rustup `UnixStream::pair` and Cargo/libcurl before compilation. No security
profile bypass was applied. Cold image, Docker Rust checks, live PgBouncer/schema
readiness, OTLP runtime behavior and supported-service startup remain **pending**
that environment repair or supported hosted CI. Exact PostgreSQL constraint/index
deparse comparisons are intentionally not called accepted before the mandatory
migrated-schema integration succeeds. Host compiler checks are supplementary
source evidence only. No merge, deployment, performance benefit or W1 acceptance
is established by W0.
