# Issue 890: initial production and lab measurements

## Production observation

On 2026-09-24/25 UTC, a read-only Azure Monitor query over the preceding 24 hours returned 10,631 requests, 230 HTTP 5xx responses, a mean of hourly mean working-set samples of 1.70 GB, and a maximum working-set sample of 3.00 GB for `app-mtg-bifrost-production`. The plan is one B3 instance. Live settings contain `BIFROST_MAX_CONCURRENCY=4`, `BIFROST_MAX_WORKERS=4`, `BIFROST_DATABASE_POOL_SIZE=2`, and `BIFROST_DATABASE_MAX_OVERFLOW=3`.

This is App Service aggregate telemetry, including its API, scheduler, worker, client, and renderer containers. It provides neither endpoint mix nor per-role CPU. All three `OTEL_*_EXPORTER` settings are `none`; the code's required `OTEL_EXPORTER_OTLP_ENDPOINT` is absent. There are no App Service diagnostic settings. Consequently the current production deployment cannot yet provide the span and role attribution required for a Go decision. The 5xx count is an observation, not an attributed failure cause.

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

## Next measurement gate

Repeat both sweeps from a clean committed revision and retain JSON outputs. Observe actual production route and workflow-shape distributions through bounded telemetry, then make anonymized fixtures. Attribute worker, API, scheduler, database, and external wait before tuning Python or building a semantically equivalent Go spike. Do not treat a small synchronous workflow's queue ceiling as proof of a Python language limit.
