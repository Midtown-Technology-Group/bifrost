# Rust MVP: Running/Cancel SQL evidence

The bounded Running/Cancel SQL projection passed supported verification. This is one prerequisite for the workflow/agent MVP, not a full MVP acceptance or authorization to merge or deploy.

## Tested source and retained evidence

- Platform candidate: `f9f2e50f6eaf3906d0b9c0a506db10287d102bc3`.
- Candidate tree: `7b877268bc27cc0497fb1c92d801ec7f54828678`.
- Included platform main: `473c68181ddad7f9f59ba63bc08cae1b4db35b2f`.
- Workspace source-boundary checkpoint: `8be6da9e0d92d9110da5141f21e9c689aa8b138e`; no workspace accommodation was introduced.
- [Supported VERIFY run 37112190135](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37112190135), job `111172102138`: success.
- Artifact `11270965356`, SHA-256 `6528e481a33fc2df10d2e2253961811f62ba809cfcd960677817bfc7a51411d9`.

The [retained archive](evidence/rust-running-cancel-37112190135.zip) preserves the exact uploaded safe evidence after GitHub's retention period. It contains 48 members: 39 synthetic business observations, one expiry-control observation, manifest, source heads, producer receipt, state, completion, projected JUnit, exit status and fallback cleanup receipt. It contains no raw child diagnostics, raw JUnit, full SQL rows, device secrets, claim tokens or customer payloads. Its digest matches GitHub's artifact digest.

The tested source is the candidate above. This documentation and evidence archive were added afterward; they are not part of the tested candidate. Exact source hashes in the receipt, rather than a later documentation commit's SHA, identify the executed implementation.

## Accepted scope

| Gate | Actual evidence |
|---|---|
| Business roster | 39 cases: 38 paired and one explicit Rust-only rollback case |
| Native calls | 44 Python and 45 Rust calls, each actually invoked and completed |
| JUnit | 40 known identities, once each, passed; no failures, errors, skips or unknown identities |
| Native checks | Required formatting, all-target/all-feature Clippy, default tests, eight explicit feature-library units and five example units |
| Source custody | All 19 receipt source hashes match Git blobs at the tested candidate |
| Runtime custody | Driver binary and API image identities match before and after execution |
| Database admission | Actual installed Alembic head matches the source-expected head in each cohort |
| Expiry control | Genuine 75-second work expiry; both cohorts dispose rows and Redis within the original 15-second reserve |
| Resources | Named owned containers, volumes and images removed; fresh custom/project inventories empty |

The actual cases exercise accepted and rejected Running transitions, missing rows, wrong and foreign fences, process-ID bounds, repeated Running, queued and running cancellation, terminal rejection, ordered/racing transitions, rollback and private Keep comparisons. The 256-character process case produces the same database failure and rollback in both lanes.

Fresh PostgreSQL graphs establish A queued behind the held lock, inspection C behind A, and B behind A and C. Post-A observations retain B behind C around the actual committed checkpoint. The deliberate operation-sample swap is detected. Separate clock witnesses confirm Running samples time after the attempt lock, while queued cancellation samples time before that lock.

The expiry control has zero business invocations. It reaches the never-completing Event only after genuine legal seeds, subscription acknowledgements and readbacks. Its actual timeout object is preserved through concurrent cohort disposal, fresh row-absence checks and Redis closure. The safe receipt records overlapping cleanup within the original case deadline.

Fourteen genuine Python execution-update receipts include subscription acknowledgement, event receipt and committed readback. The Rust publisher is explicitly absent. Every business observation records `event_parity: held` and `gate_scope: sql_projection`; this evidence does not establish Rust event parity.

## Review and limits

The producer and conductor were reviewed by different authors before execution. Independent artifact review rechecked every observation digest, actual actor count, JUnit identity, source hash, causal/time witness, expiry record and cleanup receipt. The accountable architect independently checked the same source/count/custody boundaries and accepted this bounded evidence.

Safe observations are produced by reviewed code in trusted CI. They are not hostile-process attestation. Full private SQL rows and raw command output are intentionally omitted, so complete row comparisons rely on the reviewed producer's assertions. The archive does not permit independent reconstruction of those private rows or memory identity.

Still required for the original MVP:

- Rust committed event publication and failure behavior.
- Restricted principals, mechanical writer exclusion and same-row mixed Python/Rust operation.
- Full Result behavior, database numeric characterization, NULL versus JSON-null coverage and ambiguous COMMIT behavior.
- Runtime/source admission, installed closure, process custody, provider credentials and genuine agent/tool/answer/summary behavior.
- Reversible routing and rollback without data repair.
- Fair resource, performance and implementation-cost evidence.

Sopdet remains an independent blocked compatibility gate. This workflow/agent experiment neither resolves it nor claims Go-client compatibility.

The run used the original caps without retries, timeout growth or expectation waivers. Its elapsed verification time is not a comparative performance benchmark. Architecture RFC #1005 and MVP stewardship #1011 remain separate review surfaces. No PR merge or production deployment is implied.
