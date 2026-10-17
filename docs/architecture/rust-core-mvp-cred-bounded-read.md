# CRED bounded-read interface

Package `CP-C5-D/query/v1`, 2026-10-17. This source package removes incidental
ORM reads from the existing trusted CRED implementation. It does not select or
install a credential-writer SQL principal, adapt common bindings, or prove Rust
lifecycle ownership.

## Scope and dependencies

Base is CRED-P1 PR #1037 at `6e56a72affdb828f3249e320f704c666ce775298`,
including platform main `e42999395231ebc536711e5015422edfb75613a7`.
Workspace main advanced to `3df127b2e6ed9251a1e6752bfe7c020a3cce5232`; the
[committed-blob audit](rust-core-mvp-workspace-boundary-3df127b2.json) finds
2,459 authored Python files, no forbidden imports or parse errors, and an empty
platform-internal-import allowlist. Both prerequisite merges remain ancestors.

Owned source paths are only:

- `api/src/services/runtime_sdk_grants.py`: bounded projections in `_read_work`
  and `_validate_ingress_snapshot`.
- `api/tests/unit/services/test_runtime_sdk_query_scope.py`: actual-query
  regression checks using the existing real native CRED fixture.

No route, SDK, workflow, policy, lock clause, migration, role, grant, credential
format or common schema is owned by this package. R1-CANCEL-SQL/v1 remains held.
The original issuer, ingress, renewal, operation and caller authority rules remain
unchanged. SQLx/Rust ownership is a later integration consumer, not implemented
by these Python query changes.

## Interface decision

The old ORM selections acquired full attempt/execution/source rows; loading a
Solution also selected unrelated connection schemas and file locations. Ingress
loaded whole User rows, including password/MFA/avatar metadata, despite needing
only current authority flags and organization identity. A least-privilege issuer
cannot justify these incidental reads.

Use explicit `load_only(..., raiseload=True)` and relationship `raiseload("*")`.
Explicitly retain the immutable deployment dependency-edge query and its three
validation fields plus composite key. `_pin_from_deployment`, closure/hash/source
checks, original/effective caller rules and Start/attempt fences stay unchanged.
Unknown deferred fields fail closed through the existing SQLAlchemy-error path;
no fallback reader or broader session factory is allowed.

The test's `READ_COLUMNS` enumerates the union of query projection, predicate
and ordering columns. It uses physical `solutions.global_repo_access` for the
ORM attribute `allow_outbound_access`. Required reads comprise native attempt,
execution, deployment, dependency edge, selected Solution authority, current user
flags, current organization flags and bounded role membership. Existing grant
and operation reads remain their own CRED authority surfaces. This enumeration
is not a grant script or permission to confer locking/writing authority.

Ingress performs an explicit bounded attempt lease query: ORM instances may
leave the weak identity map after `_load` returns. Scope must not depend on
incidental cache retention. SERIALIZABLE read-only ingress and the sampled-clock
ordering remain unchanged. Issuance retains its independent committed read,
attempt lock, later SHARE NOWAIT parent locks and private commit boundary.

## Source and verification

Test-only source: `e30f95841583dc3b1b0820621516991820d2a592` (including its
physical-column correction). Corrected source:
`4d55403a29f35d522d0d51c7cd27869428d451a9`.
Implementation SHA256:
`c30b76d44169dbd4dfb513fff5bc6cbe24c1fff7975fcd797006867e79a59e8e`.
Observer SHA256:
`95f4e5e959e41b9a01004067978cdabbe048298419adccba98cdba45c7e50087`.
Only the two named service function ASTs change; all other service definitions
remain identical to6e56. Locked Ruff and AST parsing pass on the PVE host;
no application imports or product execution occur there.

Independent Sol static source review accepts the retained pinning, dependency,
identity, lease and lock behavior. It identified the test's physical-column
mismatch before native execution; the test was corrected without changing the
production field or policy.

Supported host: pve-t340, VM101 `bifrost-platform-test-debian13-01`.
Checkout `/home/dev/bifrost-cred-bounded-read-e30f95841`, native Compose project
`bifrost-test-d8b84b96`. Baseline records 143 preexisting stopped containers and
2,567 volumes. Only this project's resources may be removed. Source and private
receipts are retained under `/home/dev/cred-bounded-read-evidence` and host
`/home/thomas/src/bifrost-rust-mvp-artifacts/cred-bounded-read-4d55403a2`.

The uncorrected native run exits1 with all five query-boundary positives failing,
zero errors/skips. It covers create, initial token issue, authority load, ingress
and renewal. Log SHA256
`522f3196b36f70a944b45716f76002926a3cfd7c118742742c08fc112b27ac0a`;
JUnit `d97f8fc2cbc81df46d73a82d98d9c01bf34d4838da625007a51c3444aafda321`.
The corrected native selection exits0: **307 unique cases pass**, zero failures,
errors or skips, on exact4d55403a2. This includes all five query checks, existing
codec/grant/ingress/refresh/legacy and engine/bypass cases, and all 30 selected
HTTP/SDK/OAuth cases. Log SHA256
`79356446034f5bed925d5166803e8cb7b8b60498bdbb7390a606c23a3a42b1cb`;
JUnit `dfda4f6d6c8f5b9b869e8eba538446a1757b2efaa5758e61029d1a24daad5446`.
The XML contains 307 distinct test identities, not overlapping aggregate counts.

Literal `./test.sh pre-pr` on clean4d exits0. Log SHA256
`7e3d6caffaa8ee7a2d8996d5fbf38f2e857f33136ddfeea717c6650a45cd8a2a`.
API type/lint and generated-contract checks pass. The trusted planner defers
comprehensive suites to normal CI; no full-candidate CI pass transfers from6e56.
The change is now published on existing PR #1037 at exact4d. Normal CI
`39001167280`, CodeQL `39001167277` and queue authorization `39001167352` are
pending/queued at publication; no old-head green result qualifies this head.

## Real frontend/API qualification

The supported debug build initially exhausted available disk during image export.
The owner interrupted only this checkout's identified buildx client; debug exited1
with `Canceled: context canceled`, before API startup. This is a preserved
capacity-interrupted build, not a source assertion failure or an unexplained
retry. Original log SHA256
`8db7a5985038abb4450352fef449e24e0e2017cd547ac47734c2159bf9cfabff`.
After completed native tests, supported native-stack teardown removed named
resources; eight captured anonymous attached volumes required explicit removal.
Each was outside the 2,567-volume baseline and had no remaining container user.

Six obsolete debug image tags from the preceding owned6e56 checkout were
matched to their original producer manifest/tag log, excluded from all 143
baseline container references, and removed with `docker image rm --no-prune`.
No force, global prune or shared/pinned image removal occurred. Private removal
receipt SHA256
`5413ee8c470d7f47483ce076580d6dfbab116e3cc8656e2fd9652f8346a0f4ab`.
This recovered 7,730,728,960 available bytes. One unchanged-source bounded debug
recovery then passed with a 256-MiB floor monitor; observed minimum free space
was 508,694,528 bytes, with no guard interruption. Recovery receipt SHA256
`95add8f808d10928538617980bdc853d59c8e42ce500f2c9ba0a18ed07bb0fef`;
log `11ffcf63ee8e72d1e800b1f6b9e9ff2e1796796ba328763ba5835db0bf64fc2f`.
No additional build retry or configuration/expectation change was used.

Debug project `bifrost-debug-d8b84b96` printed `http://localhost:38273`.
Guest-host GET at that exact frontend returned200. Inside the actual API image,
manual `httpx` requests traversed the `client` alias of that same inspected
frontend container, with redirects disabled and a new cookie jar per request.
The service file matched implementation SHA c30b76d4 above. All eight cases pass:

| Case | Observed HTTP |
| --- | --- |
| Granted integration read | 200, JSON null |
| Granted mapping read | 200, JSON null |
| Wrong integration name | 401, Bearer challenge |
| Wrong organization | 401, Bearer challenge |
| Refresh-token renewal | 200 |
| Renewed integration read | 200, JSON null |
| Original access token after explicit revocation | 401, Bearer challenge |
| Renewed access token after explicit revocation | 401, Bearer challenge |

Real ingress confirms renewal retains the same grant, subject, audience, purpose,
digest and closed policy; the database retains one grant for the attempt. The
probe provisions synthetic native data with the real issuer. It does not launch
a workflow process, commit a Rust Start, contact a vendor or install common roles.
These are direct HTTP cases; unchanged SDK-client behavior is covered by the
separate selected 30 automated HTTP/SDK/OAuth cases, not by this manual probe.

The first launcher failed before requests on short/full Docker-ID comparison;
the next bootstrap failed before fixture creation because runtime images omit
the tests package. Retain both setup failures; no failed policy expectation was
changed. The corrected probe embeds only the two classes and two async fixture
functions from pinned native fixture SHA256
`62f3fbabc7308c049af3304f00e639dacb6d0083ceee390135ed724bd5326ec1`,
removing pytest decorators/imports only. Extracted bytes SHA256
`00dade79d19ca4f041ce4ffd6c10b517a364b1ecf5f6adcfe00b385d4ae7b8b1`;
independent AST review confirms unchanged definition bodies. No application guard,
issuer, database factory or HTTP handler is mocked. Independent review required
normal generator teardown, unconditional DB closure and a sanitized outer failure
boundary; these were repaired before execution. Probe SHA256
`ada1fc56ffa65c77764024399563aa078c53539f039403297b12ce639d8f1776`.
Its process explicitly sets `BIFROST_DEBUG=false` before imports to prevent SQL
echo of private parameters; server/source policy is unchanged. Successful probe
stderr is empty. Sanitized live receipt SHA256
`e3caaf51cf3933cada555ab005559ba72c618ae1ecf9b1fd00586f97b73876b1`.
Private failed/successful outputs remain in the evidence directory, never Git.

## Resource disposition

Supported `./debug.sh down` exited0; captured remaining anonymous volumes were
removed only after no-reference/baseline checks. A verification assertion first
compared 12-character baseline container IDs to full IDs; corrected unique-prefix
readback proves all 143 baseline containers remain, and all 2,567 baseline volumes
remain. Zero owned project containers, networks or volumes remain. Cleanup
receipt SHA256
`ad4ba9518fa3f81cd8129fc65d06e80a6b894c75b99270c1af6a62d913d6723f`.
The six current owned debug image tags were then matched to their exact recovery
producer and retired without pruning; raw evidence/source remain. This restored
9,678,233,600 available bytes. Receipt SHA256
`92405c70f3a14cbd07f15fb13be2398b99e2377eab3b0bdb7041a9e8426ec949`.
Pinned Python61a673be/Rust0dc3dbe0 images and the shared Go base cache are retained.
A successful fresh query found no running containers or named product/build
processes; source4d remained clean. VM101 was gracefully shut down and read back
stopped after evidence collection.

Engineering flow and PR stewardship procedures are version2026-10-06.1;
skill router2026-10-01.1, skill package2026-10-09.1-b3560edd5d13f1a7.
Sol/Codex authored these changes; no human DCO/sign-off is asserted. Existing
repository contribution authorization/provenance and all review/CI gates remain.


## Acceptance and stop conditions

Run the five positive query checks against real SQL, then the existing credential,
ingress, renewal/refresh, engine/bypass, legacy-boundary and selected HTTP/SDK/OAuth
suites. Every policy/fence/caller/dependency check must retain its previous
meaning. Supported API quality and final pre-PR remain required before any PR.
New installed privilege enforcement needs its own separately approved profile
and authenticated real-login evidence; test interception cannot substitute.

Stop/report if preserving behavior requires unrelated sensitive data, dropping
dependency closure, broadening a session factory, new schema/role privileges,
changing caller/SDK authorization, or reviving a stopped campaign. Retain original
failures; no expectation, skip, retry, scope or credential expansion to pass.

The current fixture has no populated dependency edges. It proves that the
relationship query remains present but does not independently qualify a
populated dependency closure under restricted SQL principals. The regex SQL
observer checks emitted ORM shapes, not arbitrary SQL or actual PostgreSQL ACLs.
Neither this package nor successful native CRED mechanism tests complete the
common issuer, physical custody, Rust Start/result, mixed-writer, rollback,
workflow, agent or final CONTINUE gates.
