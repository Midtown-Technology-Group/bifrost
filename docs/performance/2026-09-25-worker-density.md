# Issue 890: four versus eight worker slots

The follow-up lab kept the API, scheduler, and worker on host CPUs 0–3 and varied only `BIFROST_MAX_WORKERS` and `BIFROST_MAX_CONCURRENCY` through `BIFROST_LAB_SLOTS`. The test VM still had two other active test stacks, so this alternating 4 → 8 → 4 sequence is a topology sensitivity check, not a clean B3 capacity estimate. Each sweep reset the issue worktree's isolated test state, used the fixed 20/50/100 ms outbound HTTP fixture, and completed 200/200 correct executions at each of concurrency 4, 16, and 64 with no failures. Source was `d253b5bef`.

| Slots | Sequence | c64 executions/s | c64 response p95 | Queue p95 | Running-to-terminal p95 | Worker max memory | Worker CPU cores | Max sampled busy slots |
| ---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | A | 17.76 | 3.76 s | 3.11 s | 0.19 s | 361 MiB | 1.98 | 3 |
| 8 | B | 19.95 | 3.66 s | 2.47 s | 0.26 s | 389 MiB | 2.24 | 5 |
| 4 | C | 15.33 | 4.14 s | 3.55 s | 0.23 s | 374 MiB | 1.90 | 4 |

The eight-slot setting used about 15–28 MiB more worker cgroup memory than the bracketing four-slot runs and delivered about 12–30% more c64 throughput in this noisy sequence. Only five of eight slots appeared busy in the one-second samples. Queue p95 was lower with eight slots, while running-to-terminal p95 and worker CPU rose. This is a modest topology effect with appreciable host drift, not a 2× end-to-end or proven memory-density advantage. Keep production's four-slot setting until a quiet-host repeat includes the client and renderer memory budget and a representative execution mix.

Raw [4-slot A](results/2026-09-25-density-4a-d253.json), [8-slot B](results/2026-09-25-density-8-d253.json), and [4-slot C](results/2026-09-25-density-4b-d253.json) reports have corresponding `-resources-` JSONL files. CPU cores are cgroup CPU-use deltas divided by elapsed sample time within the c64 level; memory is the maximum worker cgroup current value in that window. Stage times come from post-run samples of 30 persisted successful attempts per level.
