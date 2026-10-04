# Rust control-plane MVP: verification disposition

Evidence checkpoint: 2026-10-04. This records blocking verification results,
not an MVP CONTINUE/STOP decision or deployment authorization.

The [parity matrix](rust-core-mvp-parity-matrix.md) separates implemented
source, supported checks and still-unproven lifecycle acceptance.

The architecture authority remains [platform PR #1011](https://github.com/Midtown-Technology-Group/bifrost/pull/1011),
head `93ec9421b23b0b4a1493ab45f15f55ded3377d2e`. The platform main used for the recorded execution campaigns is
`c45725f82f0961ce45c1dc7e4928402b44176882`; workspace main is
`848134becf9e8fdb930efc86872daa62fdeb93cc`. A later read-only fetch on
2026-10-04 observed platform main `7ff37809f249ff21a9ca150780fbc8f1c6d07b0c`
and the same workspace head. Both prerequisite merges (#1001 and #1112)
remain ancestors. The platform advance is the four-file immutable empty-source
export repair (#1048); it does not change the collector schema, SQL or source
pins. A subsequent fetch observed platform main
`7e737cacdba56b214e427afed5a28e89ae1b6a47`, which adds the explicit API-target
requirement for SDK Solution start and associated tests/reference material.
The latest read-only fetch confirms that platform head and the same workspace
head, with both prerequisite merges still present. Neither advance retroactively
changes any campaign candidate or evidence.

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

The missing existing-published-attempt claim planner is now implemented in
`bifrost-domain`, with four new test functions and all fourteen prior domain
tests preserved. Candidate `0df61473a8a3774da81b5a8e4c9284735b006a07`
includes current main `7e737cacdba56b214e427afed5a28e89ae1b6a47`.
Root and independent source review accepted its exact guard order and eight
write directives; independent source receipt SHA-256 is
`def6c37e256c2d2c477482b1dd34ac67b563065793ac71165a378a734aaca6b6`.
Missing execution defers the legacy path, non-pending execution skips before
attempt validation, and no active attempt defers allocation. A published,
unfenced active attempt produces a pure running/claimed row plan. The planner
generates no token, reads no clock, writes no database and grants no Start.
[Supported source checks](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37218867181)
passed on attempt 1: formatting, locked all-target/all-feature Clippy with
warnings denied, and locked default workspace tests. All 31 tests passed
(7 contracts, 6 bootstrap, 18 domain), including the four new claim tests;
none failed or were ignored. The actual source metadata joins the candidate
and tree `17e9c13618285017fe210d276a0e17705645b024`. Evidence receipt
SHA-256 is `8f3d67e54e8ec2e5593c7d8eae51c0c279c78a1a3eea995c5ed270b5df8d4521`.
Independent executed-evidence review accepted that bounded proof, receipt
SHA-256 `c515f704c3e10ff29dc87db4890ad7956841257f74f89f4b5fb43bf42d568014`.
This is source/unit proof, not live PostgreSQL testing or Python/Rust behavioral parity.
Actual original-Python characterization and deliberate semantic drift detection
remain required before any business wiring. This candidate is not a pull
request or proof of literal pre-PR, normal CI or lifecycle ownership.

Authority decisions M1–M5 and current installed-schema writer closure remain
held. A later fresh-main checkpoint at
`7e737cacdba56b214e427afed5a28e89ae1b6a47` carries all11 selected M1
API/caller source pins and requirements.lock, and all25 selected Python M3
writer/locking pins, byte-for-byte. Root independently checked all hashes.
The separate source-carry reports are
`ef24f38631b514e5872137757e2e0114fc05b3b018775fbaf0b360cd7c2e88d7`
and `d9ff79ce6233e08c83eb5a2ee9e02c4266aa7c2b8a20f258c99e23f6e40906d2`.
The fixed rosters do not prove unlisted writer, installed catalog or privilege
closure. M3's cross-owner read and ANY table-lock concession, including
ACCESS EXCLUSIVE, remains unapproved; it cannot be advertised as SHARE-only.
Current main contains no Rust source; all Rust proof remains candidate-bound. The full runtime boundary, unchanged Cove compatibility, committed events,
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


## Agent deletion catalog: corrected source accepted, launch held

The frozen collector source has SHA-256
`a8c4aa1ceaf2a86e10b1d3a5c188c832e027d90a136fd9d4825d9ccf975cb1d9`
on candidate baseline `4234d30e62e8db4d077751541907a5cec4888041`. Root
and a separate reviewer accepted only the bounded source corrections: exact
pytest class-plugin registration, malformed-versus-oversized foreign-key
precedence, and controls that reach actual multirow/association guards.
Independent review receipt SHA-256 is
`8b933b9f6d5b6b58825196f58eebddb0d160e74e10d077f057f8fbf05eb80dfb`;
root source-preservation receipt is
`c43b85aa91e62fa6491793180ccb3b226b68c2df672b6adadeb9b3d0c3b5e1af`.

Static AST, lint and formatting checks passed. The 13 SQL statements, 17 source
edge objects, main resource lifecycle and ten control families are preserved.
Controls, plugin qualification and SQL remain unexecuted. Source acceptance
does not prove installed behavior, semantic closure or M4/C2/C3 acceptance.
The collector remains deliberately nonaccepting when semantics are unqualified.

A supported launch still requires current-main candidate reconciliation,
repository checks, actual image/configuration/source qualification, private
raw-output custody, safe property-only evidence delivery and physical cleanup.
The existing wrapper tees output and writes JUnit under a directory it makes
world-writable; that directory cannot be asserted to provide private custody.
A finite parent launch design has received independent design acceptance:
121 maximum parent operations, one 2400-second parent boundary, fixed observation
slots, actual container/image/configuration admission, bounded private captures
and an explicitly exposed original wrapper log directory. A requested runner
name is not an observed container identity. The two parent CI files are now
frozen for source review: conductor SHA-256
`49cc1465172ecf57d0c6a757b24e3a09adf363dd0cb16776b31a1325092dcbf5`
and workflow SHA-256
`b85609643b7786932c6bb2e9836736dc7a9ba578a6b55ea8b25809483e88d4a2`.
Root and independent full source review hold this candidate on three findings:
validated image aliases are dropped before tag/acquisition checks; whole Config
equality can reject SeaweedFS replacement because of a generated hostname; and
no actual mount-exclusion witness gates recursive privileged log ownership
restoration. Same-filesystem bind mounts are not excluded by device equality.
Independent review receipt SHA-256 is
`6bee65cb70670af2dfaca55043df77aed215b1b051b7ff5acd32140eb40d7084`.

Capture and publication writes now require positive bounded progress under the
original deadlines; action version comments preserve the existing pins. Those
source corrections passed root preservation checks. No parent execution or
installed catalog evidence follows. The independently reviewed bounded design
for image aliases and generated hostnames has now produced a frozen correction,
conductor SHA-256
`6952067a839ad8b546f43ee068c809f3be04397e99d805b9846f87a7d4f5a7b7`.
It retains all validated aliases and recipe associations, compares each
generated hostname against its own actual container ID, and requires retained
child settlement before qualified helper disposal. These are source changes
accepted by root and distinct source review, not executed controls or installed
proof. The independent source receipt SHA-256 is
`92da4d61b8e56e971bc613ac7854b65800735bc263468fe8c5b61b90df5c2a0e`.
The actual privileged-helper mount-namespace exclusion witness remains missing;
privileged ownership restoration and parent launch remain held. Unexpected private CLI-cache
members and unqualified helper acquisition remain unknown; neither is silently
deleted to obtain complete custody. The collector's negative or unknown
property must remain a failed result.
No collector commit, push or runtime run has been released by this disposition.

## Private workspace source acquisition: transport interface held

The researched official App-token action, immutable pin
`bcd2ba49218906704ab6c1aa796996da409d3eb1`, exposes token, installation ID
and App slug, but no actual issued expiry or effective permission response.
A conventional requested/provisioned-scope trust model was considered explicitly;
it would not be observed minted-response evidence. The exact action also
exposes neither redirect disabling nor request cancellation/deadline controls.
It therefore cannot satisfy the selected credential transport and shared
source-deadline requirements unchanged.

The selected source design instead uses official local App JWT signing and
native Node HTTPS requests with fixed destinations, default TLS verification,
no redirects or replay, bounded bodies and explicit retained-resource closure.
Node, npm and the dependency lock are pinned. This is source feasibility;
installation, actual permissions/expiry, credential custody and transport
behavior remain unproved.

The first five-file implementation was frozen at controller SHA-256
`bded67100f2ea60ccc5f71f5c83d928209f065f457d235e4d952378c9d375865`
on baseline `5be46529c122c7133ed1a85e6e344ee14105fb11`.
Independent source review held that candidate for four concrete findings:

- Generic private-file cleanup could bypass App-specific disposal guards,
  including partial acquisition.
- Authenticated Git inherited ambient proxy and TLS override variables.
- Required controls did not exercise the actual request/write helpers across
  failure and custody cases.
- Bootstrap cleanup requested a kill but did not complete bounded child/group
  settlement before dependent disposal.

The first controller repair, SHA-256
`cc7ef5b86cb0d41f609b74ace22b0ef6633c3a5b2cabfe78511262a5276f80a7`,
adds an early sensitive-path registry, excludes unresolved App custody from
generic cleanup and qualifies a narrow Git child environment. Review found
one remaining disposal fence: the actual credential-consuming `workspace.fetch`
must also be settled before App files are removed. The follow-up controller
source, SHA-256
`af793fc20834cfd9c790fe6315c6caaea72dd02f629390e414197be2f30f4ed4`,
now admits that actual consumer through the same settlement predicate used by
its declared controls. Root accepted this bounded source correction after
reading the exact delta and checking preservation independently. Actual
cleanup, Git transport and controls remain unexecuted.
Existing empty-credential-helper and disabled-redirect Git arguments were
already present and remain preserved.

The actual-helper control corrections passed bounded root and independent
source review at controller SHA-256
`e58c66fc475b4f8f65225c1d9468ca155774fa652c0b444e3b565f0f29e2d202`.
The adapter initially passed source review at SHA-256
`dbd85b6c12819df773e05209c6feca3180a43acc46f7c010f25151d2ae8690a5`.
Subsequent sensitivity review found a real masking defect: an oversized write
fixture could still fail on a later size check when the intended write guard
was removed. A two-line control-only correction stores otherwise valid bytes
and requires the specific write error. The final adapter SHA-256 is
`75ded3022b1ddb8896127e6571419b3817fd089253b5da3ee4bc21a644c58a9f`;
production definitions are byte-identical to the preceding adapter. Root and
independent source review accepted that bounded repair. The separate supported
quality run below now provides bounded JavaScript syntax/control evidence;
Python parent controls remain unexecuted.

The workflow-only settlement correction also passed root source review at
SHA-256 `37253ad9946a74c6c907d85f0c8d6813adefa315494ba65a34271f0934303296`.
It uses one shared two-second failure-only observation ceiling inside the
unchanged 45-minute job guard. Actual reap, group absence, stream EOF and close
facts gate dependent capture/directory removal in source. This adds no success
deadline, job minutes or reserved overhead, and makes no time-fit guarantee.
Actual process and descriptor settlement remain unproved.

A separate App/runtime-credential-free quality design was accepted for supported CI only:
load the actual adapter helpers without invoking main, run the unchanged
controls, and require ten exact production-guard mutations to fail at their
specific control assertion locations. A generic rejection, timeout or parser
failure cannot count as drift detection. The two new quality-lane source files
passed independent source review and were integrated with current main
`7e737cacdba56b214e427afed5a28e89ae1b6a47` as candidate
`500dd7402008411f3d03d2bd32c5697698b8cdbc`, tree
`a2d1bd92394ee56e6fd9e146e87a11a9c3029631`, on
`test/f4-source-auth-quality`. One separately released push produced
[supported run 37216604331](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37216604331),
attempt 1, which completed successfully. Its
shared deadline, finite invocation roster and closed output are independent
of the stopped fault campaign. Parent controls, official installed signing,
production CLI, socket transport and credential custody remain separate gates.
Independent source/design review receipt SHA-256 is
`fd308ae5e5a6cf2d7698a6f950341db2da0fdd58d8a7cb4176357ecfcbc183f8`.

The actual closed summary reports syntax and baseline controls passed, all ten
exact mutations detected (status, complete, response-limit, aggregate-limit,
abort, utf8, association, permissions, identity and write-progress), and all
24 driver Node children settled with cleanup complete. Root read the complete
job log and checked the summary against the frozen source and causal mutation
table. The observed profile was Ubuntu 24.04 image `20260927.320.1`,
Node `24.21.0` and Python `3.12.3`. Supported evidence receipt SHA-256 is
`6f2a487676921b0c783a8ec0117af52aedd8cabb40f54a9db5d7143d921628c5`;
the extracted actual summary SHA-256 is
`5c4eca3c58c189c2a6b08e1a26f88836fd859e386c0dfac9379369440572a883`.
Distinct review independently joined the committed blobs, live GitHub run/job,
full log and exact summary, accepting only this bounded executed-control proof.
Its receipt SHA-256 is
`90f9bac6b634868ebb022e34659eb72190469b360a1cc116f120619c52633808`.

This proves those actual helpers and inert controls for this supported profile.
It does not prove Python/Rust response, database or event parity, production
CLI behavior, installed official signing, real credential/socket custody,
Python parent controls, the fault campaign, or M4/C2/C3 acceptance. Checkout
used the managed repository-read GitHub token; no App, operator or runtime
credentials were used. Setup-node printed its default package-manager cache
setting, but no package cache restore/save or dependency installation appears
in the log. The 24-child claim concerns the driver, not every trusted action
or runner process. This successful lane is closed without an unchanged retry;
it neither revives the stopped fault campaign nor satisfies normal pre-PR/CI
gates for a future pull request.

A bounded independent packed-source audit matched all sixteen immutable
registry archives to the lockfile integrity values and inspected the official
local-signing import path. It found no install lifecycle scripts and recorded
MIT/ISC notice obligations. The audit receipt SHA-256 is
`d4b77480538473dec414200a80d48723dcd45f5d033c11e9bd4588d896960c88`.
This is archive/source correspondence, not an installed dependency, exhaustive
security or vulnerability certificate. Package installation and signing remain
unexecuted; unused OAuth/installation network paths are not approved.

The current explicit scope is 219 parent operations plus six workflow-native
processes, with pinned setup-action internals separately declared. Source
inspection is not a certificate of all runner processes or installed behavior.
No custom cryptography, action fork, runtime credential change, secret
provisioning or fourth controller run is authorized.
The third run's original fetch failure cause remains unknown; an unprovisioned
private read-access prerequisite is not retrospective proof of that cause.

## Claim characterization: driver source accepted, first check failed

The private Rust driver now calls the actual existing-attempt claim planner
and serializes its four outcomes and eight write directives. Candidate
`7880f4728ffdd97115a6d7935dec63da406c60f2`, tree
`6d90482614eee978d8fda32be0a397427173cc1a`, contains current main
`7e737cacdba56b214e427afed5a28e89ae1b6a47`. Independent source review
accepted the two-file driver/CI package; its receipt SHA-256 is
`79f6f833922eb1b0eb63193705390eec4105cf7c9c3662caa44767ab6d3fb5a1`.
The 14 original example tests remain unchanged; three new tests are declared.

The first [supported source run](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37221553752),
attempt 1, failed at `cargo fmt --check`. The native diagnostic requests four
tuple line wraps in the new guard test. Clippy, default tests and the explicit
example tests did not start. This is a diagnosed formatting failure, not a
functional result. Only those reported wraps are released for source repair;
a changed second candidate needs independent review and a separate execution
release. No unchanged retry or expectation waiver is permitted.

The claim scenario design distinguishes 40 original-consumer cases, two direct
original-helper cases and 11 impossible-row native boundary controls. Thirteen
consumer cases characterize Python allocation while Rust explicitly defers it;
they are not equivalent allocation implementations. PostgreSQL constraints stay
enabled. Corrected scenario design review receipt SHA-256 is
`32ab46b90929ed68eca4781df07e7cca7f0075ef31c558ec273413362ab730e4`.
No claim characterization cases have run. Constructor/source association,
connection and frontend lifetime evidence, execution allocation and actual
Python/PostgreSQL parity remain held.

The M4 helper namespace preflight and its unexpected-exception regression
controls also passed bounded source review at conductor SHA-256
`474fbc4cde84874be3ece8a2f4837c06395e7783d592ab132accf6f1ef059e20`.
Independent review receipt SHA-256 is
`1765c6f6aed9674c3f720c4d134c876e806870b80d9392f7d16156791d26e6b0`.
These controls have not executed; installed behavior, resource custody, cache
disposition and runtime admission remain held. This closes source findings,
not M4, C2/C3 or MVP acceptance.

The exact four native formatter wraps, including their optional trailing tuple
commas, passed independent source review. The repair changes no arguments,
ordering or expectations. Receipt SHA-256 is
`70b2784c6906794792475c79a33113fbe2cdf4973d866b72f428da605ebb8539`.
One separately released changed candidate,
`6f41f297aa6f2107086474bc402704647967b1d3`, tree
`1c7cb34dea16af6ae792d4eb6830fda29ff5f5a0`, passed
[source run 37221919975](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37221919975),
attempt 1. Actual formatting, strict locked all-target/all-feature Clippy,
31 default workspace tests and 17 explicit locked/offline example tests passed;
none failed, were ignored or filtered. The example tests ran using the actual
checks image ID with networking disabled. Root evidence receipt SHA-256 is
`c209914e3107e3d664b1ad4269d51e5c2f0fdbbfd81d9ca214fabed5dcfeca75`.
Independent review accepted this bounded source/unit evidence; its receipt
SHA-256 is `6e53cedb7026307554ee275f1220063bbc953a43f2b2307ef75da0e8ddb357fd`.
This source-check campaign is closed successfully without an unchanged retry;
its first formatting failure remains
recorded. Neither run executes the 53 proposed characterization scenarios or
proves Python/PostgreSQL parity, lifecycle authority, rollback, C2/C3 or MVP
acceptance. Literal pre-PR and normal CI remain separate PR gates.

## Claim characterization: standard-lane lifetime seam

Fresh main fetches remain platform
`7e737cacdba56b214e427afed5a28e89ae1b6a47` and workspace
`848134becf9e8fdb930efc86872daa62fdeb93cc`; the prerequisite merges remain
ancestors. The workspace boundary audit covers that exact unchanged head.

The original test launcher restarts PgBouncer before creating its pytest
runner and removes that runner on completion. An outer before/after probe
therefore cannot establish the required same-runner lifetime. The proposed
test-owned handshake observes readiness after the actual pooled constructor
returns, then completion after the selected function finalizers, while the
same runner remains alive. It preserves the original launcher and reset.

Independent design review accepts this direction conditionally but holds
source authoring on three concrete interfaces: mandatory gate selection that
does not depend on socket presence, physical whole-script deadline and cleanup
enforcement, and loop-neutral broker/finalizer custody that observes actual
cleanup failures. A closed driver is not sufficient evidence that disposal
returned successfully. The source proposal receipt SHA-256 is
`322dd8ee08342c4ba8473e51c5761a570ca6e6d15704365e8f28223d2b24fdec`;
the independent design review receipt SHA-256 is
`b389fb85f90dca8acc69cc0ea3ff27b0841cb5a8eae846c9c84456ed629fe08f`.

Actual frontend image/version/configuration remain unmeasured. A conditional
reviewed profile must fail closed on a mismatch; neither checked-in defaults
nor a self-reported version prove transport or installed source. No frontend
runtime, Python claim scenario, or new characterization campaign has run.

The architect selected the following interface direction for the finite
amendment: a closed receipt declares the frontend requirement without asserting
success; every new genuine claim case requires the witness. A synchronous
module broker avoids sharing async transports across function event loops.
A test-owned forwarding alias observes each original `close_db` invocation
exactly once and retains failures through both unchanged function finalizers.
The incumbent isolation fixture still wraps ordinary exceptions; preserving
the original error at the observer boundary does not change that behavior.
The public synchronous driver `is_closed()` getter can provide per-connection
readback, but cannot certify all pool resources or transport security.
Independent source-data receipt SHA-256 is
`e14cc9d11e54f747b71587644aab344941e8eef18188cf33cb4dd0597f722e80`.

Deadline enforcement belongs in a fixed executor for this existing parity
script's original operations. The selected design reserves script-entry
seconds 2520–2640 for cleanup and 2640–2700 for publication, without extending
the workflow's 45-minute limit. The earlier job clock can truncate those
reserves; no publication or fit guarantee follows. CLI/process-group/pipe
settlement does not prove Docker daemon work settled. Unknown remote resource
state remains a failed gate. Exact operation and finalizer choreography still
needs the finite amendment and independent review before source release.

The completed amendment passed independent design review, receipt SHA-256
`1bc0470e21caeb02b970124433f75ad6fab88712705109e2f3783ce97b3b74c3`.
It selects 71 direct parent command slots, including full builder readback and
three separate cleanup census/container/image readbacks. This is not a count
of opaque descendants or proof of execution. All 34 consumed source paths are
frozen; actual future candidate hashes remain required.

Source authoring is released in three existing paths: the Python harness and
its test module, and the parity CI script. The 53-case arrangement fixture is
committed locally at `8b7d2c6597fd134044e74249e188c55a9c3c0844`; only its
schema label differs from the reviewed design. No cases have executed.
The broker uses a local 20-second exchange bound. The unchanged pytest timeout
and actual parent deadline independently enforce their outer limits; the
broker does not claim to know their remaining clock budgets. Independent
clarification receipt SHA-256 is
`6fb59d1fd2d1e5e94a4ec92eac5192db9086f699baca11b6c488d5db548248e8`.
Authored source review and a separate supported execution decision remain
required before any new characterization run.

The latest workspace fetch is `78f37f5a5cbd7b0a6c382b54ccc617c8747418aa`.
Its change touches delivery configuration and documentation only. Exact Git
tree comparison preserves all 2,155 Python entries from the previously audited
head, and committed AST inspection confirms the allowlist remains empty.
This preserves the 2,140-file authored boundary audit without claiming a fresh
runtime scan or Rust-specific workspace accommodation.

The three characterization source files are now author-frozen against local
`8b7d2c6597fd134044e74249e188c55a9c3c0844`. A fresh platform fetch remains
`7e737cacdba56b214e427afed5a28e89ae1b6a47`, contained in that candidate.
The Python author report has SHA-256
`5dc5075bf2ce2f6e7a10747f863d87cbadd63e0a28ea92e1cd6dbb3614f66e06`;
the CI author report has SHA-256
`e97953ce48d5c4b9fd906263fbafe1e72ce090523ae9a527a12132d42cb9283e`.
Root verified their exact file hashes and read both reports. Separate full
source review is running; neither author report approves runtime execution.

Declared venues comprise 35 genuine Python cases with native planner calls,
seven reference-only cases with no native call, and 11 native-only cases with
no impossible SQL rows or original consumer invocation. Native-only items
still resolve the test's shared fixture dependencies; they do not establish
absence of a fixture engine. Source checks are not executed drift detection.
The harness is now 2,581 lines and the CI script 2,200 lines. Review must assess
that complexity against architectural benefit as well as correctness. No new
characterization runtime, commit of these three files, push, or acceptance
claim has occurred. Existing authority, coexistence and full-MVP gates remain
open.

The full three-file independent source review subsequently returned HOLD,
receipt SHA-256
`2456a98d7eb2da5356b363e024f183ec8cdfdf0b6f758db2a828c4c0084a292f`.
Six finite findings require correction: independent cleanup after partial
process acquisition; first-error protection during settlement; frontend match
publication only after completed lifetime admission; canonical compact frame
admission; file operations bounded by their original work/cleanup phase;
and unexpected control exceptions preserved by expected-fault tests.
The fixture-engine wording correction does not change fixture behavior.

Root released one source-only repair batch, receipt SHA-256
`48a3d6db5a81ffe9552bfd5d4b12a76020e3f71f0ea3d79064eeff60d66cc12e`.
The parent and Python owners retain separate paths. Exact scenarios, commands,
clocks and authority remain fixed. Repaired bytes require distinct review;
this disposition provides no runtime, cleanup, frontend or parity acceptance.

The H6 test correction passed separate source review, receipt SHA-256
`27ab7530ea272f83dde49563cb11ef0ef8452816d4456a93cfcdc8c223003fae`.
Parent v014 corrected the five implementation branches, but its separate
review remains HOLD, receipt SHA-256
`f744cf61452fe127f7239f0064484cc0f121c9a67bcabbef4791e747aa272107`.
Two obligations remain: controls through the actual launch-to-settlement
retention path, and original CleanupEnd admission before new termination
signals. Root released a finite combined correction with inert underlying
bindings, preserving the actual production launch path and all existing
clocks/commands, receipt SHA-256
`bf30578130f614dee4cfa87f5c3bd1f496fee55fc3a30b8c54e7c46129ff6792`.
Declared source inventory predicts 207 selected JUnit items; none of the new
claim cases or controls has executed. Reference-only cases and allocation
deferrals remain distinct from Rust parity. No full-MVP gate is closed by
these source verdicts.

Parent v015 closes the remaining two source-review findings, independent
receipt SHA-256
`966ccad13349f34fc66d9265e34389755263a2c7f895fdaa1c5bbf800c45e819`.
Root accepted the exact three frozen paths and released one changed-candidate
supported run, receipt SHA-256
`c533c8dbe947c3ba8ab67731612a4caef8d9109208037cc5e892298126c0cdf3`.
Clean candidate `0ef442248babd12a829543d801e5c6666a785ed2` contains platform
main `7e737cacdba56b214e427afed5a28e89ae1b6a47`; workspace main remains
`78f37f5a5cbd7b0a6c382b54ccc617c8747418aa`, preserving the recorded boundary
proof. Only the three accepted source paths were committed in this step.

The candidate was pushed to `test/workflow-claim-characterization`.
[Supported characterization run](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37232401370)
is workflow_dispatch attempt 1;
[source-check run](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37232398145)
is push attempt 1. Both were verified in progress at release readback.
Their results remain unproved. The existing characterization command retains
literal `./test.sh pre-pr`, all original checks, fixed clocks and cleanup gates.
No PR, merge, deployment, authority change or full-MVP acceptance is included.
No unchanged rerun is authorized by this release.

The candidate source-check run37232398145 completed successfully; separate
executed-evidence review accepted formatting, strict Clippy and actual31+17
tests, receipt SHA-256
`193163eea4e3a2e17670c6d919ab943fb04ac110602458719b5773f0ecb7728a`.
Actual checks image is
`sha256:81b3da3503e2f321778c5f4687e8ff72bc98af6c0e4685a745fc50a440a3c256`.
Retained source-check log SHA-256 is
`6266c8114a3117288bfc83e016e24630a843cbd6d718d504f02ac1ec03c3f8ae`.

Characterization run37232401370 completed FAILURE on attempt1, job111524699452
in3m56s. Its main step exits1 without publishing the primary exception or
native stage outcome. The evidence upload sees five files but independently
fails EACCES opening `source.txt`; the artifacts API reports zero artifacts.
Retained full job-log SHA-256 is
`75b45d92130489b2b220409c001462815d768a87954b4bf4f0cf0651b213488e`.
Neither pre-PR success, Rust/Python drift, actual selected tests nor cleanup
can be inferred from this failure. Ownership changes by the mounted-log
entrypoint and publication0600 are a source-supported upload hypothesis,
not recovered runtime identity or an explanation of the primary failure.

The candidate remains HOLD. Root released bounded read-only diagnosis of
the publication and safe diagnostic seams to the existing parent owner;
no source repair or replacement run is released yet. No unchanged rerun,
time/cap growth, pre-PR bypass, public-contract change, auth/schema/Start
authority, merge or deployment is permitted by this disposition.

Read-only diagnosis isolates two finite source seams. Original runner
entrypoint ownership changes can make the parent's0600 public evidence
unreadable by its host uploader. Independently, the parent preserves the
primary error privately but exposes only exit1/130 after conditional capture
disposal, leaving no safe first-stage diagnostic. Neither finding identifies
the actual primary failure or proves actual ownership or private disposal.
Independent source notes SHA-256:
`82a0fea903d0f5c6db4f3976d7d82299542affb1aa0632abacb174686e65aa14`.

The parent owner's frozen diagnosis/design proposal SHA-256 is
`b03e9921435c13cc1d066bd5afd745e6af5ac47a363b4bd5c6dadd6efc555083`.
It ranks falsifiable pre-PR, safe-file/custody, native build/settlement and
stack/frontend hypotheses. It proposes0644 admission on the newly owned
FD for the existing approved public outputs only, plus one closed bounded
first-failure diagnostic frozen before cleanup. Actual failing-native
association must not be replaced by the last launched child. Raw outputs,
exception strings, traceback and private captures remain excluded.

Root completed the proposal read and requested separate design review.
One evidentiary correction is mandatory: the proposal's statement that
private capture disposal was independently reported is unsupported;
disposal remains UNKNOWN because neither the log nor artifacts prove it.
No source repair or replacement execution is released by this checkpoint.
The candidate stays clean at0ef442248; both main heads remain unchanged.

Independent design review found only the unsupported disposal assertion,
while accepting the bounded permission/diagnostic interfaces, receipt
`9013015f5c1e321a935cce4cde4dbb123de253b66a20c1ed287312d9dcaf6e39`.
Root corrected only that sentence and the revision title in an immutable
v011 copy, SHA-256
`4acb16060801a883952910515553469a7f5c2dfe7cccb5b578eaf93a949c6a68`;
the original report remains historical evidence. Actual disposal is UNKNOWN. Independent
readback closed the sole design HOLD, receipt
`b49d78e7ac112186055799b3745909c5808c25ff2f007c4936f9fd48fcab84ef`.

Root released source-only package CLAIM-CHAR-PUB1 to the existing parent
owner, receipt SHA-256
`f57fc0199e288af773bd013f125d68b5e35b7defbad1f8b7ef68223881c5f547`.
It owns only `scripts/ci/workflow-domain-parity.sh`: same-FD0644 admission
for eleven existing approved public outputs, and a once-only512-byte
ASCII stderr diagnostic with six closed keys, actual failing-native
association and immutable first-error observation before cleanup.
Nonblocking output must independently restore inherited descriptor state,
never close it, retry, expose payloads or replace the original failure.
Private captures, all original command/scenario/clock/cap/security and
pre-PR contracts remain. Exact changed bytes and controls require root
and different-author source review. No replacement run, commit/push by
the builder, merge, deployment or authority migration is released.


CLAIM-CHAR-PUB1 is now source-frozen, not source-approved. Root independently
verified the actual sole-path delta and all33 unchanged incumbent input
bytes against candidate0ef442248. Complete parent source SHA-256
`4ade64564326cb2682030c7e9eb17fc6e997177763ae91ba0b14cbf977520c77`
(3337lines/142901bytes); complete incremental delta
`a686cc4ca3e7dab570531508c2a7a12de6a4554301cadefd2e27f79037b8bcb3`.
Root fully read the delta, author report
`33b6e27c6abeb33765046e44da2c16b8c12cbd07d8931481a5b6cb114576217c`,
inspection `9a35df77226d2951ddee346d90265c74cd6998a68441165fbee0058155677615`
and cleanup-primary reachability assessment
`85bf69c4697e0f7505b81468aa6e19d99241c89062693e377763436de40fb93d`.
The assessment distinguishes a synthesized unsettled-child counterexample
from the ordinary entry path: successful work has waited every actual native;
failure settlement already retains a work error. Independent review must
falsify or confirm that argument against the actual call graph. No cleanup
rewrite was added on the strength of an unreachable synthetic state.
Author Bash/AST/diff and isolated cached Ruff120/py313 checks pass; root
independently checked source hashes and diff whitespace. Controls remain
unexecuted. Concrete different-author review has been requested against the
frozen bytes and source-only release, before any replacement run. First-run
cause/disposal, actual uploader delivery,207 JUnit identities and full
workflow/agent parity remain unproven. Fresh platform/workspace main fetches
remain7e737cacdba56b214e427afed5a28e89ae1b6a47 and
78f37f5a5cbd7b0a6c382b54ccc617c8747418aa respectively.


Independent concrete PUB1 review is complete, receipt SHA-256
`8240a08ed8f604aa9e7cfcb69210da70636fe32e22f40ff34b2ff2af5160057d`.
It found no semantic blocker and independently confirmed the ordinary-entry
cleanup attribution argument. Final source acceptance is held solely for
two control-only lambda early bindings at script2237 and2413. Root's fresh
static audit, `72cbfde2424d51db5aa293bf8f2b32a33f8a6c0b63950c7a03f83ee7cdd3a281`,
passes Bash/AST/default lint/format and finds two B023 issues under auxiliary
expanded E,F,I,UP,B,SIM rules. Synchronous invocation currently avoids a
deferred late-binding bug; explicit bindings remove new control-source debt.
An initial extraction omitted the final newline and caused an auxiliary
format failure; exact newline retention passes, so no source-format failure
is inferred. Root released only those two bindings, receipt
`91708bb0a0f4717f19e6810d6e7270bf4554eb1a58cbafbe8f2b089f543ca2da`.
Frozen changed bytes, inverse AST and independent correction readback remain
required. No replacement run, changed expectations or runtime authority is
released. W0 PR1008/1009 remain unchanged and CodSpeed-blocked; amendment
PR1011 remains93ec9421b23b0b4a1493ab45f15f55ded3377d2e, open and unmerged.


PUB1 final bounded SOURCE is accepted by independent readback
`82db77b4451ec7a483515489da72f487b1f43ac28c739be0a426cdf07547f22a`
and accountable root. Exact inverse bytes recover reviewed4ade; root
Bash/AST/expanded lint/format/diff and all33 incumbent hash checks pass.
The sole-path repair was committed and pushed as
92a7a611d901b035c0f14ba26adac4825b655a8d, tree
e91e2975fbaec823b84e08fc94187414cb6a07a9; clean candidate contains current
main and both prerequisite ancestry checks pass. Script hash is
`ad1094cfb16741caa10c421018cbc5eaae8353ff42b42a94db552ca4cf21a8c2`.
Root one-changed-candidate run release is
`1748ca4a093b6d48972a68814525bcd931bf51481ab0a8c7183edf4c95464f91`.
Supported characterization run
[37235996706](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37235996706)
was actually dispatched on that head, workflow_dispatch, initially in progress;
incumbent push source-check run
[37235989500](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37235989500)
also starts on the same head. Neither initial live status proves any executed
gate passed. LiteralprePR,207 source-derived JUnit identities, real reference
observations, fixed envelopes, scoped cleanup and safe artifact delivery remain
required. No unchanged rerun, PR/merge/deployment or authority expansion follows.


The second changed-candidate characterization run37235996706 completed
FAILED on attempt1, job111535096810. The main step ran21:25:08–21:28:45UTC;
the new closed diagnostic records phaseprepr/operationprepr/exit1,
native_settled=true and classguard. It proves the literalprePR process failed
before selected parity, while its inner failing subgate remains UNKNOWN.
The first0ef run's missing cause cannot be retroactively filled from this run.
Safe publication/upload now succeeds: artifact11315408715,1406bytes, digest
`b0e42573f378b5b99d2221ccce22bb1f6c84ea5de542949ff26c7a511b6c9ad2`.
Root read all five approved members and verified archive SHA: source.txt joins
exact92a7 head/tree; both exit files1; frontend.json null/null/matchedfalse;
project-resources.txt reports cleanup_status0/inspection_status0 and allthree
final census strings empty. Initial census values are UNKNOWN in source;
empty strings are assigned only after actual final owned-scope census calls.
This is parent-reported scoped cleanup evidence, not global absence or a new
independent private-disposal observation. Exact evidence receipt is
`9bf7f6be2f6513946c9ed2ec72f3d785ccf9111566fad3dac85c2e340ce196b0`;
full retained log SHA
`6af3d062c56217a9832ef1a7842b772d807cae1b266ccdb242433444d59079fe`.
Independent failed-run evidence assessment and read-only source diagnosis of
the pre-PR prerequisite are requested. No raw private output, unchanged rerun,
gate bypass or Rust parity is inferred.

Separate source-check run37235989500 passed formatting/strictlockedClippy,
31default tests and17offline/network-none example tests on the exact candidate.
Independent executed-evidence receipt
`ceb88f831eb12fa4746cad9f05fe7a5e273aba3a3daeaaa80016f1ba482ddc4f`
is accepted by root after full relevant test/command blocks and report read.
Actual image is sha256:c026eb72362620df90f6e03a04709a00c8533a982a0b0aa3da7077597a491e92;
complete log SHA
`0ea8c9d79f8da579b44f31cb7b1ce98c0bfe8c5bbcc348721c4d6858a53b1de5`.
Neither this check nor successful upload proves pre-PR,Python/PG parity,events,
owner-grants,Start,agent compatibility,rollback or full MVP completion.


Independent failed-run evidence review
`1607fa65f795161907053622f90fcd07aba2017049b90516557897a61118a396`
is accepted after root full read. It confirms actual prepr failure/settlement,
source-controls execution/normal return, allfive safe members/digest and
source-backed actual final owned-project empty censuses. Zero cleanup errors
on this source path supports successful scoped private unlink/rmdir for THIS
run; it is not an independent filesystem readback, global process/daemon/FD
closure or retroactive evidence for the first run. Rust build/target/frontend
and selected207 JUnit comparison were not reached. The exact pre-PR subcheck
remains UNKNOWN; only read-only source diagnosis is released. Do not rerun
unchanged, waive pre-PR or redefine MVP success around source checks/upload.


Second-run pre-PR source diagnosis is frozen,
`6e11a3827f137236a9b31b046895d1db7f7b894e737681fec382d8947af29682`;
root read it fully and verified allsix actual candidate source pins against
inspection `1b9c3883795cd0066b882c233c82fa20deca9d3efee2b2bc65b87e433cd651c2`.
It finds no source-provable subgate cause. The proposed next seam observes a
fresh host Git-metadata stage ledger after native failure/settlement, before
cleanup: fixed65,536-byte private read, physical-checkout-only qualification,
private unchanged-helper binding, closed stage/status projection. Existing
primary/v1 sixkeys stay unchanged; one additional versioned three-key ASCII
line would be bounded192bytes and combined diagnostics512bytes. Missing,
preexisting, mismatched or ambiguous records remain unknown; running denotes
incomplete bookkeeping, not a proved tool failure. No raw signatures or
logs, native/helper invocation, clock growth or gate waiver is proposed.
This additional output/dependency is explicit DESIGN scope, not silently
covered by PUB1. Independent design review is requested; source authoring
and any changed-candidate run remain unreleased.


Independent stage-diagnostic DESIGN review
`8f9174d8bdca7677974b3880958a4d79c107294c22ea7ced3f641f9c05426866`
accepts the bounded direction but holds source allocation for exact
status-dependent signature grammar and retained .git/locks path-to-FD
association. Root fully read it and released only a design clarification.
The eventual primary+stage output must use one combined bounded write;
unknown stage observation cannot suppress the valid primary line. No source
or run is released by this review. A separate decision-ready M1 transport
addendum is being prepared against actual locked crates; authority and
business-admission projections remain unapproved.


### Stage diagnostics and isolated transport: source allocations (2026-10-04)

The stage-diagnostic design holds are closed for source authoring. Root read the
complete corrective design and exact 33-variant grammar; the different-author
review accepted their conjunction. The sole owned implementation path remains
`scripts/ci/workflow-domain-parity.sh` on the claim-characterization worktree.
Allocation `2f9f70d500eb316714d4a35c36f0b12a684f4a164bee205303bc49f774b309de`
allows a privacy-safe stage/status observation after the actual failed pre-PR
child has settled. It preserves the existing primary diagnostic and uses one
bounded combined write. Missing, stale, malformed or ambiguous evidence remains
unknown. This is source work only: concrete review and a changed-candidate
supported run are still required. The observed pre-PR failure remains unresolved.

The independent native transport design review also accepted a separate
synthetic experiment for source authoring. Review receipt
`9acf37cea3ff45d85c81994fe992cb8ff1cda21ee8c360da9a66b1e9fbd718e3`
applies the original design, corrective addendum and mandatory streamed-response,
explicit-provider and bounded-cleanup conditions together. Root created a clean
worktree at `/home/thomas/src/bifrost-m1-native-admission-transport`, branch
`test/m1-native-admission-transport`, from candidate
`92a7a611d901b035c0f14ba26adac4825b655a8d`. All eight selected baseline source
pins were independently verified before allocation
`d127de56889ff2923ebc6ccd40d5fe7ac89867238584f7b29f2b733b559e1bee`.

Its five owned paths are the workspace manifest and lock, the core crate
manifest, a new `admission_transport_prototype.rs` example, and a new
`api/tests/diagnostics/core_admission_transport.py` diagnostic. Only two inert
probe routes are admitted. The experiment uses native mandatory client TLS and
revalidates actual peer certificates and the current leaf allowlist on every
request, including a retained connection. It introduces no business admission,
production bootstrap invocation, database access, runtime grant or SDK change.
New dependency packages or versions, extra owned paths, custom cryptography,
fake expiry clocks, automatic redirects or retries require stopping and reporting.

Both allocations prohibit author commits, pushes and runtime execution before
root and different-author concrete source review. The transport suite's exact
scenario/child roster, source-to-binary joins and shared supported Python/Rust
venue remain execution gates. Existing Rust checks do not exercise this new
optional example or diagnostic. Actual M1 issuer and key custody, API-origin
assertions, business-operation authority, M2–M5 and C2/C3 remain held. Neither
allocation resolves the current lifecycle parity, mixed-writer or rollback gates.


Workspace main advanced to `6dfcf74a09743aa83d74f9fe582f8869828e61bf`
through #1181. Root fetched both mains and independently parsed all 2,140
current authored Python files in the committed quality gate's boundary scope.
There were zero parse errors, zero `src`/`src.*` imports and zero SQLAlchemy
imports; the internal-import allowlist remains an explicit empty `set()`.
Audit receipt `780a6159fb96c9161a50ff9265b5d04d64bae163d2312cc8b22f97140833e9a8`
binds the current scope source and selected-file manifest. This is a fresh
AST source audit, not execution of the workspace quality gate. #1112 remains
an ancestor. Platform main remains
`7e737cacdba56b214e427afed5a28e89ae1b6a47`, with #1001 an ancestor;
#1011 remains open at `93ec9421b23b0b4a1493ab45f15f55ded3377d2e`.
The Rust experiment requires no accommodation in authored workspace code.


The transport test environment direction is now selected from independent
source review `3e734c171edcdedcc92d10ef85ec9a7088391acc45c13e3869b2c1b52e887421`:
compile in the existing Rust checks image, then run the standalone diagnostic
and exact copied native binaries in one nonroot, externally disconnected
container from the existing API development Dockerfile's `builder` target.
Root independently verified all 12 committed recipe and manifest pins. No
Compose stack or application entrypoint is needed. Actual compiler-artifact
joins, ELF compatibility, installed wheels and the shared suite remain unproved.
A venue script and isolated workflow would require a separate two-path
allocation after the diagnostic's source and child roster are frozen.

Independent review `f811f335629b28e8c3556a6fd84bdae34491af8ab2b2faa40ffbf301112480a8`
clarifies safe Rust file cleanup for this experiment. Explicit `File` drops
provide ownership disposal without a caller-visible close result. They must
never be reported as measured syscall-close success. Dependent fixture-key
deletion instead waits for Python's actual settlement of every native child
and group, process reap, pipe EOF, known Python FD closure and unchanged file
and directory identities. Real/effective UID agreement is required in the
selected nonroot venue. Root accepted this bounded design clarification;
concrete implementation and runtime cleanup evidence remain review gates.


CLAIM-CHAR-STAGE1 source is frozen at
`39b51208093ef2d9671b2cf7b8b39c1c4fab497b2c58307f5725fdd629705324`
(186,217 bytes; 4,250 lines). Root read the complete delta, independently
verified the unchanged 33 other source-map paths and exact inert helper bytes,
and repeated static AST, Bash syntax, Ruff lint/format and diff checks. Those
checks pass, but source acceptance is **held**: four new negative-control
expectations use `json-shape`, while the unchanged `closed()` helper emits
`closed-keys`. This would fail the new controls before pre-PR. Root review
`a0964c6edc4cc9aa5e7a32288a2a5c5fb77d1ad195276b63d7c5aca9f117fbd8`
records the source finding; author acknowledged it. Independent review remains
in progress. No controls or runtime were executed, and no source commit, push
or changed-candidate run is released. Any repair must preserve the rejection
conditions and exact error assertions, with a new immutable source version.

Read-only PR reconciliation confirms W0 #1008 and #1009 remain open at their
recorded heads with CodSpeed failures, and #1037 remains open/blocked at
`3918d608a2d497d1ca8424fd4229d191323e2d7a`. No threshold adjustment, rerun,
merge or deployment was performed.


The four-label repair closes STAGE1-H1. Root and independent reviewer verified
that corrected source
`5f083b0d2f5d82b60cbf4e7bb270ff7833c727d0c0359d46bb3374f79698b9e1`
differs only in those expected labels; inverse comparison restores the entire
reviewed snapshot. Independent final SOURCE acceptance is
`afd0e79973d7d42201c33779eb4e46d70deddd8eecda4b1d87a070ee0919f328`.
Root committed and pushed candidate
`aafc710649b5f77f7835244323bc080e4d05cf1d`, tree
`8feece72550e07b1b1e34020f1ac78c22bc7e980`, with a clean worktree containing
current platform main. Release
`daba98802ee462610796ceb028a41aacaab7a015a59d9dc27fcf33c6299360ad`
authorizes one changed-candidate source-check run and one characterization
attempt, preserving literal pre-PR and all existing clocks/caps.

Actual GitHub source-check run37240412992 (push) and characterization
run37240412538 (workflow_dispatch) were observed in progress at that exact
head. Neither result is yet accepted. Controls passing, stage evidence,
selected parity and cleanup must be established from the terminal run and
approved artifacts. No unchanged rerun, private-output export, PR creation,
merge, deployment or M1 authority follows from this release.


Terminal results for the STAGE1 candidate are now available. Source-check
run37240412992 passed fresh fmt, strict locked all-feature Clippy, 31 default
Rust tests and 17 offline example tests. Root read the actual command, image
and complete test-result blocks; independent executed-evidence receipt is
`cd01ce14e429c4a7acf5bdc05323d4a722b8a2b32f04947a25b66b47e6c01278`.
The measured checks image is
`sha256:ebe08aae7facae1547fc4b90b751a8eaf2ee0937b2a21c0a2516c38b5f4b76e7`.

Characterization run37240412538 attempt1 failed at pre-PR after reaching
**quality / failed**. Its original primary diagnostic remains prepr/exit1,
native_settled=true, classguard. The exact quality subtool and cause remain
unknown. Reaching pre-PR supports normal return of this invocation's declared
controls, and the nonunknown stage supports completion of the reviewed reader
admission checks; neither is a general custody attestation. Independent review
`9b7cb8a90ed38e254dc2113760ab7acbd4d9899bd1814853e9a4a4ef83714027`
is accepted after root read the actual diagnostics and all approved artifact
members. Artifact11317087581 is 1,406 bytes with digest
`664ba8c51d3fe703424555c5104db393d31faeeabf3f5159f9cc435aa5c36efe`;
source/head/tree match, both exits are1, frontend is null/null/false, and
successful final scoped resource censuses report empty containers, volumes
and networks. This remains source-scoped cleanup evidence, not independent
filesystem or global disposal proof. Selected 53-case/207-venue parity was
not reached. No unchanged rerun is released; source-only quality diagnosis
is underway. The watch handle99615 is terminal and must not be polled as live.

The separate five-path native transport source has also frozen for concrete
root and different-author review. Author report
`7543b7ead8747a184bea5da8ec73683550d9a73b3d647bdd670372205c177b65`
and full delta `1d9a250d94d6e8ddfeec7d30009d8a5bf1ec6d1aa22d66ec8f52c87f2bb843ae`
claim four native declarations, 16 Python cases and three sequential native
children. Those are authored declarations, not executed results. Rust
compilation, PKI, runtime and the separate two-path venue remain held.
