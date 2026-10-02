# Mechanical writer exclusion: investigation and preflight

Status: independently reviewed source investigation; no schema, role,
provisioning, runtime or authority-builder release. This is a prerequisite to
C2/C3, not proof of mixed-writer safety and not a global Rust STOP.

Source reviewed: platform reference
`701aaccd7956b1a3224fc561e4edc72368e88ace`, incorporating main
`01cadfe09710d293a40da14d6cf4056165289e31`. Fresh workspace main is
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

This is the next bounded diagnostic package, to be source-frozen and reviewed
before execution in a supported isolated CI or dedicated VM lane. No current
receipt or new test implementation is claimed.

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
