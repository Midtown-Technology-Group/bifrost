# Mechanical writer exclusion: investigation and preflight

Status: independently reviewed source investigation and completed isolated-CI
identity collection. Observed test-runner superuser/table-owner authority does
not establish writer security. No schema, role, provisioning or authority-builder
release. C2/C3 and mixed-writer acceptance remain gated; this is not a global
Rust STOP.

Historical investigation source: platform reference
`701aaccd7956b1a3224fc561e4edc72368e88ace`, incorporating main
`01cadfe09710d293a40da14d6cf4056165289e31`. Workspace source at that investigation checkpoint was
`7fa1c2e045c103b5a118cf1d44c95e749eb184b6`; selected authored inputs and
public-boundary checks remain unchanged. Full A readiness and B
Agent → capacity workflow → answer → summary remain required.

## Findings that constrain the design

- `api/src/models/orm/executions.py` and `agent_runs.py` have no immutable
  lifecycle coordinator owner. Table grants alone cannot partition shared rows.
- `api/src/models/orm/work_deliveries.py` records encrypted envelope,
  queue/message identity and transport leases; it has no domain parent FK.
  `agent_runs.summary_delivery_id` is not a universal reverse parent binding.
- `api/src/models/orm/execution_attempts.py` uses logical type/id rather than a
  universal domain FK. Generic attempts also serve unrelated jobs. They must
  not be treated as workflow typed attempts or mechanically mapped by UUID.
- `docker-compose.test.yml` configures transaction pooling and shared
  DB_USER=bifrost. This is source configuration, not actual backend identity,
  generated pool mapping, grants, migration ownership or deployed evidence.
- `work_delivery_store.require_delivery_ownership` fences a transport lease and
  returns without checking when no context lease exists. It does not establish
  lifecycle ownership or language-specific writer exclusion.

A candidate adds immutable incumbent/core owners to existing Execution,
AgentRun and WorkDelivery rows through one additive Alembic migration. Existing
rows remain incumbent; admission assigns ownership from authenticated authority.
No request chooses ownership, no owner-changing conflict update is permitted,
and no post-Start transfer or duplicate domain schema is proposed. This is an
unapproved design direction; no column or migration exists in this package.

Delivery ownership would express independent transport custody. It cannot
certify decrypted domain identity. Every consumer, recovery, poison and summary
path must admit the actual domain owner under its existing lock before launch,
SDK/model calls, Redis mutation or event publication. SQL guards separately
protect domain and dependent writes. If database certification of ciphertext to
parent consistency is required, stop for an explicit binding design; do not
invent a message-id FK, SQL decryption or broad exemption.

## Security and parity conditions

Two application principals must be distinct, non-owner, non-superuser and
non-BYPASSRLS, without privileged membership or SET ROLE escape. Migration and
guard ownership remain separately managed. SECURITY DEFINER changes
current_user; session_user names the authenticated backend identity, which may
be a forced pooled user rather than the frontend coordinator. Headers and
user-writable GUCs cannot establish authority. Fixed search_path and effective
schema/function/table privileges require verification, not assumed role names.

Dependent guards must inspect OLD and NEW references and immutable identity.
Every nonnull AIUsage parent must be checked. Ancestor deletion and referential
SET NULL/CASCADE paths require explicit coverage: user/form/workflow, agent,
parent run, conversation, message and organization changes can affect protected
rows. A lookup after parent deletion may lose ownership evidence. Blanket
maintenance exemptions fail exclusion; denying legitimate deletion, summary,
verdict or cancellation fails parity.

Freeze finite summary fields/transitions and usage receipts, verdict audit,
metadata, log publication, retention/redaction and public cancel/rerun routing
before implementation. Python runtime must not retain lifecycle DML privileges.
An entire summarizer or SDK flush service is not a finite authority grant.

Preserve incumbent lock order: poison takes domain advisory lock → Execution
row → delivery row → typed attempt. Private issuance remains attempt-first with
secondary SHARE NOWAIT. NOWAIT on one edge does not prove the complete graph.
Include FK/trigger, source/session, ancestor and advisory locks in review.
No reversed blocking edge or automatic replay after possible Start is allowed.

SQL rollback does not undo earlier Redis deletion, publication, provider calls
or launch. `api/bifrost/_sync.py` deletes pending changes before the caller's
commit; this is a reference hazard to characterize, not a silently approved
repair. Observe failed flush/commit and Redis failure independently of DB rows,
including legitimate incurred-usage and terminal-race behavior.

## Stage 0: existing identity and ownership receipt

The investigation originally specified this bounded diagnostic before source
freeze and supported execution. It has since been implemented and collected;
the [retained-receipt checkpoint](#retained-receipt-collection-passed-writer-safety-not-established)
below records exact source/run/artifact evidence and unsafe observed authority.
The original scope and interpretation rules follow; they are not a claim that
collection is still pending.

Inspect existing connection paths only: pool/frontend/backend identities,
session_user/current_user across repeated transactions, role flags/membership,
relation/function/schema owners, effective privileges and RLS/FORCE RLS state.
Keep credentials and connection secrets out of receipts. Do not create two
proposed roles or a guard merely to make the diagnostic pass. Tests inside a
future guard body belong to Stage 1. A valid receipt may expose unsafe facts;
collection success is not security readiness.

A CI receipt proves only that exact candidate's isolated test environment.
It cannot establish deployed API/worker/scheduler settings or production pool
configuration. Record actual source, image, connection path and receipt scope.
If the pool collapses identity, freeze the infrastructure mapping decision
separately before permission builders; do not substitute SET ROLE.

## Stage 1 and acceptance remain gated

Before any additive migration or role/pool mutation, approve exact owned paths,
role provisioning owner, immutable admission, complete dependent/ancestor
coverage, finite ancillary operations and the lock graph. Alembic remains the
sole schema history. No production/schema-owner mutation follows from this doc.

Require real restricted-principal SQL negatives and matching positives through
the actual pool, including owner change, cross-owner writes, parent rebinding,
delivery conflict/claim/settle/retirement, generic type spoof, cascades,
SET ROLE/GUC/search_path/function abuse and failed-commit rollback. Follow with
actual consumers and Redis/runtime effect negatives, summary/public positives,
full A+B parity and mixed active-owner rollback on one database. Route rollback
changes new admissions; existing core rows drain under the same owner without
repair, guard disable, transfer or replay.

Stop the candidate if it requires broad schema duplication, guessed encrypted
parents, whole-service ancillary privilege, weakened fences, unrestricted pool
roles or unrelated writer redesign. Do not substitute queue/environment
partitioning for mixed-writer proof.

## Independent source-review provenance

Author investigation SHA256:
`eb603b2bb725ab62cf26435681d3392b7886e740538e340fb70b21fa2aaf6ba2`.
Different independent review SHA256:
`14dabec9942e80e136daf9e1d8fc4ba52994f6ba40743da02c7e89264d255dd7`.
Both reviewed exact reference701aaccd source. Verdict: accept source investigation
with conditions; do not freeze schema/guards or release builders. Root applies
the review's Stage 0 correction above: inspect existing identities first;
proposed roles and guard-body tests belong to a later authorized stage.

## Stage 0 source candidate and execution checkpoint

The exact one-test diagnostic is published on `test/agent-capacity-reference`
at `827f44e86540c3effe2e5645e8df89aae9cbecd8`, tree
`34069c220c9e13e10de444fc5b22eb20f2560ae2`. Owned scope is only
`api/tests/e2e/platform/test_writer_identity_preflight.py`:345 normally formatted
lines, SHA256 `fe157094fd0de96ec18e8267df22d46e7cce7773f84c81453dfc35943072e0b0`.
Different final source review accepts those exact bytes, SHA256
`9a14c17a97300ed7996c5539b910014563e186b9cb5617950abde1e3f2b7836c`.
AST, pinned Ruff0.15.12 check/format-check and whitespace checks pass.

The initial246-line candidate `c79ea32a` stopped when mandatory formatting
produced343lines. Root had incorrectly copied the prior reader test package's
250-line cap as a general repo limit. Fresh instruction/config inspection and
different independent review establish that it is package-specific. Root
separately corrected only this diagnostic footprint decision; formatted ASTs
are identical. The required schema-owner observation then adds two lines.
No helper relocation, compression, format skip, weakened observation or general
cap exception occurred. Both stopped drafts remain inert0600 outside Git; the
historical STOP is retained. Other package limits remain unchanged.
Scope limits must retain their source and package; formatting precedes counting.

The collector uses one direct and two independently connected existing pooled
snapshots, fixed catalog SELECTs, read-only repeatable-read transactions, native
type/row/byte admission and one shared30-second async deadline. Native asyncpg
CHAROID fields retain exact one-byte admission with closed ASCII representation
recorded in the receipt. Actual installed decoding remains a runtime check.
Schema/table/attached-trigger owners and observed privileges remain honest,
including unsafe values. Scope explicitly excludes complete role-admin graphs,
all definer helpers, actual API/worker/scheduler sessions and deployed posture.

Only a fixed sanitized catalog receipt is0644 for the existing artifact uploader;
all secret/custody modes remain unchanged. Exclusive same-FD bounded readback
and pathname identity checks do not establish immutable storage or authority.
Passing JUnit, complete receipt, actual candidate/image/plan provenance and
verified teardown must be inspected independently. Standard isolated test
setup/reset is effectful; only the diagnostic DB queries are read-only.

[Supported CI37015218198](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37015218198)
was dispatched once against exact827f44 with existing `pre_pr=true`. It is pending
at this checkpoint. No observed role/ownership receipt or runtime success is
claimed. No new workflow, conftest, role, permission, migration, public credential
or lifecycle behavior changed. Full A+B, host construction STOP, dependency
approval, #1017 STOP and C2/C3 gates remain independent.

### Completed execution and actual capture correction

CI37015218198 at827f44 is SUCCESS. Completed shard4 job110864605033 logs the
selected diagnostic PASSED with482 shard tests; unit job110864604959 reports
12,781 passed/3 unrelated skips/35 deselections. This is supported execution
evidence, not a retained complete privilege receipt or named JUnit readback.

Root inspected manual-prePR artifact11229254011: exact head/tree/main, exit0,
cleanup0 and actual EMPTY resources. Its actual comprehensive plan defers E2E,
so it has neither writer receipt nor JUnit. Ordinary successful E2E diagnostics
were also not uploaded. The initial capture design cannot meet acceptance on
this branch; passing CI is not substituted for missing evidence. Artifact ZIP
SHA256 `48467d2fc0134e1238200001bade019d0730f050cea05f202cf5c276609c21b3`.

After that run completed, root published `f3c008f2e33f07adb6b0070f47a88163ab7180c3`,
tree `76af702d2af28ed9943d275498b8a6c9342e9865`. Its sole delta is one11-line
existing-E2E upload step retaining the fixed sanitized receipt and existing
JUnit, with per-shard names and14-day retention. No test, input, permission,
job definition, gate, successful service-log capture or old failure diagnostics
changed. Different final source review SHA256
`7de0986a4e9ca7c70677fe8fc0774eaf071e8fcede867fc610b4b4078a81d055`
accepts workflow SHA256
`c18a0da1e5d789a5ec56bce32a02f3ead7efa85beb678a9b159d45556f4deffe`.
Exact inverse of the insertion restores prior workflow bytes;20 jobs remain.

[New-candidate CI37017334293](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37017334293)
was requested once; its completed evidence is recorded below. It verifies new evidence-retention behavior;
it is not a retry of unchanged827 or a green-result substitute. Artifacts with
only unrelated JUnit cannot satisfy collection. Require the actual passing
named testcase and complete safe receipt in the same shard/project artifact,
actual candidate/image/plan provenance, and verified cleanup. At that capture checkpoint no privilege values were accepted. Two-role
separation, deployed posture and writer safety remain unestablished.

### Retained receipt: collection passed, writer safety not established

CI37017334293 at exactf3c008 completed **FAILURE**. API E2E shard4
job110871702234 succeeded (482 passed/6 unrelated skips); all four API shards,
unit, lint/type and manual-prePR succeeded. Client E2E shard2/job110871702233
failed USER-01 at `client/e2e/users.bulk.spec.ts:160`: the expected two-selected
user move control was absent after10s (87 passed/1 failed). The client source
and test have no candidate diff against main01cadfe. Root cause is unknown;
[blocking repair #1034](https://github.com/Midtown-Technology-Group/bifrost/issues/1034)
is owned by the architect workstream. No rerun, timeout increase, skip or waiver
follows. This reference candidate cannot be treated as globally green.

Artifact11232653080 (`api-e2e-writer-identity-evidence-4`) retains the complete
receipt and exactly one passing, nonskipped
`tests.e2e.platform.test_writer_identity_preflight::test_collect_existing_writer_identity_receipt`
JUnit case (0.154s) under the same project directory. ZIP SHA256
`fdaad24a694ce29af1c583c3d21d156567f9edbf7b327f195373a54d60112167`;
receipt72797 bytes, SHA256
`d77b84624a2e76c8d1a6f490c82dceec067898f47a93bff014f3f42dbe0f7512`.
One direct and two sequential pooled samples contain all Q1–Q6, respectively
1/15/12/211/72/0 rows, within fixed limits. Each reports read-only repeatable
read, PostgreSQL160015, database bifrost_test, session_user=current_user=bifrost.
The two pooled client samples share backend PID498: new client connections do
not demonstrate distinct server identities. Direct backend PID3017 differs.

Actual observed bifrost role is superuser, BYPASSRLS, CREATEROLE/CREATEDB and
has effective membership/usage across the reported roles. It owns all12
selected tables; public schema owner is pg_database_owner. All selected table
RLS/FORCE RLS flags are false; table and column privileges are all true, with
schema USAGE/CREATE true. Attached-trigger query returns72 rows; policy query
returns none. **Collection succeeds; security_readiness remains not_established.**
This test-runner authority cannot prove mechanical separation between incumbent
and Rust writers. It identifies an isolated-lane prerequisite, not a whole
architecture falsification and not permission to provision roles or DDL.

Run/job/artifact association is external GitHub provenance, not embedded in the
receipt. API shard4 logs independently report the candidate-tag image pull
with digest `sha256:7580bdb0d02493ab761aea5ddb32ec5fe522ac284d15caabfeaf307b1dc19ab5`.
Manual-prePR artifact11232495268 separately binds exactf3/tree76af/main01,
exit0/cleanup0 and actual empty containers/volumes/networks. That EMPTY receipt
proves its own pre-PR job only; it does not certify another shard's teardown.
Its image ID is `sha256:d47d3993d624783ffbac6da68c76ee91dd06e485ee1842c3b4bb6022811fd181`
and registry digest matches the API-shard pull. No actual API/worker/scheduler
session, deployment identity, full SET/ADMIN graph, arbitrary callable function
inventory, mixed-writer isolation or Stage1 approval is implied.

Different retained-evidence review SHA256
`4daa54eed51849b8d3e980b06349c436324949f8ecb9b129ead314ab0a61e795`
accepts the complete receipt and named passing test, with the custody limits
above. It does not accept writer security or globally passing CI.
