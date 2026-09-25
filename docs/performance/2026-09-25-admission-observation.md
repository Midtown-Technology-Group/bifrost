# Issue 890 worker admission observation, 2026-09-25

The lab now samples fresh worker heartbeats from `/api/platform/workers`, including slot occupancy and cumulative admission counters. It excludes heartbeats older than 30 seconds. The previous `/stats` samples sometimes reported zero busy slots throughout a loaded run, so they could not establish whether worker admission was the limiting stage.

On `bifrost-platform-test-debian13-01`, commit `4a77e73d7e52a0d55221225896b94ed5eeb6894e` ran `bash scripts/issue-890-run.sh --scenario external-http --operations 200 --concurrency 4 16 64 4`. All four levels completed 200/200 executions correctly, with no failed attempts or stale pools. The worker advertised four slots; the one-second samples observed up to three busy. Another test stack was running on this shared eight-vCPU host, so the numbers are a functional and diagnostic check, not a clean capacity comparison.

| Concurrency | Correct executions/s | End-to-end p95 | Publish-to-claim p95 |
| ---: | ---: | ---: | ---: |
| 4 | 13.34 | 0.422 s | 0.057 s |
| 16 | 14.11 | 1.304 s | 0.778 s |
| 64 | 15.83 | 4.466 s | 3.449 s |
| 4 recovery | 16.56 | 0.303 s | 0.045 s |

Worker admission attempts and accumulated wait increased during the sweep, with no slot-timeout or memory-pressure rejections. The c64 latency increase was mainly in publish-to-claim, while throughput rose only modestly; the recovery bracket returned to low latency with no correctness loss. Heartbeat counters update less often than the one-second sampler, so per-level counter differences are approximate and should not be interpreted as exact operation counts. This result reinforces the need to attribute queue and admission time before selecting a language port.

Raw output: [load and heartbeat samples](results/2026-09-25-admission-observation-4-16-64-4.json) and [per-role resource samples](results/2026-09-25-admission-observation-resources.jsonl).
