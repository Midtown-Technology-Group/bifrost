# Rust workflow Start persistence: source qualification

Package `R1-START/source/v1`, crate0.1.0, 2026-10-17. This advances Rust
transaction implementation; **no actual Start SQL or runtime ownership is
qualified**. The frozen cancellation experiment is a separate dependency.

Follow-on [prepared fixture and final source qualification](rust-core-mvp-workflow-start-fixture.md)
is now [PR #1320](https://github.com/Midtown-Technology-Group/bifrost/pull/1320)
at `30d36a6d4`, including the native claim integration correction. The rest of this record retains its original 6793 source/evidence
snapshot; new Dockerfile/pre-PR/native fixture evidence is recorded separately.
No common SQL or runtime activation follows.

## Source and owned scope

Platform main remains `e42999395231ebc536711e5015422edfb75613a7`.
Workspace advanced to `41ceed881295ad5601afb003e089d331f60074a4`.
A full new [committed-blob boundary audit](rust-core-mvp-workspace-boundary-41ceed88.json)
finds 2,460 authored Python files, zero forbidden imports/parse errors and an
empty allowlist; #1001/#1112 ancestry is reverified. An initial all-Python blob
comparison against3df correctly failed: intervening commits changed ITGlue
Python and added its test, despite the final commit being docs-only. No prior
AST result was transferred to changed Python.

Implementation branch `rust/workflow-start-transaction`, clean published commit
`6793d750ee7ddd96b60dde2e6b59da412cd07c88`, based on frozen R1 candidate
`560e3decd7a18200809edb9bf9a078ede5d06ea5`.
Worktree `/home/thomas/src/bifrost-rust-workflow-start`.
Six owned paths, all under `core-rs/crates/bifrost-db/`:

- `Cargo.toml`: inactive candidate and opt-in SQL test features, no dependencies.
- `src/lib.rs`: candidate module excluded from default builds.
- `src/common_session.rs`: extract the unchanged actor/source/execution-fence
  prefix for both operations; cancellation's literal/query sequence is unchanged.
- `src/workflow_start.rs`: atomic native Running/Start and uncertain-commit reader.
- `tests/workflow_start_sql.rs`: opt-in real SQL acceptance source.
- `README.md`: interfaces, prerequisites, commands and stop/report conditions.

Implementation SHA256
`d5584c52be9815597dd57a5b4df95185fd7c572b5c139a28394f0e60260c5ae8`;
SQL-test source `8cb44ed13d3c099fcbb56c893eb356b9d56061d5effe44d2235ed0477e805cab`.
Cargo.lock is byte-identical to560. No migration, SQL profile, credential,
Python source, public route or accepted Go code changed.

## Transaction and interface decisions

Reuse the existing Go common Start design with attribution; do not copy its
insert-before-update order. Current common guards require native execution
Running and typed attempt running/execution with the exact Start timestamp
before immutable Start insertion. The new Rust transaction performs these three
writes under one sampled PostgreSQL clock and one commit.

The shared prefix checks genuine restricted core identity and existing ambient
conditions, then trusted search path/timeouts, shared source fence and exclusive
execution fence. Start locks execution, typed claim, owner, still-live accepted
artifact, session, detachable native link, Start and ordered later evidence.
Stale/missing links, actor/fence mismatch, closed session, preexisting Start or
material/report rows reject. Current input parameters must match. No source/user
reader privilege, fallback factory, broad role or new lock convention is added.

Intent uses canonical UUIDs, strict bounded ordinary JSON, retained Prepare
identity/hash and explicit absolute/null deadline. Input/context are not Debug
or Serialize. Known commit returns `CommittedWorkflowStart`; commit failure is
`UnknownCommit` with original identities retained. Observation rolls back its
locking transaction and compares exact IDs, payload hashes, deadline and native
clocks. Pending root or claimed attempt contradicts retained Start and rejects;
terminal/Cancelling history can remain Retained. Absence is never retry, new
ownership, process-spawn or delivery authority. Detached history fails closed;
a historical-only recovery reader needs separate review.

The trusted coordinator still must validate full Prepare/Prepared wire data,
source/schema eligibility, live authenticated custody and deadline policy. The
review caught that a freely constructed intent is not admission. Therefore
`common-start-candidate` is excluded from default/production builds and no
callsite is added. Enabling a feature is not permission to invoke it. A reviewed
coordinator/opaque-validation seam is required before real use.

No wire profile is frozen by this persistence interface. The existing partial
Rust control Start uses positive parent-duration seconds; this operation returns
explicit remaining milliseconds or None. Future integration must settle that
mapping and native no-timeout behavior; do not silently round or invent infinity.
There is no runtime wire send, SDK provision, physical launch, result projection,
resource retirement or agent implementation here.

## Independent review and supported checks

Independent Sol scoped SOURCE ACCEPT on exact6793. Review required feature
containment and a retained-row classifier that rejects Pending/claimed despite
matching clocks. It also corrected the test's native terminal spelling to
`succeeded`. The opt-in test distinguishes potentially serialized contenders
from a separate deterministic held-advisory-fence overlap case. Review performed
no builds/imports/tests/SQL.

Supported environment: pve-t340, VM101 `bifrost-platform-test-debian13-01`.
Guest checkout `/home/dev/bifrost-rust-start-ba8d418a3`, advanced clean to6793;
private receipts `/home/dev/rust-start-evidence-cd56c7049`, host archive
`/home/thomas/src/bifrost-rust-mvp-artifacts/rust-start-6793d750e`.
Pinned tool image
`sha256:0dc3dbe0d1868cc15a45b22cdb685ad1bb30ab80b4789ec96405bc4df5fb0891`.
Only source/tool execution occurred. No PostgreSQL component or login was started.

Original unformattedba8 source failed fmt; pinned formatter changed only the two
new Rust files, then final clean6793 passed:

```sh
cargo fmt --check
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo test --locked --all
cargo test --locked -p bifrost-db --features common-start-candidate --lib
```

All 36 default tests pass. Candidate-feature library tests pass7, overlapping5
existing cancellation tests and adding2 new classification/JSON cases: **38
unique source-test identities, not43**. No ignored/filtered/failing tests.
All-feature Clippy compiles the opt-in SQL acceptance test but does not run it.
All 44 tracked Rust inputs match the host committed source after verification.

| Retained evidence | SHA256 |
| --- | --- |
| Original fmt failure | `6600745df2db5ba445d7f4ca49c281c3811d8a053c40e71f61f21c10f2d02bdc` |
| All-feature Clippy log | `b0195600b7658444f35a34b09ab957ad3f2045b180e085700ef196c4a935eb83` |
| Default tests | `1bd5a81696798ad8ebd93a477781a8c3b1d1a8b415c5c7af5dd11c2ccd6c064e` |
| Candidate-feature tests | `f8a1556f0e6b9a950fc1eb1448cb53dcfae388cfe9fff71cabe05bf919c8cb0b` |
| Full source manifest | `ee1b50497cf370f1158ac919eb59b55fe3074825a305aa15cff9d09ce5785416` |
| Source-check receipt | `db4d2d5ca55894b1b115d73d5799513e2df1fc3b8a5322c1ddcadd9ab651a26f` |

No new implementation PR or full pre-PR/hosted-CI acceptance is claimed for this
branch. It is a source-reviewed inactive candidate; applicable publication gates
remain before opening its PR. CRED #1037 remains a separate prerequisite at4d.

## SQL acceptance still required

The compiled-only test consumes two distinct fresh prepared cohorts, finite and
native no-timeout, through genuine core/incumbent logins on one database. Cases
cover wrong actor/fences, forced advisory contention, two Start contenders/one
commit, exact native clock/incumbent readback, duplicate/payload-drift denial,
a deliberate global Start-PK collision **after native updates** proving complete
rollback, and Rust cancellation retaining Start. No DDL/role/fixture producer or
child launch is hidden inside this test.

The existing cancellation producer precommits synthetic Running/Start and cannot
feed this test. A separately reviewed prepared-fixture producer and exact isolated
execution envelope are still required. Direct and pooled runs need distinct
cohorts. Lost-ACK fault injection, real coordinator admission, runtime execution,
result ownership and physical/source retirement remain additional gates.

## Disposition and next action

The new task baseline was143 stopped containers and2,583 volumes. Two newly
created labelled Cargo/target cache volumes were identity-checked and removed;
exact baseline equality and pinned Python/Rust images were verified afterward.
Sixteen anonymous volumes absent from the previous task's initial2,567 baseline
were already present at this task's start. Their ownership is unproved: retain
them, do not infer deletion authority from timestamps. Private metadata receipt
is retained. This is an explicit cleanup limit, not a global-prune request.
No running containers/named build processes remained; VM101 was gracefully shut
down and read back stopped. Cleanup receipt SHA256
`e95b65aa3053ffa3f872e564f294ac0e12693b2d5f43d3cfdb979d3b4244cee0`.

Frozen R1 source560 and operator runbook SHA256
`9322bea3681afaecd19cbdc8df0bdbac36cab422ef4ef4ea23e2f50b3439cd86`
are unchanged. Its SQL authority decision remains pending. This package does not
expand that approval request to Start/runtime experiments. The immediate product
path remains real installed-principal/cancellation qualification, prepared-fixture
and admission seam, actual Rust Start/result, then unchanged Python workflow and
agent execution/coexistence/rollback. No MVP CONTINUE/STOP decision yet.

Procedures: engineering flow/PR stewardship2026-10-06.1, router2026-10-01.1,
package2026-10-09.1-b3560edd5d13f1a7. Codex/Sol authored the new source with
independent Sol review; no personal DCO, human approval, merge or deployment.
