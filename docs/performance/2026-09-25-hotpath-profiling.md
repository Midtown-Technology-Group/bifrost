# Issue 890: CPU and memory hot-path reconnaissance

This pass used isolated VM101 (`bifrost-platform-test-debian13-01`, 8 vCPU, 16 GiB) at source `a681a848b7e44030b85365ea2dc55a0ec33fed0a`. The test project was `bifrost-test-0ec4fb6d`; it was torn down after the captures. Another test project on the VM was left alone. These are lab observations, not production route-frequency or B3 capacity measurements.

## Results

| Workload | Correct | Throughput | Latency | CPU and memory observation |
| --- | ---: | ---: | --- | --- |
| Authenticated `/api/profile`, c16, no profiler | 10,000/10,000 | 271.94 requests/s | p95 79.5 ms | Control for profiler overhead; [report](results/2026-09-25-hotpath-api-control.json). |
| Mocked outbound HTTP workflow, c64 | 1,000/1,000 | 17.60 executions/s | queue p95 3.25 s; running-to-terminal p95 0.18 s | Worker averaged 1.62 cores and peaked at 234.6 MiB; active API averaged 0.42 cores and peaked at 351.2 MiB. [Report](results/2026-09-25-hotpath-worker-1000.json), [per-role samples](results/2026-09-25-hotpath-worker-resources.jsonl). |

Four worker slots were busy near the end of the c64 run, but sampled worker CPU did not exhaust four cores. The 30 sampled attempts spent much longer waiting to be claimed than running. This workload does not select a CPU-bound Python function for a Go port.

The active API cgroup grew from 293.4 to 351.2 MiB across the worker run; the worker grew from 153.5 to 179.6 MiB and peaked at 234.6 MiB. These are first-to-last and peak **cgroup** samples, not retained Python allocations or a leak diagnosis. Startup, warmed caches, forks, and object retention are not separated by this sampler.

## Profile validity

`py-spy 0.4.2` was installed in a scratch venv on VM101, outside the repo. A 10 Hz GIL-focused attach to the active one-process API returned 882 samples with no read errors, but repeatedly fell behind and consumed substantial profiler CPU. The concurrent c16 load completed at 172.29 requests/s. Its [folded stacks](results/2026-09-25-api-profile-10hz-perturbed.raw) expose FastAPI route matching, SQLAlchemy/asyncpg query work, and JWT decoding; their percentages are **not** trustworthy cost shares. Lower-rate attempts were either too sparse or still lagged. The `/api/profile` handler also fetches the full `User` row, including avatar bytes, to compute `has_avatar`; this is a specific allocation hypothesis for avatar-bearing users, not a measured improvement or evidence that this route is hot in production.

A 1 Hz GIL-focused attach to the worker and subprocesses during the c64 run produced only 45 samples across diverse execution, HTTP, SQL, and framework functions. Its [folded stacks](results/2026-09-25-worker-profile-1hz.raw) do not rank a dominant function. The earlier all-thread capture was dominated by idle worker threads and the Python resource tracker. Neither capture supports claiming that Python interpreter CPU is the limiting resource.

## Next measurement

Use passive production route/workflow-shape counts and per-role CPU and memory to choose a representative high-cost cohort. In the isolated lab, measure allocations for that cohort and bracket one source change with the same workload and resource sampler. For this worker fixture, attribute publish-to-claim and claim-to-start time before optimizing a CPU path. Keep a Go spike conditional on a concentrated, measured Python CPU cost and an end-to-end benefit after equivalent behavior is restored.

Commands used on VM101: `BIFROST_SKIP_BUILD=1 bash scripts/issue-890-run.sh --scenario api-read --operations 10000 --concurrency 16` and `BIFROST_SKIP_BUILD=1 bash scripts/issue-890-run.sh --scenario external-http --operations 1000 --concurrency 64`. The profiler attached only after the test runner began, using `py-spy record --pid <api-or-worker-pid> --gil --format raw` with the rates above. Its overhead is why the unprofiled control is reported separately.
