# Issue 890: initial production and lab measurements

## Production observation

On 2026-09-24/25 UTC, a read-only Azure Monitor query over the preceding 24 hours returned 10,631 requests, 230 HTTP 5xx responses, a mean of hourly mean working-set samples of 1.70 GB, and a maximum working-set sample of 3.00 GB for `app-mtg-bifrost-production`. The plan is one B3 instance. Live settings contain `BIFROST_MAX_CONCURRENCY=4`, `BIFROST_MAX_WORKERS=4`, `BIFROST_DATABASE_POOL_SIZE=2`, and `BIFROST_DATABASE_MAX_OVERFLOW=3`.

This is App Service aggregate telemetry, including its API, scheduler, worker, client, and renderer containers. It provides neither endpoint mix nor per-role CPU. All three `OTEL_*_EXPORTER` settings are `none`; the code's required `OTEL_EXPORTER_OTLP_ENDPOINT` is absent. There are no App Service diagnostic settings. Consequently the current production deployment cannot yet provide the span and role attribution required for a Go decision. The 5xx count is an observation, not an attributed failure cause.

App Service application and HTTP file/blob logging are also disabled. The Talos observability cluster has an OTLP collector and Tempo, but its gRPC receiver is currently exposed as a cluster-only service; a secure, reachable production export path has not been proven. Production tracing should start only after that route, sampling, retention, and export overhead are checked.

The existing execution spans in `async_executor.py`, `worker.py`, and `engine.py` include execution and organization IDs; workflow names are also present in span or metric attributes. An export destination for this study must either restrict access and retention appropriately or remove those attributes at the collector before sharing profile data. Do not publish a raw production trace or use workflow names as metric labels in an open benchmark artifact.

The current `bifrost-infra` `origin/main` Bicep file `bicep/environments/poc/appservice-production.bicep` explicitly sets the three OTel exporters to `none` to avoid depending on the retired host candidate's cluster-local collector. The App Service is Bicep-managed. A durable production capture therefore needs an approved destination and a source-controlled infra change; editing the live app setting alone would create drift. No production telemetry configuration was changed during this study.

Source: `az monitor metrics list` for the production App Service resource with `--metric Requests Http5xx MemoryWorkingSet --interval PT1H --offset 1d --aggregation Total Average Maximum`, plus read-only `az webapp`, `az appservice plan`, and site-container configuration queries.

### Execution workload shape from production (read only)

An authenticated, paginated `GET /api/executions` query with `startDate=2026-09-24T04:00:00Z` and the default `excludeLocal=true` returned 3,203 records through 2026-09-25T04:06:45Z (four pages, no continuation remaining). The [anonymized aggregate](results/2026-09-25-production-execution-shape.json) contains no workflow names, IDs, inputs, or results. Among 3,178 records with a duration, the recorded execution duration was p50 3.08 seconds, p95 34.85 seconds, and p99 157.08 seconds. The six duration buckets from under 100 ms through over 120 s contained 315, 1,018, 433, 1,028, 347, and 37 executions respectively. There were 77 distinct workflow names before aggregation. The most frequent anonymous workflow appeared 571 times; several other common workflows had p50 durations of 10–32 seconds. The small lab workflow is not representative of this mix.

A second read-only query of successful executions at about 04:20 UTC found 3,125 creation-to-start intervals: p50 2.35 seconds, p95 275.87 seconds, p99 446.86 seconds. The start-to-completion interval was p50 19.50 seconds, p95 57.95 seconds, p99 152.04 seconds. These are **different boundaries** from `duration_ms`; creation-to-start is not proven to be queue wait for every trigger, and start-to-completion can include setup, persistence, and teardown. The queries were moving snapshots, so their counts differ slightly. These large gaps warrant tracing claim, process setup, execution, and completion before optimizing a language runtime.

The [anonymized 30-run detail sample](results/2026-09-25-production-detail-sample.json) stratifies five successful executions across each of the six duration buckets. It records only sizes, resource fields, and counts. Input size ranged from 18 to 57,621 serialized bytes in this small sample; one result was about 1.46 MB. These are observed shape hints, not population percentiles. Several records report `cpu_total_seconds` greater than `duration_ms / 1000`, in extreme cases by orders of magnitude. Code inspection explains a boundary mismatch: `worker.py` captures CPU from before workflow loading through after `execute()`, while `engine.py` starts its reported `duration_ms` inside `execute()`. `simple_worker.py` also has a fallback resource capture based on cumulative process usage. Do not use the sampled CPU/duration ratio or daily averaged CPU metric to claim Python saturation; measure CPU and wall time over the same boundary in the isolated lab and add bounded production spans. `peak_memory_bytes` is a PSS delta in the simple-worker path, whereas `process_rss_bytes` is current whole-process RSS, so those fields must not be compared as like-for-like per-execution peaks.

The App Service **plan** `asp-mtg-bifrost-production` reported hourly mean `CpuPercentage` from about 16% to 44% in a separate rolling 24-hour Azure Monitor query, with a highest within-hour sample of 96%. Hourly mean `MemoryPercentage` was about 42–53%, with a highest sample of 64%. These plan-wide measurements show transient high CPU but do not locate it in API, worker, scheduler, or another container, and cannot establish a Python interpreter bottleneck. Query: `az monitor metrics list --resource <serverFarmId> --metric CpuPercentage MemoryPercentage --interval PT1H --offset 1d --aggregation Average Maximum`.

The production execution detail API already exposes durable attempt timestamps. At 05:59 UTC, a read-only sample of every tenth record among the latest 300 non-local successful executions yielded 30/30 recorded attempts and no lookup errors. The [anonymous aggregate](results/2026-09-25-production-attempt-stages.json) reports publish-to-claim queue p50 10.86 s and p95 139.92 s; claim-to-start p50 5.16 s and p95 26.61 s; and running-to-terminal p50 16.17 s and p95 219.22 s. The latter includes workflow work and result persistence. This is a small moving snapshot, not a population estimate or a tracing substitute. It confirms that production has substantial time both before and after start, so the creation-to-start field cannot be treated as pure queue wait. These API reads changed no production settings or data. The sample contains no customer identifiers, workflow names, or payloads.

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

### Controlled outbound HTTP workflow, four shared runtime cores, clean commit `12f7cc538`

Command: `bash scripts/issue-890-run.sh --scenario external-http --operations 200 --output /tmp/bifrost/issue-890-external-http-12f7cc538.json`. The registered workflow made a real HTTP call from the worker to the test-only fixture server. The fixture delayed responses by 20, 50, or 100 ms in round-robin order. All 1,200 executions returned the expected payload with no failures. The [raw result](results/2026-09-25-external-http-12f7cc538.json) and [one-second role samples](results/2026-09-25-external-http-resources-12f7cc538.jsonl) preserve the evidence.

| Concurrency | Executions/s | p95 (s) | Worker CPU cores | API CPU cores | Worker max cgroup memory (MiB) |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 5.45 | 0.23 | 0.55 | 0.11 | 309 |
| 4 | 17.30 | 0.30 | 1.81 | 0.33 | 356 |
| 16 | 17.35 | 1.02 | 1.92 | 0.37 | 355 |
| 32 | 18.57 | 1.79 | 1.95 | 0.38 | 360 |
| 64 | 18.18 | 3.62 | 1.92 | 0.37 | 366 |
| 128 | 15.96 | 7.97 | 1.89 | 0.34 | 365 |

The worker uses about two of the four available cores while throughput plateaus, and the client uses at most 0.58 CPU seconds in the 12.53-second highest-load level. This does not demonstrate host CPU or load-client saturation. Four process slots plus per-execution setup and I/O are plausible constraints; pool occupancy and queue depth need direct capture before declaring the first saturated resource. The delays are controlled sensitivity inputs, not observed Graph, NinjaOne, or model latencies. The test VM's CPU, local dependencies, and missing renderer still differ from production B3.

A [subsequent 128-to-1 recovery run](results/2026-09-25-external-http-recovery-314cb00e0.json) completed 400/400 correct operations, with 10.68 executions/s and p95 14.44 seconds at concurrency 128, followed by 5.27 executions/s and p95 0.24 seconds back at concurrency 1. [Role samples](results/2026-09-25-external-http-recovery-resources-314cb00e0.jsonl) were retained. A separate worktree's test stack created its API container at 04:44:15Z, just before this run's high-load level began at 04:44:24Z; host load rose and its coverage-enabled API/scheduler were active. The high-load result is **confounded by a noisy neighbor** and is excluded from clean capacity comparisons. The return to serial latency is suggestive of recovery but likewise not a controlled recovery proof. The lab stack was then stopped; the other worktree's stack was left untouched.

## Next measurement gate

### Worker slot occupancy and low-rate profile

At clean commit `56e669ec7`, the outbound HTTP fixture completed 200/200 executions at each of concurrency 16 and 64. The [result with one-second worker-pool samples](results/2026-09-25-external-http-pool-56e669ec7.json) shows configured capacity 4, maximum busy count 4 at both levels, and all 13 samples during the 64-concurrency level reporting four busy slots. There were no pool-sample errors or admission rejections. Throughput was 13.70 and 14.38 executions/s, with p95 1.69 and 4.54 seconds respectively. [Role resource samples](results/2026-09-25-external-http-pool-resources-56e669ec7.jsonl) are retained. Another test stack remained active on this shared VM, so these rates are **not** clean capacity estimates. The occupancy does directly establish four-slot saturation for this fixture while the clean `12f7cc538` resource sweep showed only about two worker CPU cores in use. The immediate cap is worker admission capacity; why each slot takes this long still needs stage timing.

A 10 Hz, subprocess-aware `py-spy` capture during a separate 1,000-execution, concurrency-64 outbound HTTP run produced [folded Python stacks](results/2026-09-25-worker-lowrate.raw) (3,098 samples, 200 collection errors as short-lived children exited). The load completed 1,000/1,000 at 15.13 executions/s and p95 4.90 seconds while another test stack was present. Most sampled thread leaves were idle thread workers, the multiprocessing resource tracker, or the template accept loop; among active leaves, HTTP/TLS/DNS setup, SQLAlchemy, import/module loading, and memory accounting appeared without a single dominant Python business-logic stack. These are all-thread samples, **not** CPU-time percentages or a comparative flamegraph, and profiler overhead was not isolated. A 20-second GIL-only attempt yielded just 71 samples with 51 collection errors, too little to attribute CPU. Do not choose a Go extraction from this profile alone.

The CodSpeed branch dispatch [run 36096315509](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36096315509) passed both the retained simulation suite and new execution-serialization walltime/memory cases; performance data uploaded. This validates the benchmark wiring, not a language comparison or production capacity claim.

### Controlled agent tool loop and exposed DB-pool failure

The agent fixture drives `POST /api/agent-runs/execute` through a real two-iteration model/tool loop with 50 ms per mocked model request. At `15780d818`, 20/20 serial runs completed (p50 0.48 s, p95 0.76 s; the cold first run was 4.22 s) and 20/20 at concurrency 4 (p95 1.09 s). At concurrency 16, only 8/20 completed; p95 among successes was 35.75 s. The [failing result](results/2026-09-25-agent-failed-15780d818.json) and [role samples](results/2026-09-25-agent-failed-resources-15780d818.jsonl) are retained. API logs at 05:26 UTC showed `sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached ... timeout 30.00` while the synchronous agent endpoint waited for run results. Each request kept its read transaction and database connection open during that potentially long wait. The mock summarizer also returned invalid JSON in this revision, adding an unrelated error path.

At `6e8758d80`, the endpoint snapshots the agent identity and paused state, rolls back the read transaction, then enqueues and waits. The fixture returns valid synthetic summaries. A concurrency-16 repeat completed 20/20 in 9.79 s, with p95 8.74 s and zero pool admission rejections: [result](results/2026-09-25-agent-fixed-6e8758d80.json), [role samples](results/2026-09-25-agent-fixed-resources-6e8758d80.jsonl). A focused route test verifies that connection release precedes the blocking wait. This is a concrete Python-path fix discovered by the performance lab. Because the summary fixture also changed and another test stack was active, the two latency measurements are not an isolated speedup comparison. The failure and timeout disappearance is supported by the API exception, code path, and same-concurrency rerun.

Repeat the occupancy and profiling sweeps on an otherwise idle host, then time queue claim, slot wait, fork/setup, workflow body, persistence, and external HTTP over matching boundaries. Observe production route and workflow-shape distributions through bounded telemetry before weighting fixtures or selecting a semantically equivalent Go spike. Do not treat a small synchronous workflow's queue ceiling as proof of a Python language limit.

### Persisted attempt-stage sample, clean commit `54893ccc3`

The lab harness now reads up to 30 successful execution details **after** each measured load level and computes intervals from the existing durable attempt timestamps. The [result](results/2026-09-25-external-http-stages-54893ccc3.json) and [role samples](results/2026-09-25-external-http-stages-resources-54893ccc3.jsonl) are retained. All 300 requested executions succeeded and every post-run stage lookup succeeded. At concurrency 4, 16, and 64, the queue interval from broker-confirmed publication to durable claim had p95 of 0.045, 0.601, and 3.287 seconds. The running-to-terminal p95 remained 0.208, 0.212, and 0.194 seconds. At concurrency 64, the sampled queue interval was the dominant part of the response delay. Dispatch p95 was 0.535 seconds and claim-to-start p95 0.247 seconds at that level. These boundaries do not isolate process startup from admission or the workflow body from persistence.

Two other test stacks were active on the VM during this run, so its 14–16 executions/second is excluded from clean capacity estimates. The one-second worker-pool endpoint reported zero busy slots throughout, contrary to the earlier four-busy sample and the persisted queue growth; this live-stat discrepancy needs investigation before it is used as an occupancy authority. The persisted timestamps do show that high-concurrency delay grew mainly before the workflow reached its running phase in this specific synthetic load. They still do not identify a Go-suitable CPU hotspot.
