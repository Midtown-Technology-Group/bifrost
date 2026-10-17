# R1 cancellation dependency: isolated SQL packet

Package `R1-CANCEL-SQL/v1`, updated 2026-10-17. **Execution held.** This is
the next persistence dependency, not completion of R1, workflow acceptance or
permission to activate a runtime. The [execution plan](rust-core-mvp-execution-plan.md)
retains actual Rust Start/result ownership as R1's exit.

The independently source-reviewed [operator procedure](evidence/r1-cancel-sql-proposal/operator-runbook.md)
now supplies the exact private-input, source-overlay, build, authentication,
test and cleanup commands. Its SHA256 is
`9322bea3681afaecd19cbdc8df0bdbac36cab422ef4ef4ea23e2f50b3439cd86`.
The operator procedure is complete. Its fixture namespace defect now has
native PostgreSQL red/green evidence: both original cases fail on 361c92c7d
and both pass on the explicit-public repair at 560e3decd. All 154 selected tests
also pass with zero skips. Final literal `./test.sh pre-pr` passed on the same
clean source.
The packet is ready for the explicit isolated authority decision; normal CI
remains pending and no gate is waived. The operator commands have not been
executed. Source acceptance is not installed-authority or runtime acceptance, and publication grants no permission to apply it.

## Exact candidate and scope

Repository `Midtown-Technology-Group/bifrost`, branch `rust/r1-sql-candidate`,
clean published commit `560e3decd7a18200809edb9bf9a078ede5d06ea5`, tree
`eb76ed5304f3438d88981d3eb5f9d23d8e85f020`, based on main
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
| `api/tests/helpers/common_cancel_fixture.py` | `32073afff69076285e981c11c87c335dd8ea5bc215872f8493f97276743235dd` |
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
checkout `/home/dev/bifrost-rust-r1-f75da956b` (now fast-forwarded to 560e3decd),
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
and Rust Core38993725274 for a6b completed cancelled, superseded by the
fixture repair. No tests began and no green evidence is transferred. The CI
aggregate's `always()` condition survived ordinary cancellation; one documented
force-cancel completed the obsolete CI run. No workflow/threshold was changed.

Final source review found one schema-less ORM INSERT under the core's trusted
`pg_catalog,pg_temp` path. Diagnostic 361c92c7d added two native-only database
cases without changing that behavior: missing-public resolution raised
UndefinedTable and a temporary shadow left the real table empty. Both failed
as predicted. The independently source-accepted repair 560e3decd uses a bound
`public.workflow_execution_attempts` INSERT, preserves claim fields and
transaction order, and relies on native policy/creation defaults. It does not
add public to the search path or grant authority. Both identical cases pass
(2 passed, zero skipped; 3.08s), with call times below 0.3s; setup dominates.
Original red log/JUnit and corrected green evidence are preserved privately.
The first fixed-source run stopped at collection because root's checkout
umask made the replaced file 0600; after inspecting the mode, that public
source file was restored to 0644. This is a diagnosed checkout repair, not a
behavioral failure waiver. The final selected suite passes all 154 tests with
zero skips (10.981s).
Final literal pre-PR passed (exit 0) after a diagnosed GitHub action-pin API
rate limit;
its original failure is retained. A fresh failed-endpoint probe returned 200
and the API reported available capacity before one unchanged-source recovery.
Its full log is SHA256
`b57f75866335a6d3fb16df3aaf7fc4058693a89b04ca27bbf41c3eecb050a6f3`.
API quality and generated-contract checks passed. The trusted comprehensive
fallback defers full API unit/E2E, client unit/E2E and MCP conformance to
required CI; those suites are not claimed passed. Normal CI38996089958 and
Rust Core38996091192 are queued at exact560, attempt1, with no duplicate run.
No common DDL/role/login is covered by these native-only tests.

Final Docker readback found the earlier native/selected-test image e68c331e
absent after pre-PR rebuilt the shared test tag. The held procedure now pins
actual final pre-PR image `61a673bed47dc94554d9b31121628be2e164bd01599bfbdb0281d3a4b4a619cc`,
exported in the successful log and retained as
`bifrost-r1-sql-python-560e3decd:qualified`. That image passed API quality and
generated-schema checks with the exact source mounted. Native2/selected154
remain evidence of their actual e68 image runs; no new61a native run is
invented. Dependency/build inputs did not change. The final image-only binding
received independent source acceptance. Rust tool image0dc remains retained.
The ordinary native project was removed with verified zero task containers,
networks and volumes; no common component was ever started. Fresh other-
activity checks were empty, then VM101 was gracefully shut down and read back
stopped. No unrelated stopped containers/volumes were removed.

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
   migration input to the reviewed 560e3decd fixture repair yields exact overlay
   tree `1613bbc35ddbc2a3074fbdb2e63bf38dc5969561`. This tree was
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
   Capture again after NOLOGIN role preparation and before C5 so the final
   installed comparison has a real four-role ambient baseline. The original
   roles-absent native capture alone cannot establish their effective ACLs.
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
run `DISCARD ALL` by default; neither is a cleanup or isolation proof. Custodians
stay NOLOGIN. The procedure creates task-only caches and a new component named
`bifrost-r1-cancel-a6b5cb7ae`; it does not reuse the native source-validation
stack. It requires effective root before creating paths, successful Docker
metadata queries and exact resource receipts, then checks ownership again
before use/removal. Rust receives no native-owner URL, settings key or private
directory. Dependency fetch/link has no credentials; credential-bearing tests
run offline on the internal component network. Actual application of these
steps remains held pending approval and then installed-catalog reconciliation.

The previous native catalog contains `plpgsql` 1.0 and `vector` 0.8.7, with
zero SECURITY DEFINER functions among 3,420 recorded functions. This narrows
the expected baseline for comparison; it does not qualify new common definers
or privileges. The finite application profile denies CREATE/TEMP on
`bifrost_test`. Stock PUBLIC builtin rights and maintenance-database access
must be explicitly listed and assessed as isolated trusted-test concessions;
they are not a production principal profile or an assumption of tenant safety.
Unexpected authority stops before LOGIN, without automatic revokes or grants.

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

The procedure, private-input generation, source-overlay recipe, capture,
component/pool setup, finite login admission and cleanup now have independent
source acceptance. Eleven shell blocks and three embedded Python snippets
parse; that is static evidence, not an execution pass. A native-only PostgreSQL
16.15 capture smoke completed with exit 0 and 7,687 JSON records at
native head `20261012_work_delivery_started`, with all four proposed roles and
common relations absent. It does not qualify installed common authority.
Current source CI remains unqualified and must still receive its normal disposition;
this packet waives no CI or acceptance gate. Isolated schema/role/login apply
requires explicit approval, then the documented installed review before LOGIN.
CRED's
separate least-authority adapter, real Rust Start/result, Python workflow,
agent lifecycle, event/public parity and reversible routing remain outside
this dependency test and unproved.
