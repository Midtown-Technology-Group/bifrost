# First-class Go acceptance audit

Decision: **CONTINUE SPIKE**. SDK `0.0.0-spike.2`. Updated 2026-10-11.
Thomas released the common interfaces at `b8b44cbc01b1fb6be86f4fba050e93eb0da55924`
for isolated implementation after independent codec acceptance. That release
remains effective; it does not accept the runtime, writer or profile.
[Exact authorization](isolated-implementation-authorization.json).

The latest fully inspected real-owner proof is source
`c739480ce7f79168d5df173158147f7be2d94700`, native/live run 38130627987 and
writer/security run 38130628006. Both passed. [Retained complete proof](nine-case-proof.json)
verifies nine executions of the unchanged Go binary, actual closed-Result owner
crash recovery, uncertain release without material delivery, 13,898 unit tests,
126 baseline and 248 guard cases, API quality and empty resource inventories.

All three candidate lanes passed, including Rust foundation run 38130627964.
Behavioral evidence includes closed-cancellation recovery before resource settlement.
The [partial six-case proof](six-case-partial-proof.json) at581d passed runtime cases
but failed the mandatory live-client proxy shutdown regression; it is not accepted.
The [failed bootstrap](live-bootstrap-failure.json) atb105 did not reach admission.
[Dated historical audit](completion-audit-history.md) preserves earlier source
pins and failures; prior missing-work statements are superseded here.

## Required vertical slice

| Requirement | Current evidence and limit |
| --- | --- |
| 1. Ordinary Go authoring | Implemented readiness handler uses packages, typed input/output, a consumer interface, loops, context and ordinary errors. `main` calls `bifrost.Workflow(Run)`. No DSL. |
| 2. Ordinary tests | Normal Go tests run in the isolated producer. The representative handler tests need no control plane. 540 individual Go tests passed; six command packages have no tests. |
| 3. Isolated build | Read-only source/modules, separate scratch, network-disabled tenant tests/compile, resource limits, no runtime/vendor/DB/signing credentials. This bounded container recipe is proved; hostile multi-tenant production builder acceptance is open. |
| 4. Dependency/toolchain identity | Pinned Go1.27.2 Linux/amd64, CGO0, readonly modules, checksums/module graph and builder/recipe evidence. No dependency installation at execution. |
| 5. Static/security checks | Producer executes gofmt, vet, tests and pinned govulncheck. Existing backend quality and writer/race lanes remain required. No transferred status or scan waiver. |
| 6. Immutable artifact | Unchanged 9,942,367-byte Go binary SHA256 `5f9c912ec6055882e0d443378cb93bb87b58eb7ab2a49eb5e14d338d72865edd`. Native archive separately binds adapter, schemas, graph and build evidence. Source identity and archive identity are distinct. |
| 7. Neutral registration | Explicit JSON manifest/schemas and immutable native descriptor. No source execution for metadata discovery; Rust reads the descriptor/association, not Go AST. General schema extraction and product registration UX remain limited. |
| 8. Accepted deployment pin | Real migrated native association and existing workflow/Solution deployment are checked in the isolated live fixture. Accepted archive bytes and owner binary consumed by that run are independently hashed. No production trust anchor claimed. |
| 9. Rust admission/Start | Real Rust transactions create the existing execution/typed attempt/owner/session and commit Start before material delivery. No Go-specific lifecycle table or fake durable store. |
| 10. Published protocol only | Independent Go/Python/Rust codecs and session vectors cover the proposed common profile. Go adapter uses that profile; workload sees its typed capability context. Human profile/runtime acceptance remains open. |
| 11. No Rust linkage | Workload/SDK have no Rust crates, structs, FFI, DB, Redis or private scheduling knowledge. The independent Rust owner/codec is an implementation of common contracts. |
| 12. Restricted SDK | Real synthetic TLS integration-get uses the finite dedicated attempt/session-bound credential, exact organization/Solution/name, no redirect/renewal, and ordinary-route rejection. Issuer and ingress are first-party fixtures, not a live vendor. |
| 13. Logs/Result/Receipt | Actual adapter LogBatch/Result, raw-payload SHA256 and committed receipt correlate with Start. Normal ResultReceipt follows observed commit; lost Result ACK case sends no receipt and does not replay. |
| 14. Durable finalization/API | Rust projects Success/Cancelled to existing execution and workflow-attempt state; authenticated existing Execution API reads them. SQL assertions execute in the live fixture; retained metadata does not independently recreate SQL state. |
| 15. Cancellation/resources | Actual SDK response held while Rust cancels; correlated Stopped, original namespace/CLI drain, FIFO/gate/source retirement, then common resource fact and Cancelled projection. Source pin remains held before that fact and releases after commit. Closed Result, closed cancellation and open-session original-owner loss are proved. Recovery is denied while the original actor is alive; it preserves the same logical owner and never resumes the workflow. |
| 16. No rebuild on execution | Same unchanged accepted binary serves normal, alternate readiness, cancellation and lost-ACK executions. Execution containers have no compiler; producer build is separate. |
| 17. Non-Rust supervisor | Independent Go protocol probe executes actual adapter/workload using published common frames and synthetic decisions. This proves portable local execution, not production admission/durability. Peer codecs preserve six encoding directions. |
| 18. Warm edit loop | Actual edit changes missing-key order, produces a different edited artifact, executes it, then restores the original byte-identical workflow. Cold/warm measurements are retained. |

## Security and writer review

[Concrete schema/lock review](owner-current-review.md) covers composite owner,
execution, typed-attempt and session identities, immutable artifact/binding and
raw receipt/resource facts, permanent close/revocation, writer exclusion and the
common order: workspace-release advisory fence → attempt → NOWAIT owner/root/
deployment/Solution → session → Start → ordered admissions → grants/operations →
receipts → resource fact. Existing Python-owned positive cases remain required.

At `c739`, independent retained evidence verifies248 guard cases,126 baseline cases,
248 post-repeat cases, 13,898 backend unit tests, API quality and empty task
container/volume/network inventories. These are isolated authenticated principals
and real-schema checks, not production principal acceptance. First-party hosted
fixture PID/UID/filesystem trust is explicit; it is not a tenant isolation policy.
No production dispatch, credential expansion, deployment, merge or renewal is added.

## Crash and uncertain outcomes

Proved at `c739`: actual PostgreSQL COMMIT observed but its Result acknowledgement
withheld; Rust sees UncertainCommit, performs no Result/SDK/receipt replay, drains
original resources, reads the sole immutable receipt and preserves durable Success.

Also proved at `c739`: closed-Result original-owner SIGKILL, recovery denial while
that actor is alive, immutable receipt/source/owner readback and stop-only resource
closure without Result/SDK/receipt replay or owner reassignment. A lost release
COMMIT acknowledgement delivers no Provision/material/SDK and reaches Cancelled
only after original physical/source closure. The independent dormant guardian
probe proves birth before retained custody, original owner death, duplicate-create/
Start denial and original namespace/CLI/path cleanup; this is physical evidence.

Proved at `c739`: original-owner death after committed cancellation/Stopped and
before resource settlement. The closing actor uses the same closed SQL owner,
original custody and resource writer, with no receipt or protocol replay. Existing
authenticated API reads Cancelling before settlement and Cancelled afterward.

Also proved at `c739`: resource-settlement COMMIT observed by PostgreSQL with its
acknowledgment lost to the original Rust owner. The consumed retirement token is
not rebuilt. Read-only reconciliation denies a wrong digest and owner, reads the
single immutable exact fact, and preserves durable Cancelled/API/source settlement.
No settlement INSERT, physical cleanup, SDK or protocol frame is replayed.

[Retained failed recovery run](closed-recovery-fixture-failure.json) timed out at
proxy shutdown and did not verify teardown. The subsequent regression reproduced
Server.wait_closed waiting for idle clients before those transports were aborted.
The a103 fix closes owned transports/handlers first; both idle owner-disconnect
and live-owner stop regressions passed within the full13,898-unit gate. Earlier
failures remain retained. No timeout increase, skip or unchanged retry qualified it.

Proved at `c739`: original-owner loss with an open session after one admitted SDK
call. A living owner blocks recovery before intent or cancellation. After actual
SIGKILL/wait, API reads Running with no Result/resource fact and pinned source.
The fresh Rust actor proves original custody/death, commits the existing cancel,
and performs stop-only settlement with the same owner/session. API then reads
Cancelled with source unpinned; no SDK, Result or Cancel/Stopped frame is replayed.
The [earlier failed counter run](open-owner-loss-counter-failure.json) remains
retained. Its observed SDK admission was omitted from instrumentation; the fix
counts the actual admission without weakening any assertion or authority gate.

[Actual Docker reply-loss component](docker-create-loss-component-proof.json)
at `50ccb` verifies real daemon HTTP201, exactly one request, uncertainty and
fresh create/Start denial, original drain and empty container inventories. Its
whole current-source live/security lanes remain pending.

Still unproved: interrupted partial cleanup and human writer/runtime acceptance. Failures retain custody/source obligations;
no replay or owner reassignment is permitted. Missing evidence is not completion.

## Measurements and decision

At `c739`: cold compile 16.899s, independent cold 16.660s, edited warm compile 375.890ms,
restored 366.193ms, edit-to-artifact 572.389ms, module resolution 85.578ms.
Full original-owner fixture durations range 2.941–3.575s. The closed-Result recovery
actor separately takes 2.632s and excludes the original execution before SIGKILL.
These measurements include Docker, SQL, fixture pauses and physical/source
settlement; they are not bare runtime overhead. Native authoring-only startup/result samples are
separately retained; no fair current Python lifecycle benchmark or remote deploy
latency is claimed. [Developer assessment and comparison](decision.md).

The goal remains active. Complete it only after current-source security/race and
full runtime evidence are verified and accepted, then audit every row above.

## Security audit correction and remediation

The older Go1.27.1 scan emitted 199 findings, including 164 reachable traces,
but its descriptor incorrectly treated JSON-mode exit status as a clean scan.
The SDK HTTP path was affected. That old behavioral proof was not accepted.
The corrected parser passed 11 hosted regressions and rejected the actual old
stream before descriptor creation. Go1.27.2 is pinned by verified image digest.
The replacement binary has a new explicit identity; the unchanged replacement
serves all eight cases. Its retained scanner stream contains zero findings.
[Toolchain change and guarded rejection](toolchain-remediation.json) preserves
both the old scan denial and rejection by the old byte pin. The initially missed
API fixture pin failed before admission with verified teardown; its correction
passed the complete seven-case run and this fresh eight-case run. No waiver,
retry-until-green, timeout increase or credential expansion qualified them.
