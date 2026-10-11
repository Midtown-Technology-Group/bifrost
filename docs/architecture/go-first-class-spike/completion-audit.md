# First-class Go acceptance audit

Decision: **CONTINUE SPIKE**. SDK `0.0.0-spike.2`. Updated 2026-10-11.
Thomas released the common interfaces at `b8b44cbc01b1fb6be86f4fba050e93eb0da55924`
for isolated implementation after independent codec acceptance. That release
remains effective; it does not accept the runtime, writer or profile.
[Exact authorization](isolated-implementation-authorization.json).

The latest fully inspected real-owner proof is source
`7f8af35231b7de952ee870badad688d68212354a`, producer run38120574615,
writer/security run38120574619. The later runtime candidate
`b105d7ab38e573bb8c2a28e01c9acc956c3a589a` passed its native producer and Rust foundation run38124022967, but live bootstrap
failed at locked API dependency installation before admission. Its writer lane
remains pending. A diagnostic successor must retain the actual build failure and
produce fresh source-bound live evidence. Earlier green results
do not qualify this newer candidate. [Exact retained proof](lost-result-commit-ack-proof.json).
[Dated historical audit](completion-audit-history.md) preserves prior source
pins and failures; its earlier missing-work statements are superseded here.

## Required vertical slice

| Requirement | Current evidence and limit |
| --- | --- |
| 1. Ordinary Go authoring | Implemented readiness handler uses packages, typed input/output, a consumer interface, loops, context and ordinary errors. `main` calls `bifrost.Workflow(Run)`. No DSL. |
| 2. Ordinary tests | Normal Go tests run in the isolated producer. The representative handler tests need no control plane. Current candidate producer checks remain pending. |
| 3. Isolated build | Read-only source/modules, separate scratch, network-disabled tenant tests/compile, resource limits, no runtime/vendor/DB/signing credentials. This bounded container recipe is proved; hostile multi-tenant production builder acceptance is open. |
| 4. Dependency/toolchain identity | Pinned Go1.27.1 Linux/amd64, CGO0, readonly modules, checksums/module graph and builder/recipe evidence. No dependency installation at execution. |
| 5. Static/security checks | Producer executes gofmt, vet, tests and pinned govulncheck. Existing backend quality and writer/race lanes remain required. No transferred status or scan waiver. |
| 6. Immutable artifact | Unchanged 9,920,235-byte Go binary SHA256 `160917deeb94275f31ca9ddee2dacae00fb079fdf54cb60e206c8def261f9c34`. Native archive separately binds adapter, schemas, graph and build evidence. Source identity and archive identity are distinct. |
| 7. Neutral registration | Explicit JSON manifest/schemas and immutable native descriptor. No source execution for metadata discovery; Rust reads the descriptor/association, not Go AST. General schema extraction and product registration UX remain limited. |
| 8. Accepted deployment pin | Real migrated native association and existing workflow/Solution deployment are checked in the isolated live fixture. Accepted archive bytes and owner binary consumed by that run are independently hashed. No production trust anchor claimed. |
| 9. Rust admission/Start | Real Rust transactions create the existing execution/typed attempt/owner/session and commit Start before material delivery. No Go-specific lifecycle table or fake durable store. |
| 10. Published protocol only | Independent Go/Python/Rust codecs and session vectors cover the proposed common profile. Go adapter uses that profile; workload sees its typed capability context. Human profile/runtime acceptance remains open. |
| 11. No Rust linkage | Workload/SDK have no Rust crates, structs, FFI, DB, Redis or private scheduling knowledge. The independent Rust owner/codec is an implementation of common contracts. |
| 12. Restricted SDK | Real synthetic TLS integration-get uses the finite dedicated attempt/session-bound credential, exact organization/Solution/name, no redirect/renewal, and ordinary-route rejection. Issuer and ingress are first-party fixtures, not a live vendor. |
| 13. Logs/Result/Receipt | Actual adapter LogBatch/Result, raw-payload SHA256 and committed receipt correlate with Start. Normal ResultReceipt follows observed commit; lost Result ACK case sends no receipt and does not replay. |
| 14. Durable finalization/API | Rust projects Success/Cancelled to existing execution and workflow-attempt state; authenticated existing Execution API reads them. SQL assertions execute in the live fixture; retained metadata does not independently recreate SQL state. |
| 15. Cancellation/resources | Actual SDK response held while Rust cancels; correlated Stopped, original namespace/CLI drain, FIFO/gate/source retirement, then common resource fact and Cancelled projection. Source pin remains held before that fact and releases after commit. Crash/uncertain coverage of the newer candidate is still pending. |
| 16. No rebuild on execution | Same unchanged accepted binary serves normal, alternate readiness, cancellation and lost-ACK executions. Execution containers have no compiler; producer build is separate. |
| 17. Non-Rust supervisor | Independent Go protocol probe executes actual adapter/workload using published common frames and synthetic decisions. This proves portable local execution, not production admission/durability. Peer codecs preserve six encoding directions. |
| 18. Warm edit loop | Actual edit changes missing-key order, produces a different edited artifact, executes it, then restores the original byte-identical workflow. Cold/warm measurements are retained. |

## Security and writer review

[Concrete schema/lock review](owner-schema-review.md) covers composite owner,
execution, typed-attempt and session identities, immutable artifact/binding and
raw receipt/resource facts, permanent close/revocation, writer exclusion and the
common order: workspace-release advisory fence → attempt → NOWAIT owner/root/
deployment/Solution → session → Start → ordered admissions → grants/operations →
receipts → resource fact. Existing Python-owned positive cases remain required.

At7f, independent retained evidence verifies248 guard cases,126 baseline cases,
248 post-repeat cases,13,896 backend unit tests, API quality and empty task
container/volume/network inventories. These are isolated authenticated principals
and real-schema checks, not production principal acceptance. First-party hosted
fixture PID/UID/filesystem trust is explicit; it is not a tenant isolation policy.
No production dispatch, credential expansion, deployment, merge or renewal is added.

## Crash and uncertain outcomes

Proved at7f: actual PostgreSQL COMMIT observed but its Result acknowledgement
withheld; Rust sees UncertainCommit, performs no Result/SDK/receipt replay, drains
original resources, reads the sole immutable receipt and preserves durable Success.

Pending atb105: closed-Result original owner SIGKILL, live-owner recovery denial,
read-only exact original SQL/custody recovery, no ownership reassignment or receipt
replay; uncertain release abort without Provision/material/SDK delivery; dormant
process birth before retained channel custody with stop-only drain.

[Retained failed recovery run](closed-recovery-fixture-failure.json) timed out at
proxy shutdown and did not verify teardown. The current source fixes client-EOF
upstream leakage and adds idle owner-disconnect/live-owner shutdown regressions.
The dormant birth probe passed once but then failed; the current source retains
stop-only kernel custody before inducing owner EOF. No unchanged retries, timeout
increases or weakened drain checks qualify either fix.

Still unproved: Docker transport response-loss injection, durable pre-Result owner
recovery, interrupted partial cleanup, ambiguous resource-fact commit reconciliation
and human writer/runtime acceptance. Failures retain custody/source obligations;
no replay or owner reassignment is permitted. Missing evidence is not completion.

## Measurements and decision

At7f: cold compile16.248s, independent cold16.136s, edited warm compile368.325ms,
restored337.293ms, edit-to-artifact560.046ms, module resolution106.816ms.
Four full Rust-owner fixture durations are3.780s,2.880s,3.014s and2.973s; these
include Docker launch, SQL, fixture pauses and physical/source settlement, so they
are not bare runtime overhead. Native authoring-only startup/result samples are
separately retained; no fair current Python lifecycle benchmark or remote deploy
latency is claimed. [Developer assessment and comparison](decision.md).

The goal remains active. Complete it only after current-source security/race and
full runtime evidence are verified and accepted, then audit every row above.
