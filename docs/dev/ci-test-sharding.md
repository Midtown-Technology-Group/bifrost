# CI test sharding

Comprehensive browser coverage runs on two GitHub runners, each with its own
Docker test stack and one Playwright worker. Keep `fullyParallel: false`, no
retries, and the setup/MCP-settings project dependencies: browser specs mutate
shared workspace and global configuration. Parallel workers on one stack need
fixture isolation before they are safe.

Playwright shards whole files across the four existing browser projects. Each
stack runs its own authentication and MCP-settings prerequisites. Focused
browser selection uses one runner; an explicitly unselected lane uses the
planner's zero-shard sentinel. The required `E2E Tests` aggregate depends on the
entire browser matrix, including failures and cancellations. Results are retained
under shard-specific artifact names on success or failure; each acceptance ledger
reports only that shard's cases, so inspect both reports for comprehensive proof.

Backend E2E retains four independent stacks. `scripts/e2e_shard.py` assigns every
collected file to the lightest estimated shard in descending runtime order.
`scripts/e2e-shard-weights.json` records complete successful JUnit testcase-time
sums by file, rounded up to seconds, with the source revision and measurement
date. Unknown files receive the measured median weight and are still included.
Timings influence allocation only, never test selection. Refresh the snapshot
from a complete successful suite when measured shard durations drift, accounting
for setup, call and teardown rather than only the slowest-test output.

The pre-change queue run `36865611676` measured browser coverage at 19m48s and
the slowest backend shard at 19m47s. Both lanes need balancing to reduce the
combined gate; stack/image preparation remains a fixed cost on each runner.
The initial backend snapshot estimates 901/900/900/900 seconds across the four
shards. Actual CI timings remain the performance authority.
