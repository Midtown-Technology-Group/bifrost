# Solution publication performance

The Docs 0.1.3 publication spent 320.37 seconds in deferred finalization. The
deployed implementation invalidated resolver metadata by scanning Redis for
each Python file and ancestor namespace. Its Azure adapter also opened and
closed an owned identity/client for every storage call.

An isolated diagnostic using the 45 Docs Python paths and 41,500 unrelated
Redis keys measured a median 39.03 seconds and approximately 355,000 SCAN
requests for the deployed cache writer across three rounds. Upstream's
generation protocol measured 18.31 milliseconds and zero scans in that
experiment. These are cache-only measurements, not predicted production
publication times. Synthetic Azure identity latency separately established
client construction amplification; it did not measure production Azure latency.

The implemented fork was then measured with the same fixture: three rounds
completed in 25.83, 22.52, and 22.95 milliseconds (median 22.95 ms), with zero
SCAN requests. Cross-scope invalidation, unrelated entries, source bytes, and
the delayed-write fence passed; fixture cleanup left zero keys. This later
measurement is not a simultaneous paired comparison to the baseline.

## Implementation and provenance

Selectively adapt the resolver generations and publication observation hooks
from gobifrost/bifrost commit
`aa1b72e53f20ad7467dc56f7e6fd107de57e7204` (Jack Musick, upstream PR #984,
AGPL-3.0). Agent stream retention changes from that commit are outside this
change. Preserve this fork's immutable-release and workspace source generation
checks, original-job reconciliation, and uncertain activation handling.

Metadata uses a v2 namespace. A logical name has one expiring random token shared
across scopes. Invalidation rotates exact and ancestor tokens transactionally;
atomic Lua reads and guarded writes reject old metadata and delayed lookups.
Routine invalidation never enumerates Redis. Administrative cold-cache clearing
retains a bounded COUNT hint; it is not part of source publication.

Python publication borrows one operation-owned storage client for its list,
writes, and deletes. The original storage object and sibling operations retain
independent ownership. The existing adapter closes transports and credentials
on normal exit, exceptions, and cancellation. No process-global client is added.

Existing PlatformJob progress reports publication phases. Aggregate job log
timings distinguish source storage, Redis cache work, app upload, activation,
and cleanup. They contain categories and elapsed time, never source or secrets.
Ordinary progress transport errors are non-fatal. Explicit cancellation and
lease rejection remain stop signals through the reporter and finalize retry
wrapper. A rejected activation-phase report must not retry the upload or reach
activation, even if a subsequent report would succeed. Regression coverage
exercises that composed call path, alongside ordinary telemetry errors and
transient storage retry behavior.

## Development application acceptance

Dedicated VM101 (`bifrost-platform-test-debian13-01`), worktree
`/home/dev/src/bifrost-solution-publication-perf`, debug project
`bifrost-debug-d276ee4d`, endpoint `http://172.16.15.130:31754`:

- Actual authenticated API rejected an invalid archive with HTTP 422.
- One source-only 45-file Solution install succeeded through the local scheduler
  and its original PlatformJob (`230318e0-bead-459f-ac34-0d183097c966`).
- Observed aggregate timings: source archive 27 ms, source storage 238 ms, Redis
  44 ms, total Python publication 296 ms. Request-to-readback took 13.40 seconds.
- Installed identity readback and scoped deletion both succeeded; the disposable
  Solution was removed. Credentials were not retained in this record.

This debug stack uses S3-compatible SeaweedFS, not production Azure Blob. Azure
transport ownership is separately exercised with the actual adapter and mocked
SDK transport. Neither result proves production end-to-end latency.

## Release and rollback

Deploy API, scheduler, and worker together through the protected infra runtime
lane after draining accepted executions and publications. Old and new resolver
metadata protocols must not overlap active work. There are no database or public
API contract changes. Old resolver metadata expires within its existing TTL;
this change performs no production keyspace cleanup.

Retain the previous complete image tuple and capacity before apply. A rollback
must likewise drain work and restore the complete tuple. If source was published
under v2, old v1 metadata can remain stale until its original 24-hour TTL expires.
Before reopening old readers in that window, require a separately reviewed cache
reconciliation plan or proof that the old metadata has expired. This change adds
no automatic rollback cleanup. Never flush unrelated Redis data. Verify image
digests, readiness, scheduler leadership, worker capacity, and an accepted safe
execution independently of source and CI evidence.

For publication acceptance, use a disposable local Solution through the actual
API and retain its original PlatformJob result and timing logs. Production
publication latency requires a separately observed real publication; a healthy
runtime deployment alone does not establish the speedup.
