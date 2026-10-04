# Rust control-plane MVP: verification disposition

Evidence checkpoint: 2026-10-04. This records blocking verification results,
not an MVP CONTINUE/STOP decision or deployment authorization.

The architecture authority remains [platform PR #1011](https://github.com/Midtown-Technology-Group/bifrost/pull/1011),
head `93ec9421b23b0b4a1493ab45f15f55ded3377d2e`. The platform main used here is
`c45725f82f0961ce45c1dc7e4928402b44176882`; workspace main is
`848134becf9e8fdb930efc86872daa62fdeb93cc`.

## Result verification: stopped and blocking

| Candidate | Run, attempt 1 | Actual outcome |
| --- | --- | --- |
| `43229f7d65e7d3e0194baafeab701c3ad53f6ef1` | [37181334909](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37181334909) | Failed after configuration rendering; primary cause unavailable. |
| `75ec2e86bcb1ec24dcd472f19aceb8c8641a83b3` | [37182590297](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37182590297) | Closed diagnostic identified `compose-selected-source`; precise failing member unavailable. |
| `6f4c83ea4da16223522d32c8e2013be7508d9818` | [37182965541](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37182965541) | Configuration, DSN, recipe and initial resource admission passed; literal `./test.sh pre-pr` exited 1. |
| `8c42190982efdb07b267b7b94284a0dff219277a` | [37183407811](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37183407811) | Literal `./test.sh pre-pr` again exited 1; exact failing subcheck remains unavailable. |

The configuration correction selects the four current profiles only for
canonical metadata rendering. Existing runtime service selection and admission
guards remain unchanged. The next correction adds the repository-required
`v7.0.1` comments to the two existing Action pins. Official tag references were
verified; this is a definite source-policy correction, not proof of the sole
cause of either pre-PR failure.

Both pre-PR failures have 17 command records. Subsequent compilation, feature,
native, SQL and business-test stages did not start. The last run's five closed JSON artifacts are referenced by its authoritative
GitHub artifact listing.
No private configuration, raw captured output or customer payload is published.

The last safe disposal record has `primary_exit=1`, `cleanup_exit=1` and
`complete=false`. It records 17 settled processes and empty final project
container/network/volume inventories. Those observations do not waive the failed
cleanup result or establish complete resource custody.

This campaign is stopped under the stewardship rule for two consecutive rounds
with the same unresolved failure and no new actionable evidence. Do not rerun
an unchanged candidate, start a fifth run merely to obtain green, weaken the
pre-PR gate, increase deadlines, publish a PR on this evidence, or revive an
older stopped campaign. Further execution requires a concrete diagnosis and
an explicit new release consistent with the stewardship limit. Read-only
investigation and independent MVP packages may continue.

## Consumed-COMMIT source checks: separate, incomplete

The first source-check candidate `fbd30d67a7a4498dcaa8f9f617e3891aa68ebd6a`
failed [run 37183372833](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37183372833),
attempt 1, at `cargo fmt --check` in the existing pinned Docker checks target.
Clippy and default Cargo tests did not start. A repair is limited to the native
formatter's reported hunks in `workflow_sql_vectors.rs` and must receive
independent source review before execution. The initial allowance covers at most
one separately released, genuinely corrected second candidate and no unchanged
rerun.

The reviewed formatting repair was committed as
`c6adc7cf99c3709bc61533b6215ff6d92e5f2f17`. Its second source-check
[run 37183950928](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37183950928),
attempt 1, passed native formatting, then failed compilation during Clippy with
five `E0599` errors: the new test calls `CanonicalUuid.to_string()`, but that
wrapper supplies `as_str()` and does not implement `Display`. Default Cargo
tests did not start. Correcting the test callers must preserve canonical UUID
validation and the shared contract type. The initial two-run allowance is
exhausted; source repair does not authorize another run. A further supported
execution requires an explicit architect decision after review, within the
stewardship limit; no unchanged rerun or test/lint waiver is permitted.

The architect separately released one third changed candidate after independent
review of all five caller repairs. Commit
`caaba8fec7e834418a0553bf1237b536c05b4d3f` uses the existing
`CanonicalUuid.as_str()` accessor without changing the shared type or validation.
[Run 37184771575](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37184771575)
completed with failure at formatting: the longer accessor calls require two
additional line wraps. Clippy and default tests did not start; the UUID repair
is not compiler-verified. This is cycle three within the stewardship ceiling.
Any fourth run requires another explicit reviewed-candidate release; no
unchanged rerun, timeout growth or expectation waiver is permitted.
The initial two-run allowance and its failures remain recorded.

The architect then released one fourth changed candidate containing only the
two native formatter wraps, independently reviewed and committed as
`c911b46baa7260ed1492bfbe5e1295c6db9eb410`.
[Run 37185003270](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37185003270),
attempt 1, completed successfully. The actual source identity matches that
commit and tree `2989913821669cebcebd00cbac44fb4fc609d8a6`. The unchanged
pinned Docker checks target passed `cargo fmt --check`,
`cargo clippy --locked --all-targets --all-features -- -D warnings`, and
`cargo test --locked --all`. Default test groups passed 7, 6 and 14 tests,
with no failed, ignored or filtered tests; other groups contained no tests.
The four-cycle source-check campaign is closed successfully. Do not rerun this
unchanged candidate; the prior failures remain historical evidence. This does
not reopen the stopped Result campaign or prove runtime fault behavior.

The lane does not run Python, PostgreSQL, fault injection, the example's native
tests or the complete Result suite. Hosted runner disposal is its builder
lifecycle boundary; intermediate build layers are not independently certified
empty. It does not replace normal Rust CI or literal pre-PR verification.

## Consumed-COMMIT controller: first runtime run failed

The separately reviewed controller candidate
`016f2aee773bb0ac8fa52835ce3c5db1608f7c15`, tree
`51b93c752406d39d3a78167c631698611c5a0900`, was committed and pushed once.
[Run 37196588531](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37196588531),
attempt 1, job 111419660646, completed with failure. The characterization step
failed and the upload of the three closed evidence artifacts succeeded.

The producer stops at `git.origin`. Only `git.version` and `git.origin` started;
both exited 0 with complete native/EOF/group/FD settlement. All four source
identity fields remain null. No pre-PR, build, stack, frontend, SQL, actor, fault
or control execution is evidenced. Native success does not prove output admission.
The actual primary cause remains unknown; the safe receipts omit private output
and the original exception. A source-based hypothesis is not a runtime diagnosis.

Disposal records `primary_exit=1`, `cleanup_exit=1`, `complete=false`, with 2/2
processes settled. Captures are closed and removed; credentials are disposed.
Files report 9 admitted, 8 removed, 1 remaining and 1 unknown. Network and volume
counts remain null. This does not prove complete disposal or an empty environment.
Both fault lanes are entirely null and `passed=false`; Rust/Python transaction
behavior remains untested by this run.

Artifact 11301581371 supplies the three bounded, single-final-LF safe files.
The GitHub-reported ZIP digest was observed, not independently matched to a local
ZIP. Report/inspection fingerprints:
`73e4e0b9459c4b3cda7453630fa19738a9b96a3699fbc3ffc26789afb8eabdac`,
`f1c9b042ef92bb5804356514275cb28fbab4b79d4bb17bd1a19d50b597e8728f`.
The first-run release was exhausted. The separately reviewed correction and
second-run release below supersede only its prohibition on one changed second
candidate; all other execution and acceptance boundaries remain. This failure
does not revive any older stopped campaign.

## Consumed-COMMIT controller: second runtime run failed

Independent source review accepted a narrow correction for the exact checkout
origin and lazy acquisition/disposal of the pre-PR log directory. The controller
was committed as `45fa2343e5f85b9698d2bad194120062caa4ad1c`, tree
`eed1e402f4f7d3de8a7dbcc5b9f01eec356666fe`. Its SHA-256 is
`5a44a057b3b29b4b413c369187a84f8299c94456b6668ce0eeed61715ae3abd0`.
The workflow was unchanged. One separately released push produced
[run 37200188754](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37200188754),
attempt 1, job 111430111525. Characterization failed; the three-file safe evidence
upload succeeded.

The producer stops at `git.tree_roster`. All ten started Git commands exited 0
and settled their native process, EOF, group and FD observations. Source head
and tree match the candidate above; main is
`c45725f82f0961ce45c1dc7e4928402b44176882`; workspace remains null. No object
admission, pre-PR, control, build, stack, frontend, SQL, actor or fault execution
is evidenced. The actual primary cause remains unknown.

Disposal records `primary_exit=1`, `cleanup_exit=1`, `complete=false`, with
10/10 processes settled, captures closed/removed and credentials disposed. Files
report 24 admitted, 24 removed, zero remaining and zero unknown. Network and
volume counts remain null. File closure does not establish complete resource
custody. Both fault lanes remain entirely null and `passed=false`.

Artifact 11301969403 contains the three safe files; its GitHub-reported ZIP
digest was not independently matched to a local ZIP. Report/inspection
fingerprints are
`b9f4a59cd425b64202936f66db98e08a7bf811291b1558a6815abc368dcd0975` and
`def5f9af5ee0390d41a7dbf815974096fe56959eac3ea440af36eef7597f08df`.
The second-run release was consumed. The separately reviewed correction and
third-run release below supersede only its prohibition on one changed third
candidate. No unchanged retry or older stopped campaign was reopened.

## Consumed-COMMIT controller: third runtime run failed

Independent source review accepted whole-tree symlink metadata classification
while preserving regular-only admission for every selected platform/workspace
source, complete membership and physical no-symlink checks. The controller
was committed as `5be46529c122c7133ed1a85e6e344ee14105fb11`, tree
`4a1dda722cbec0c9721ffefdaa97435fd457c43a`; its SHA-256 is
`8363c47e5844ddabe5b347467c96e788ab01997cdf4359a2ea9deab4e53eb586`.
The workflow was unchanged. One separately released push produced
[run 37201902355](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37201902355),
attempt 1, job 111435151403. Characterization failed; safe evidence upload
succeeded.

The producer stops at `workspace.fetch`, which exited 128. The preceding twelve
commands exited 0; all thirteen have settled native process, EOF, group and FD
observations. Platform head/tree match the candidate, main remains
`c45725f82f0961ce45c1dc7e4928402b44176882`, and workspace remains null. The fetch
cause is unknown. No pre-PR, control, build, stack, frontend, SQL, actor or fault
execution is evidenced. This progress does not retrospectively establish the
causes of either earlier failure.

Disposal records `primary_exit=1`, `cleanup_exit=1`, `complete=false`, with
13/13 processes settled, captures closed/removed and credentials disposed. Files
report 30 admitted, 30 removed, zero remaining and zero unknown. Network and
volume counts remain null; scoped file removal does not establish complete
resource custody. Both fault lanes remain entirely null and `passed=false`.

Artifact 11303660682 supplies the three safe files; its GitHub-reported ZIP
digest was not independently matched to a local ZIP. Report/inspection
fingerprints are
`caeb7f7060858ccb2bcbee7a6baca47a5cb0984f81ec4668fbf80a174a19e47e` and
`59d3ffb0b5e20b1ca76f9282d7d0854648f4675f6fbff786b1269bb0492fd5f6`.
The third release is consumed. No fourth run, rerun, dispatch, repair/push loop,
PR, merge, deployment or authority change is authorized. Read-only diagnosis
and independent source packages may continue; all unstarted acceptance gates
remain held.

## Remaining architectural acceptance

Authority decisions M1–M5 and current installed-schema writer closure remain
held. The full runtime boundary, unchanged Cove compatibility, committed events,
Redis failure, mixed writers, ambiguous commit/spawn handling, in-flight routing
rollback and fair resource/cost measurements remain unproved. Sopdet access and
compatibility are blocked independently. Preserve the W0 PRs and public
workspace boundary. No merge, production deployment, live vendor use or full
MVP acceptance is authorized by this checkpoint.

The initial agent-deletion audit missed a later migration and must not be used
as evidence of a current ORM/migration disagreement. Although the initial and
February 17 definitions omit the conversation delete action,
`20260227_add_ondelete_to_remaining_fks.py` explicitly rebuilds
`conversations.agent_id` with `SET NULL`, matching the ORM's intended behavior.
Its catalog-selected rebuild still does not prove the installed constraint.
Read current installed FK actions and characterize genuine caller transactions
before selecting mixed-writer deletion authority. No schema correction is
justified by the incomplete audit.

## Preserved W0 pull-request checks

Read-only inspection on 2026-10-04 finds W0 parity [#1008](https://github.com/Midtown-Technology-Group/bifrost/pull/1008)
at `09539559961dd22ed3fd3f9179de84bc8f3bd772` and W0 bootstrap
[#1009](https://github.com/Midtown-Technology-Group/bifrost/pull/1009) at
`5ad5010400bf1ade5d1a96cda22684a3f4d6cf35` remain open. Both have a current
failed `CodSpeed Performance Analysis` check. Their successful functional
checks and the separate green source-check lane do not waive those failures.
Keep the performance-analysis disposition blocking; do not rerun benchmarks,
change thresholds or claim comparable measurement evidence from these checks.
Neither W0 PR was modified or merged by this checkpoint.
