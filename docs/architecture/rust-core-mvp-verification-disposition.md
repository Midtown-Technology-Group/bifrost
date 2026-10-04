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
formatter's reported hunks in `workflow_sql_vectors.rs`; independent source
review is pending. This new lane permits at most one separately released,
genuinely corrected second candidate and no unchanged rerun.

The lane does not run Python, PostgreSQL, fault injection, the example's native
tests or the complete Result suite. Hosted runner disposal is its builder
lifecycle boundary; intermediate build layers are not independently certified
empty. It does not replace normal Rust CI or literal pre-PR verification.

## Remaining architectural acceptance

Authority decisions M1–M5 and current installed-schema writer closure remain
held. The full runtime boundary, unchanged Cove compatibility, committed events,
Redis failure, mixed writers, ambiguous commit/spawn handling, in-flight routing
rollback and fair resource/cost measurements remain unproved. Sopdet access and
compatibility are blocked independently. Preserve the W0 PRs and public
workspace boundary. No merge, production deployment, live vendor use or full
MVP acceptance is authorized by this checkpoint.
