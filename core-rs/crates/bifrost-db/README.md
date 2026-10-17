# Internal workflow Start candidate

Package `R1-START/source/v1`, crate0.1.0. This is the next internal persistence
operation after cancellation. It is not runtime admission or completed lifecycle
ownership. The separately held `R1-CANCEL-SQL/v1` source/runbook remain unchanged.

`workflow_start` is excluded from default builds. `common-start-candidate`
exposes the source-qualification seam; `common-start-sql` also compiles its
opt-in database acceptance test. Neither feature authorizes SQL activation,
production use, execution or adding a callsite.

The trusted coordinator must supply an already validated Prepare/Prepared
exchange, authenticated live channel custody, accepted source/artifact/schema
eligibility and deadline policy. These are outstanding integration gates, not
facts inferred from retained hash equality. `PreparedStartIntent` is intentionally
not deserializable or printable. It is freely constructible only in this inactive
candidate; a production integration must provide the reviewed validation seam
before enabling calls. No public API or wire profile is defined here.

The transaction uses the existing core-principal checks and source/execution
advisory fences, then native execution, typed attempt, owner, accepted artifact,
session, detachable live link and ordered Start/admission/grant/receipt locks.
Every row/advisory contention fails closed. The held schema guards require native
Running/execution and an exact attempt timestamp before Start insertion. Both
native updates and immutable Start use one sampled PostgreSQL clock and one
transaction. No SDK material or runtime Start message is issued.

Commit errors return `UnknownCommit`. Preserve the original IDs and binding and
observe them separately. `NotRetained` is not replay, replacement, owner-transfer
or process-spawn authority. Observation rolls back its locking transaction. It
checks exact IDs, input/context hashes, deadline and native clocks; contradictory
Pending/claimed projections reject while terminal/Cancelling states can retain
historical Start. Missing live parents fail closed; a historical-only recovery
reader requires separate review.

Source qualification in the repository-supported VM/container lane:

```sh
cargo fmt --check
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo test --locked --all
cargo test --locked -p bifrost-db --features common-start-candidate --lib
```

The extra feature tests cover malformed/duplicate JSON and retained-state
classification. They do not prove SQL, role privileges, concurrency or runtime
ownership. All-feature Clippy compiles the opt-in SQL test without running it.

The SQL acceptance source consumes two fresh externally prepared synthetic
cohorts through private fixture version `bifrost.common-start-sql-fixtures/v1`:
one finite deadline and one native no-timeout cohort. It creates no schema,
roles, fixture or process. The existing cancellation fixture producer creates
Running work and **cannot** supply these prepared cohorts; a reviewed producer
and explicit experiment authority remain prerequisites. Direct PostgreSQL and
supported PgBouncer must use distinct cohorts, never reset/reopen them.

Only after those gates, the intended command is:

```sh
cargo test --locked -p bifrost-db --features common-start-sql \
  --test workflow_start_sql -- --nocapture
```

Required private inputs are `BIFROST_COMMON_START_TEST_FIXTURES`,
`BIFROST_COMMON_CORE_TEST_DATABASE_URL` and
`BIFROST_COMMON_INCUMBENT_TEST_DATABASE_URL`. Keep values out of logs.
Cases include actual wrong-actor/fence denial, forced advisory contention, two
contenders with one commit, finite/null deadlines, exact native clock/incumbent
readback, definite pre-commit PK failure rolling back both native updates,
replay/payload-drift denial, and Rust cancellation retaining Start. The two
contenders may serialize; the explicit held-fence case supplies forced overlap.
Lost commit acknowledgments and post-spawn ambiguity require separate fault
injection and actual runtime evidence.

Stop/report if integration needs new SQL privileges, weaker live links/fences,
an unreviewed wire/profile/policy decision, automatic retry after an uncertain
commit, sensitive payload logging, modified Python consumers, or an expansion of
the frozen cancellation experiment. No migration/issuer, result projection,
agent lifecycle, routing, resource retirement or runtime adapter is owned here.
