# Rust MVP Result source: build evidence

Status: build and bootstrap evidence accepted; workflow/agent MVP acceptance remains open. This record supports [RFC PR #1011](https://github.com/Midtown-Technology-Group/bifrost/pull/1011). It does not authorize merge or deployment.

## Exact candidate and environment

Platform candidate: [`95b6ebcbbd3bbecf8750ecf2817d7515d4077a68`](https://github.com/Midtown-Technology-Group/bifrost/commit/95b6ebcbbd3bbecf8750ecf2817d7515d4077a68), tree `defd67e19ca6dfb5d6eead2cf30ea711d7bf6831`, private branch `test/workflow-result-feature-source`. Candidate contains platform main `1445706946e0408198ba14c9a2a09109863aa33a`. Workspace main used for the boundary audit remains `c8ddeb588ba5960be9a34c0e63ddbf7103179f1f`; workspace code was not changed for Rust.

[Ordinary Rust Core run 37156417522](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37156417522) completed successfully on that exact candidate in GitHub's supported Linux CI. All four jobs succeeded. The existing workflow, pinned Rust build image, dependency policy, and test commands were retained. No product tests, Docker, Cargo, or rustfmt ran on the architecture host.

## Observed coverage

| Job | Actual evidence | Limit |
|---|---|---|
| [Formatting, lint and tests](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37156417522/job/111300604332) | `cargo fmt --check`; `cargo clippy --locked --all-targets --all-features -- -D warnings`; `cargo test --locked --all`: 27 passing identities, zero failed/ignored | Default tests are seven contracts, six bootstrap, fourteen domain. The selected SQL Result adapter/example tests were type-checked, not executed. |
| Same checks job, telemetry step | One native TLS exporter test executed twice, against trusted and untrusted server chains; both expectations passed | The negative run reports metrics shutdown failure. Passing expectations do not mean every exporter flush succeeded. |
| [Migrated schema and PgBouncer](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37156417522/job/111300604215) | Checks rebuilt; bootstrap `cargo test --locked --all --features live-db`: 28 passing identities including `live_tests::migrated_schema_readiness_and_drift` | Uses the existing isolated test fixture and helper DSN. It is not the Result producer's full constructor/frontend/DSN association. |
| Same integration job, production smoke | Built production binary: health/readiness, incompatible-schema readiness 503, synthetic request secrets absent from logs, SIGTERM exit zero; read-only filesystem, dropped capabilities, no-new-privileges | Bootstrap/service behavior, not workflow execution, SDK authorization, or coordinator ownership. |
| [Dependency policy](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37156417522/job/111300604330) | Locked graph security/license policy passed | No new dependencies were introduced by the source package. |
| [Cold production image](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37156417522/job/111300604370) | Cold image build and expected sanitized invalid-configuration rejection passed | No production deployment or resource benchmark. |

The integration teardown step succeeded and logs record resource removals. The smoke helper suppresses container-removal errors. Neither provides an independent final owned-resource inventory; **EMPTY is not claimed**. Raw job logs remain private and are not copied into this document.

## Corrections and independent review

[Cycle 1](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37155692214), candidate `37d2b382ce92956d807d5472a4a29333b85c80f8`, failed at one rustfmt line break. [Cycle 2](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37156011044), candidate `7a1d43f2c8e8f587100e4c7fc200e46b26a1b796`, passed formatting but failed on eight Clippy warnings. Those failures remain recorded; there was no expectation waiver or unchanged rerun.

The corrections preserve short-circuit order, witness marks before mutable logical-field borrows, SQL/lock/error order, and commit-ambiguity handling. Separate reviewers accepted the exact source corrections and the final bounded CI evidence. Final adapter SHA256: `0800726621eb1ca79e735e96f9ce38c19fb9dca6aa54ac966c62dc39cfb7fc2a`. Independent CI review SHA256: `760a7cfd29c90050737bac2363913c1a752db74d67c9760a3843c50fa0d0140f`. Root evidence disposition SHA256: `10ed1d2e713d558d7ecabedf32053bd13d6e8e52b9079a03dba6ea52fc1ff4af`. These identify retained review records; they are not extra runtime attestations.

The first cycle-3 submission returned HTTP 503; readback showed no admitted run. After reconciliation, the supported workflow admitted the successful run above. No additional formatter diagnostic was run. Previous stopped Agent Prepare, HTTP, and historical formatter attempts remain separate dispositions.

## Gates not discharged

This run does not prove the sole 299-case Python Result target, selected fifteen native Result tests, eight feature venues, full Result transition/transaction parity, unknown-COMMIT handling, real Redis-outage event behavior, constructor/frontend admission, restricted or mixed-writer ownership, actual Python Prepare/Start custody, unchanged Cove workflow/answer/summary behavior, or reversible in-flight routing. The literal `./test.sh pre-pr` gate remains pending before PR publication.

## Source changes after the tested candidate

The successful run above belongs to `95b6ebc`; it does not attest later Python harness changes.

| Candidate | Reviewed change | Evidence status |
|---|---|---|
| [`3031575fae9dedef9ec84bf9f91d0c99a7618b52`](https://github.com/Midtown-Technology-Group/bifrost/commit/3031575fae9dedef9ec84bf9f91d0c99a7618b52) | Private, single-file native DSN input; genuine fixture, production-engine and driver association; explicit descriptor cleanup and first-error preservation | Root and distinct source review accepted; AST, diff and cached Ruff static checks passed. Input materialization, container mount/identity and runtime controls remain unexecuted. |
| [`6677c9e1c3a8ea13ba65afe3df098cc9160efa39`](https://github.com/Midtown-Technology-Group/bifrost/commit/6677c9e1c3a8ea13ba65afe3df098cc9160efa39) | Source-receipt v2 consumer validates closed graph summaries against the exact admitted Cargo.lock; existing receipt root keys and test identities retained | Root and distinct source review accepted; AST, diff and cached Ruff static checks passed. Graph summaries are not compiled-unit feature outcomes; producer and differential runtime evidence remain pending. |

The source-accepted harness SHA256 is `ffd359a45618bca8112b86c94b7a8d663359e223cfbf88d5b2ef16d3109a481a`. Independent source reviews are `46578fcac42f6e46a8582581c88f84ead5d513250f1295daf5c56f71861807f0` (private input) and `81ed5f31482fa7bba14bec00ace422ebb4c8617263a60120c9a64b50ecfc24d0` (receipt consumer). These are source dispositions, not executed-control evidence.

The private-input producer, actual schema observations, compiler-unit feature/outcome records and runtime integration remain pending. Restricted writer authority is a separate unresolved architecture gate. Successful builds and source reviews do not imply C2/C3 acceptance. No full-MVP CONTINUE or STOP recommendation follows from this record alone.
