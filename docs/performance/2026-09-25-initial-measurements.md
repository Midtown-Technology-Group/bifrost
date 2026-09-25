# Issue 890: initial production and lab measurements

## Production observation

On 2026-09-24/25 UTC, a read-only Azure Monitor query over the preceding 24 hours returned 10,631 requests, 230 HTTP 5xx responses, a mean of hourly mean working-set samples of 1.70 GB, and a maximum working-set sample of 3.00 GB for `app-mtg-bifrost-production`. The plan is one B3 instance. Live settings contain `BIFROST_MAX_CONCURRENCY=4`, `BIFROST_MAX_WORKERS=4`, `BIFROST_DATABASE_POOL_SIZE=2`, and `BIFROST_DATABASE_MAX_OVERFLOW=3`.

This is App Service aggregate telemetry, including its API, scheduler, worker, client, and renderer containers. It provides neither endpoint mix nor per-role CPU. All three `OTEL_*_EXPORTER` settings are `none`; the code's required `OTEL_EXPORTER_OTLP_ENDPOINT` is absent. There are no App Service diagnostic settings. Consequently the current production deployment cannot yet provide the span and role attribution required for a Go decision. The 5xx count is an observation, not an attributed failure cause.

App Service application and HTTP file/blob logging are also disabled. The Talos observability cluster has an OTLP collector and Tempo, but its gRPC receiver is currently exposed as a cluster-only service; a secure, reachable production export path has not been proven. Production tracing should start only after that route, sampling, retention, and export overhead are checked.

Source: `az monitor metrics list` for the production App Service resource with `--metric Requests Http5xx MemoryWorkingSet --interval PT1H --offset 1d --aggregation Total Average Maximum`, plus read-only `az webapp`, `az appservice plan`, and site-container configuration queries.

## Isolated lab scope

The dedicated `bifrost-platform-test-debian13-01` guest on `pve-t340` has eight vCPUs and 16 GiB RAM. The issue worktree uses the repository's Docker test stack and `scripts/issue-890-run.sh`. The issue-specific Compose override disables coverage for API and scheduler and sets four worker slots. The API scenario is authenticated `GET /api/profile`; the workflow scenario submits a tiny function and checks the completed result. State is reset before each sweep.

These two scenarios establish harness behavior and a first queueing curve. They do not reproduce production endpoint mix, external API/model latency, B3 CPU and memory limits, renderer load, or remote database behavior. Container CPU readings were point samples, not a complete attribution trace. No language comparison is valid yet.

Early sweeps found two benchmark hazards. The default test stack ran API and scheduler under coverage. The first HTTP driver kept only 20 idle connections at concurrency 128, causing a false throughput collapse and several-second tail latency. A 99 Hz `py-spy` attach also fell behind and disturbed a separate run. Those runs are excluded from capacity conclusions. The committed harness removes coverage, keeps up to the requested number of connections, rejects fewer operations than the highest concurrency level, and requires a clean worktree so `source_sha` identifies exact code.

### Authenticated profile read, clean commit `2e707cd2d`

Command: `bash scripts/issue-890-run.sh --scenario api-read --output /tmp/bifrost/issue-890-api-read-2e707cd2d.json`. Each level completed 1,000 requests with zero failures. [Raw results](results/2026-09-25-api-read-2e707cd2d.json) contain the full source SHA, Python 3.14.7, architecture, and percentiles.

| Concurrency | Requests/s | p95 (ms) | p99 (ms) |
| ---: | ---: | ---: | ---: |
| 1 | 209 | 6 | 7 |
| 4 | 288 | 17 | 37 |
| 16 | 269 | 78 | 97 |
| 32 | 288 | 128 | 163 |
| 64 | 288 | 294 | 505 |
| 128 | 266 | 661 | 925 |

This narrow API read did not show a material throughput collapse through concurrency 128 after the client pool was fixed. Tail latency still rises with contention. This result does not determine whether Python CPU, database, or another component is limiting a production workload.

### Small workflow, clean commit `70b0daa10`

Command: `bash scripts/issue-890-run.sh --scenario workflow --operations 200 --output /tmp/bifrost/issue-890-workflow-70b0daa10.json`. All 1,200 executions returned the expected result, with zero failures. [Raw results](results/2026-09-25-workflow-70b0daa10.json) and [one-second cgroup samples](results/2026-09-25-workflow-resources-70b0daa10.jsonl) preserve the observations.

| Concurrency | Executions/s | p95 (s) | Worker CPU cores | API CPU cores | PostgreSQL CPU cores |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 10.5 | 0.11 | 0.72 | 0.20 | 0.14 |
| 4 | 22.3 | 0.21 | 1.57 | 0.40 | 0.26 |
| 16 | 24.6 | 0.76 | 1.79 | 0.45 | 0.26 |
| 32 | 25.3 | 1.31 | 1.79 | 0.43 | 0.25 |
| 64 | 24.3 | 2.74 | 1.77 | 0.41 | 0.25 |
| 128 | 22.2 | 6.10 | 1.57 | 0.42 | 0.24 |

CPU cores are the difference in cgroup `usage_usec` divided by time between the first and last one-second samples wholly inside each load level. The worker is the largest measured CPU consumer during this tiny workflow, but it averages under two cores when throughput plateaus. These counters do not distinguish Python CPU from native-library CPU, process startup, DB wait, queue wait, or other blocking. The scheduler briefly consumed CPU during startup and was near idle in the sustained levels. The workflow payload is too small to represent integrations, agents, or production workflow mix.

### API repeat with resource sampling: high-load discrepancy

A [second API sweep](results/2026-09-25-api-read-70b0daa10.json) with [cgroup samples](results/2026-09-25-api-read-resources-70b0daa10.jsonl) completed levels 1–64 at roughly 224–301 requests/s with zero failures. At concurrency 128 it completed 998/1,000 requests at 129 requests/s, p95 3.80 seconds, and two connection read errors. The API container was not OOM-killed or restarted. During that level it averaged about 0.44 CPU cores, down from about one core at levels 16–64; PostgreSQL and PgBouncer CPU also fell. This does not look like sustained API CPU saturation.

Scheduler CPU briefly reached about two cores around its startup jobs during this short sweep, then settled. This repeat began immediately after recreating services, so startup work is a plausible confounder, but the cause of the two read errors remains unproven. The harness now waits ten seconds after readiness and records load-client CPU for the next repeat. Keep both successful and failed runs; do not classify this discrepancy as a Python bottleneck or ignore it as random noise.

A [warmed 128-to-1 repeat](results/2026-09-25-api-recovery-d8526a684.json) with [role samples](results/2026-09-25-api-recovery-resources-d8526a684.jsonl) had no failures. At concurrency 128 it delivered 127 requests/s with p95 3.55 seconds, while the API averaged 0.40 CPU cores and the load client used 6.57 CPU seconds in 7.88 wall seconds. Back at concurrency 1 it delivered 190 requests/s with p95 6.5 ms. The load client approached one busy core during the high-load level; the server-side role counters fell. This supports a load-generator or shared-harness limit as a live hypothesis, not a proved diagnosis. A separate driver or distributed load source is needed before treating 128-concurrency results as Bifrost capacity.

## Next measurement gate

Repeat both sweeps from a clean committed revision and retain JSON outputs. Observe actual production route and workflow-shape distributions through bounded telemetry, then make anonymized fixtures. Attribute worker, API, scheduler, database, and external wait before tuning Python or building a semantically equivalent Go spike. Do not treat a small synchronous workflow's queue ceiling as proof of a Python language limit.
