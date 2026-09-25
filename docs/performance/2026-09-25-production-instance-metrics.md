# Issue 890: live App Service CPU and memory window

Read-only Azure Monitor query on 2026-09-25, covering 14:54–15:54 UTC. The site `app-mtg-bifrost-production` was `Running`; its `asp-mtg-bifrost-production` plan read back as B3 with capacity **2**. [Raw one-minute series](results/2026-09-25-production-instance-metrics.json) contains `CpuTime`, `MemoryWorkingSet`, and `Requests`, each split by Azure's `Instance` dimension. Two series had nonzero values throughout the window; Azure also returned a zero-valued third series, which is excluded from the calculations below.

| Metric | Instance A | Instance B | Combined |
| --- | ---: | ---: | ---: |
| CPU time in the hour | 2,125.56 s | 1,903.09 s | 4,028.65 s, or 1.12 cores averaged over the hour |
| Mean one-minute working set | 1.59 GiB | 1.53 GiB | 3.13 GiB |
| Highest one-minute working set | 2.23 GiB | 2.05 GiB | 4.16 GiB at the same minute, 15:47 UTC |
| HTTP requests reported | 698 | 670 | 1,368 |

At 15:47 UTC the two `CpuTime` series totaled 285.14 CPU-seconds in one minute, while the `Requests` series totaled 31. That minute's mean working sets were 2.23 and 1.93 GiB. This is a **site-level** peak; the metrics cannot separate API, scheduler, worker, client, renderer, framework, or collector work, and request counts include non-user traffic. Do not divide request count into CPU time as if it were useful workflow work. The one-hour mean is observed use, not B3 capacity. These metrics also cannot identify a Python stack, retained allocation, leak, or a Go candidate.

Query: `az monitor metrics list --resource <production-site-resource-id> --metric CpuTime MemoryWorkingSet Requests --aggregation Total Average Maximum --dimension Instance --offset 1h --interval PT1M -o json`, paired with `az appservice plan show` and `az webapp show` readbacks. CPU totals sum `CpuTime.total` across 60 points and divide by 3,600 seconds for average cores. Working-set means average `MemoryWorkingSet.average`; the combined peak sums the two instance values **at the same timestamp**, rather than summing unrelated per-instance peaks. No production setting or load was changed.

The [bounded production telemetry issue](https://github.com/MTG-Thomas/bifrost-infra/issues/1614) is the next attribution step. Its draft collector must first have a reviewed deployment path that is safe for the live two-instance plan; the existing protected runtime deployer refuses multi-instance maintenance. Until role-level CPU/memory and coarse workload shape are sampled, a narrow Go comparison cannot be selected from production evidence.
