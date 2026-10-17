# Unapproved migration candidates

These files are review inputs, not a second migration history. Alembic discovers
`alembic/versions` only; no runtime imports or startup hooks consume this folder.
Unit tests may compile candidate SQL offline without applying it. Move an exact
reviewed candidate into `versions` only after its schema/authority gate is met,
rechecking the current main head and installed catalog first. Do not stamp or
apply a candidate from this directory.

The owner-links draft is C1 storage only. The C2 starts/receipts draft adds typed
Start and exact report evidence, including the Start clock key required by C3.
C3 adds the closed CRED grant/operation records bound to retained owner, source,
session and Start identity. C4 adds exact provision/release and physical-resource
records. These drafts do not enable admission, finalization, renewal or replay.
This continuation does not yet include restricted login/guard grants,
ancestor-write exceptions or physical-drain admission. In particular, native
FK detachment is not physical/source retirement, and PUBLIC revocation does not
restrict a migration/table owner. It must not be installed as a complete writer
or retention policy. The whole approved common continuation and matching tests
are required before any runtime owner is enabled.

Native attempt deletion clears only `native_attempt_id` on the retained live-link
row. That NULL marker cannot be deleted or reattached, even if a native UUID and
its original fence are recreated. Historical session/fence fields stay intact.
The column-targeted `SET NULL` uses the repository's PostgreSQL 16 contract.
An empty-only downgrade requires read-committed isolation and migration-owner
table locks before checking emptiness; these are not runtime coordinator grants.

C2 preserves the prototype's immutable raw report bytes and 16 MiB storage
bound. These are sensitive payloads, not an approved retention policy. No real
payload ingestion is authorized by a storage draft; disposition and complete
writer/admission enforcement remain required before enabling a runtime owner.

C3 retains the original 35 grant fields and eight operation fields, adding only
owner incarnation, committed Start and Start message IDs. Its reference fixture
is pinned to CRED commit `3918d608a2d497d1ca8424fd4229d191323e2d7a`;
policy expectations must not be rewritten to match a candidate. Permanent native
grant FKs become immutable common-source/install/caller bindings. Attempt ordinal,
live source/current caller eligibility and session-close/first-issuance races
still require the complete C5 admission guard and existing CRED issuer adapter.
No C3-alone application or new credential authority is authorized. The accepted
Go consumer remains integration-get only; table enums do not expand its SDK.

C4 preserves the common prototype's 13 admission fields and seven resource
fields, exact composite grant/provision/session bindings, finite admission
budget and bounded raw observation digest. A provision/release row records an
execution-delivery decision. It is not a resource-cleanup receipt. A resource
row additionally requires a retained closed session, but the database does not
prove physical custody; only the original trusted observer may supply that fact
under the complete C5 writer/admission guard. Terminal status, process exit,
disconnect and missing files are insufficient. General partial-cleanup recovery
remains unresolved; storage cannot authorize replay, inferred retirement or
owner reassignment. Real payload retention, C1-C5 application and all runtime
enablement remain held.

C5-A drafts native wrong-writer enforcement, not a complete C5 install. Only
Execution gains a control-plane owner marker; retained common history keeps
descendant classification after native deletion. Existing child FKs, cascades
and unrelated Python associations are not replaced. Both the actual generic
attempt and declared logical tuple govern Rust-touching lifecycle events.
The native CLI link is `session_id`. Exact ancestor NULL transitions and the
existing public terminal-payload redaction are explicit incumbent exceptions;
the redaction service takes the execution advisory fence before its row locks.
Common close/issue/drain enforcement, remaining cascade/delete paths, finite
privileges/roles, installed catalog, actual race tests and payload disposition
remain required. Multi-parent agent ownership is not admitted by this workflow
draft and must be extended and qualified in R4. No fixture-wide read or table-lock
concession is authorized. Do not apply C5-A by itself.

The guard custodian performs plain reads and try-advisory locking; it receives
no row-lock UPDATE privilege. Lifecycle-event parent identity is read again
after acquiring its execution fences, rejecting delete/recreate changes. Guard
functions are explicitly VOLATILE and require READ COMMITTED so checks use
fresh snapshots; see [PostgreSQL's volatility contract](https://www.postgresql.org/docs/16/xfunc-volatility.html).
Runtime isolation/profile compatibility and the actual replacement race still
need supported PostgreSQL proof. Generic-attempt and lifecycle-event DML also
share the ordered `bifrost:execution-attempt-identity:<uuid>` try-advisory fence
for both OLD/NEW IDs, including non-workflow and missing parents. Execution
fences alone cannot protect those polymorphic FK targets. Include this edge
and implicit FK/row/index waits in the complete lock review. A replayed result receipt must never restore
native payloads cleared by the public redaction operation; native sanitation
and retained common evidence are separate contracts.

C5-B1 adds session/grant write authentication and the shared native execution
try-fence. A permanently closed session rejects first grant and operation
insertion. A deferred constraint reads final session/grant rows and rejects
COMMIT if a closed session retains an unrevoked grant. The existing Rust
cancellation transaction closes then revokes; both must commit together.
Closing with no grant remains valid and prevents later issuance. Already-revoked
grants need no new revocation. Failed/unknown commits still require exact state
reconciliation; no replay, physical-stop or source-release fact follows.
The read-only guard never revokes or issues on the caller's behalf. CRED's one
policy/issuer and renewal/ingress eligibility remain authoritative and still
need their common adapter; B1 is not complete SDK admission. A separate issuer
SQL principal, if needed, requires explicit review rather than granting the
Python runtime coordinator authority. See PostgreSQL's
[deferred constraint-trigger contract](https://www.postgresql.org/docs/16/sql-createtrigger.html).

Required actual PostgreSQL cases, not established by offline DDL tests:
close with no grant then late first issue; issue versus close in both lock
orders; close alone with a live grant must fail at COMMIT; close plus revoke in
either statement order must succeed; pre-existing explicit revoke; grant and
operation parent substitutions; close versus operation append; rollback and
unknown COMMIT readback; SET CONSTRAINTS IMMEDIATE; restricted incumbent and
untrusted SQL identities. Include implicit row/FK/index waits. B1 creates no
roles/grants and admits no runtime. Source-drain/remaining common writers,
installed privileges and complete source/physical/payload gates remain held.

C5-B2 authenticates all ten common stores, takes the existing shared workspace
release try-fence before the native execution try-fence, and retains every
C1-C4 identity guard. Its trigger sorts before B1. Incumbent writes are limited
to exact live-link deltas whose native-parent absence C1 independently checks;
incumbent insertion/deletion or lifecycle mutation is denied. New sessions
require native Pending/Running work. Start and provision/release insertion
requires an open attached session, matching live attempt/claim/worker/source,
native Running state and native execution phase; Start clock must match and
provision/release requires the exact unrevoked grant. It performs no network,
credential issuance, report projection, revocation or cleanup on behalf of a
caller. CRED policy and committed-read admission remain separate requirements.

This is still not source retirement enforcement. Retained source inventory
must begin at historical common owner deployment IDs even after native history
unlink. Terminal/no-session owners remain unresolved and pinned: current code
does not prove that all adapter processes/staging began after committed session
admission. Soft Solution uninstall is a status-only native operation and must
remain available; destructive DB/source removal requires a separate drained
obligation check and the same exclusive workspace-release fence. Missing Start,
session, native row, process or file cannot manufacture physical retirement.
Do not add a new retirement table or infer no-effect solely from those absences.
Actual SQL/lock/cascade/RLS/terminal-admission and mixed-writer proof, the CRED
adapter, original resource-custody observer, finite privileges and schema/profile
approval remain held before this continuation can be applied.

C5-B3 supplies the historical source collector and destructive-drain guards.
Common owner deployment IDs remain consumers when sessions are open, lack a
qualified physical observation, or were never assigned a session. Native
nonterminal work and incomplete typed/generic attempts also pin; both generic
workflow aliases are included. Current native links or active-install membership
do not decide whether a common pin exists. Python reuses its existing exact
deployment/provenance/dependency readback, failing closed on missing source.
The service recognizes the current-main state where neither common owner store
nor collector exists. A partial install, permission failure or collector error
cannot become an empty successful inventory. Installed catalog qualification
must separately authenticate the complete schema/functions/owners/privileges;
object presence alone does not qualify a runtime profile.

All Rust native writes now share the source fence with common writes. The
collector and destructive source removal take its exclusive try form. Root or
typed-attempt retirement requires the common owner fully drained; source UPDATE
retains Python's native status-only uninstall/activation behavior, while Rust
has no source-write exception. Native deployment immutability and all C1-C4
constraints remain enabled. Qualified resource observations still require the
original trusted observer; this draft does not establish that custody path.
No-session owners cannot be retired on a terminal flag or missing-row inference.
Their eventual release remains a concrete pre-session resource/proof decision.

Post-commit S3 sweep, per-install Python write locking, indirect dependency
retention, native UUID reuse, SQL maintenance, actual lock/cascade/error behavior
and source-admission races still need supported integration proof. These drafts
do not establish deletion compliance or permit schema/role/runtime activation.
