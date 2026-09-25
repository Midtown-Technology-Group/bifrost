# Issue 890 client and renderer role budget, 2026-09-25

The external-HTTP lab now starts the production-pinned client and document renderer beside its API, scheduler, and four-slot worker. `scripts/issue-890-run.sh` pins the client and renderer image digests by default; `BIFROST_LAB_CLIENT_IMAGE` and `BIFROST_LAB_RENDERER_IMAGE` allow an explicit later image comparison. The load report records both digests. The five application roles share CPU set `0-3`; PostgreSQL, PgBouncer, Redis, RabbitMQ, SeaweedFS, the load driver, and the test-only API replica remain outside this application-role budget.

On `bifrost-platform-test-debian13-01`, source `83fdde7c575134f96160eca0a4e9071e9d8eaff3` ran `BIFROST_SKIP_BUILD=1 bash scripts/issue-890-run.sh --scenario external-http --operations 200 --concurrency 4 16 64 4`. The client was healthy and the renderer was running from the pinned digests. All 800 executions completed correctly with no worker admission rejections or stale pools.

| Concurrency | Correct executions/s | End-to-end p95 | Publish-to-claim p95 |
| ---: | ---: | ---: | ---: |
| 4 | 16.68 | 0.30 s | 0.04 s |
| 16 | 19.19 | 0.89 s | 0.50 s |
| 64 | 18.57 | 3.50 s | 2.92 s |
| 4 recovery | 18.20 | 0.27 s | 0.04 s |

At concurrency 64, the sampled cgroup peak memory was 5.8 MiB for the client, 347.3 MiB for the API, 228.0 MiB for the worker, 148.1 MiB for the scheduler, and 35.8 MiB for the renderer. The maximum simultaneous sum of these five roles was 764.6 MiB. The test-only API replica reached 339.0 MiB and is excluded from that sum. During the c64 window, sampled CPU consumption averaged about 0.01, 0.35, 1.90, 0.02, and 0.00 cores respectively for the five application roles. Worker occupancy reached all four slots, while the queue p95 rose; the five-role CPU total did not fill four cores. This supports worker-slot and queue pressure in this fixture, not a Python CPU limit.

The client and renderer received only startup/health traffic, so their measured 41.6 MiB combined is an **idle** budget, not a document-rendering peak. This VM has eight CPUs, a nearly full disk, and another test stack. Its local database/queue and CPU model differ from production B3; the figures are not executions per B3-hour or a production memory-density limit. The earlier [admission sweep](2026-09-25-admission-observation.md) used the same scenario without these two roles but ran at a different time on the shared host, so cross-run throughput differences are not attributable to the added containers.

Raw [load and heartbeat report](results/2026-09-25-role-budget-4-16-64-4.json) and [one-second role samples](results/2026-09-25-role-budget-resources.jsonl) retain the source SHA, image digests, timings, counters, and memory samples.
