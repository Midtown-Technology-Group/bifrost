# R1 cancellation dependency: isolated SQL packet

Package `R1-CANCEL-SQL/v1`, updated 2026-10-17. **Execution held.** This is
the next persistence dependency, not completion of R1, workflow acceptance or
permission to activate a runtime. The [execution plan](rust-core-mvp-execution-plan.md)
retains actual Rust Start/result ownership as R1's exit.

## Exact candidate and scope

Repository `Midtown-Technology-Group/bifrost`, branch `rust/r1-sql-candidate`,
clean published commit `a6b5cb7aea87cea48a7b531bd5c7a2d1cbfd3e09`, tree
`c52b47495cc1b1d921a7f65082e01832b5f7cd7d`, based on main
`e42999395231ebc536711e5015422edfb75613a7`.
Local worktree: `/home/thomas/src/bifrost-rust-r1-sql-candidate`.

Composition at f75da956b reused the Rust tree from
`3c66dc70a58a8be1758a535c6b47c1147df71f6e` verbatim. Supported formatting
then exposed unformatted test assertions. The partial repair at 967ce4e72 still
failed formatting; 13744851d applies the complete pinned formatter output.
The current Rust tree is `5e6f7bbc467b51d2f8979bf8385a2d020472d7ac`.
The cancellation operation itself is unchanged. Composition retains current-main
`test.sh` and adds only the 14-line native Rust bootstrap adapter; it does not
copy old test-runner changes or revive experimental CI campaigns. The common
writer/fixture input is `9f45e57e8f95354a4a230033f6e59b3970b33b4a`.

| Input | SHA256 |
| --- | --- |
| `api/tests/helpers/common_cancel_fixture.py` | `9f366712def6b166aeffdd4680ee963e04a7e46d41c8aeaae1dcb9af15e6052c` |
| `api/tests/helpers/common_cancel_fixture_driver.py` | `5456ff71425aaef87e6bb9a0cc16546bbbf482c6a554954bbd9fd11420c52eea` |
| `api/alembic/candidates/runtime_principals_v1.json` | `712a06a9b21ee00912221b0625e881fe92131a297834db175bca4ff04ddd16e7` |
| `api/alembic/candidates/runtime_principals_prepare.sql` | `e4069f4722ef424821c7225fce93b78f05c8be6160a83bb291a61bcc213b7068` |
| `api/alembic/candidates/runtime_principals_grants.sql` | `cf4ebd9c6b9b7322391995ed09cf0b5cc3fd02296e8d131abba7a0fc421b46cd` |
| `core-rs/crates/bifrost-db/tests/common_session_sql.rs` | `8d34bf0e6a2bbf48131173f9aea0b1926038cc8519588bea62a8758c2a297f2c` |

Independent Sol source review accepted the producer at 9f45 after its P2
publication repair, and the composed SQL/bootstrap/driver at c4c32dc7/f75da956b.
No reviewer imports, tests, SQL or Cargo execution are claimed. Locked Ruff
0.15.12, formatting, shell syntax and diff checks passed. Supported source
qualification is separate and must bind its actual candidate/environment.

Supported source checks use VM101 `bifrost-platform-test-debian13-01`, guest
checkout `/home/dev/bifrost-rust-r1-f75da956b` (now fast-forwarded to a6b5cb7ae),
Compose project `bifrost-test-0290298a`. Initial source-only attempts retained
two diagnosed checkout failures (missing origin/main tracking ref, then root
umask preventing uid1000 from reading public source) and the actual Rust fmt
failure and the failed partial formatter repair. On source 13744851d,
literal `./test.sh pre-pr` passed (exit 0), as did `cargo fmt --check`, strict
all-target/all-feature Clippy and all 36 default Rust tests (exit 0). All 41
tracked Rust input files were checked before/after the container run against
the final source manifest. All-feature Clippy compiled the opt-in SQL test;
default tests did not execute it. Original failures and final raw logs remain
private under `bifrost-rust-mvp-artifacts/r1-13744851d`. These are supported
operator-run checks, not signed independent CI attestations. No common
DDL/role/login or common SQL fixture was executed. The focused Python run at
13744851d found 153 passes and one failing offline test: its blanket prohibition
on `IF NOT EXISTS` rejected C1's legitimate fail-closed role prerequisite.
Independently reviewed a6b5cb7ae narrows that assertion to conditional table
creation, preserving role checks and table/key/order/FK assertions. On a6b,
all 154 selected tests passed with zero skips. Its Rust tree is unchanged;
final-source literal pre-PR passed with exit 0. Normal CI38993723523
and Rust Core38993725274 were dispatched once for a6b and remain pending.

## Required ordering

Use only a newly identified task-owned disposable PostgreSQL16/PgBouncer
component environment on the supported VM101 lane. Existing stacks/volumes
belong to their original owners. No production DSN or API/worker/scheduler
credential switch is part of this experiment.

1. Qualify the clean source candidate. Prepare native Alembic history through
   `20261012_work_delivery_started` with no common stores and no proposed roles.
   Preseed **two distinct** native histories, one for direct and one for pooled
   qualification, through the existing deployment repository. Complete this
   before installing common source/write guards: migration-owner source
   transitions after guard installation are intentionally denied.
2. After explicit isolated authority/application approval, prepare the four
   NOLOGIN identities using the reviewed SQL. Its two explicit ambient changes
   are PUBLIC CONNECT/TEMPORARY on **bifrost_test** and CREATE on **public**.
   These are approval items, not automatic repairs on another database.
3. Admit the exact reviewed C1-C5 files into this isolated Alembic continuation
   by the approved activation procedure. Use the existing environment,
   migration advisory lock and one revision chain through
   `20261016_runtime_source_drain`. No stamping, manual `Operations` replay,
   historical rewrite or second migration ledger. The independently source-
   accepted [activation recipe](rust-core-mvp-r1-activation-overlay.json) binds
   all eight source/target blobs and revision links. Rebinding that identical
   migration input to the reviewed a6b test-only repair yields exact overlay
   tree `3ca13fba4fa0cb35f3c9cad06f2a41aacff62256`. This tree was
   computed with a temporary Git index only; no active migration file was
   written and no overlay was applied.
4. Apply only the finite grants SQL. Keep all four roles NOLOGIN. Capture and
   independently reconcile the installed schema, functions/owners/search paths,
   triggers/FK actions, roles/memberships, effective table/column/sequence and
   function privileges, PUBLIC/default/extension authority and RLS against the
   profile. Matching revision or object names alone does not qualify it.
   Unexpected authority or a required unlisted privilege stops the experiment;
   no broad grants, arbitrary revocations or catalog repair to obtain a pass.
   The source-accepted [read-only capture](evidence/r1-cancel-sql-proposal/installed-catalog.sql)
   is SHA256 `7819c8b83809c1959ff0b4e9633a80a662ba73c9a81636aa5194fa8be93ca32a`.
   Invoke `psql --no-psqlrc --quiet` with that input and a private 0600 output
   file. Its READ ONLY/REPEATABLE READ transaction includes database/role
   settings, index validity/readiness and parameter ACLs (including effective
   SET/ALTER SYSTEM rights), not only DDL text. Successful
   exit, complete output, target/image provenance and independent comparison
   are required; this is a capture, not an automatic authority validator.
5. Only after that gate and its approved activation procedure, enable the two
   restricted test logins. Prove genuine `session_user=current_user` for each
   through direct PostgreSQL and supported transaction pooling. Custodians stay
   NOLOGIN, without membership or native ownership. The test SQL principals
   have explicit cross-owner read and lock capabilities; approval must cover
   those concessions. This is not a runtime-child or credential-issuer role.
6. Use each native seed once to create its synthetic common cohort. Native
   owner performs reads only; authenticated core commits all common and
   Rust-owned native writes. Run the actual Rust SQL test, direct then pooled,
   against distinct cohorts on the same database. Never reset/reopen a cohort.

The fixture entrypoints are container commands from `api/`:

```sh
python -m tests.helpers.common_cancel_fixture_driver native \
  --native-principal bifrost --destination /private/direct-native.json
python -m tests.helpers.common_cancel_fixture_driver common \
  --native-principal bifrost --native-seed /private/direct-native.json \
  --destination /private/direct-common.json
```

Repeat with distinct `pooled-*` output names, in their appropriate stages.
Supply `BIFROST_COMMON_NATIVE_TEST_DATABASE_URL` explicitly; common stage also
requires `BIFROST_COMMON_CORE_TEST_DATABASE_URL`. No default/global factory is
used. Private directories are caller-owned 0700; outputs are 0600. Keep URLs,
claim material, SQL parameter errors and raw private logs out of reports.

The consuming command, inside the pinned Rust tool container, is:

```sh
cargo test --locked --manifest-path core-rs/Cargo.toml -p bifrost-db \
  --features common-session-sql --test common_session_sql \
  no_grant_cancel_preserves_fences_and_commits -- --exact
```

It additionally requires `BIFROST_COMMON_INCUMBENT_TEST_DATABASE_URL` and
`BIFROST_COMMON_CANCEL_TEST_FIXTURE`. Bind all three inputs to either the
direct pair/fixture or pooled pair/fixture, never a mixture. Do not invoke
ordinary `./test.sh` auto-reset commands against these admitted cohorts.

## Evidence and failure disposition

Component and private login source proposals now accompany the packet:
[two-service Compose](evidence/r1-cancel-sql-proposal/compose.yml),
[PgBouncer config](evidence/r1-cancel-sql-proposal/pgbouncer.ini) and
[test-login SQL](evidence/r1-cancel-sql-proposal/activate-test-logins.sql).
They are independently source-accepted and remain unapplied. The component has no
product services, host ports or external network; its task-owned data volume
retains uncertain results until reconciliation. The pinned pool image uses an
explicit mapping with no forced backend username. Plain pool authentication
is limited to this internal disposable test network, not a production policy.
The intended two private synthetic logins require distinct generated passwords
and finite expiry of at most two hours. Built-in `psql \password` must set
SCRAM secrets while roles remain NOLOGIN; plaintext is never interpolated into
SQL. The activation file accepts only expiry and checks stored SCRAM format.
Expiry does not terminate existing sessions. Pool transaction mode does not
run `DISCARD ALL` by default; neither is a cleanup or isolation proof. Custodians stay NOLOGIN. These files do not supply
approval, generate inputs or qualify effective authority; exact setup, private
input handling, installed-catalog comparison and cleanup remain prerequisites.

The producer preserves an immutable sibling `.pending` intent synced before
SQL. Final publication stages and syncs bytes, then links without replacement.
File existence alone is never a receipt: a post-link sync failure may leave
final bytes despite producer failure. Require successful producer completion
and independent committed/catalog readback; retain pending and final bytes
privately after any uncertain outcome. No automatic database write retry.

Required Rust observations are paired incumbent Python-owned UPDATE success
and Rust-owned UPDATE denial, actual wrong-principal rejection, wrong binding,
held advisory contention without mutation, committed cancellation/session close,
fresh reads through both identities and idempotent internal observation/replay.
Zero grants/admissions/reports/resources remain zero. Preserve raw-enough private
evidence, sanitized outcomes, exact source/image/config identities and teardown
ownership for both transport runs.

The synthetic Start/artifact/binding hashes establish **no** runtime execution,
physical custody, credential or actual Rust Start fact. Final native status is
Cancelling with an incomplete attempt; source stays pinned. Missing resources
or rows do not authorize retirement, replay, owner transfer or fabricated stop
proof. Dispose of this wholly synthetic database only after failure/uncertainty
is reconciled and evidence is retained; database teardown is not a product
resource-settlement event.

Execution is not yet ready: installed-catalog comparison, private test-login
activation and bounded component setup/cleanup commands still need concrete
review. The activation recipe and capture source are reviewed; a native-only
PostgreSQL 16.15 syntax smoke completed with exit 0 and 7,687 JSON records at
native head `20261012_work_delivery_started`, with all four proposed roles and
common relations absent. It does not qualify installed common authority.
No permission request or acceptance is implied by this packet draft. CRED's
separate least-authority adapter, real Rust Start/result, Python workflow,
agent lifecycle, event/public parity and reversible routing remain outside
this dependency test and unproved.
