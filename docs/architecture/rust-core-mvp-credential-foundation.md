# CRED-P1-S: private runtime credential foundation

Status: architect-approved bounded build packet, not credential/runtime acceptance.
The user approved the [narrow credential prerequisite](rust-core-mvp-runtime-credentials.md).
This packet implements its private storage/token foundation only. No merge,
deployment, vendor use, runtime extraction or C2/C3 acceptance is authorized.
Initial freeze procedure: mtg-engineering-flow 2026-09-30.1, MTG package 2026-10-01.2.
Current reconciliation uses MTG package 2026-10-01.3.

## Source and ownership

The initial freeze used f10da7c (#1007). Main then advanced through #1021,
adding nullable immutable declaration identity and a new Alembic parent. The
six-file delta changes no auth/SDK/attempt/deployment helper. Reconciled migration
ownership uses the new parent; workspace-release grants remain excluded.

The initial source implementation used platform main
`fcfbedfe189e8efab0546890828806c91cec358d`. Current authoritative main is
`658283e8c8707ceecf9e2c0b56b779d074b7bdaf` after #1023, included in the
`fix/runtime-sdk-purpose` reconciliation. #1023 adds Solution source-accounting
completion evidence and a shared runtime-admission fence before pin persistence.
It changes no selected SDK/auth/minter/deployment-pin helper. Preserve that
admission fence in future C3 coordination; it is not implemented by this private
grant packet. Its new Alembic head is the parent of our unmerged forward revision;
do not create sibling histories. Reverification at the reconciled candidate is
required; previous source/CI pins remain historical evidence.

Current accounting inventories accepted `Execution` rows and general
`execution_attempts` whose logical type is workflow. It does not inventory the
dedicated workflow attempts or new private grant/supervisor sessions. Accounting
completion therefore cannot establish runtime/source drain for C3. Preserve
genuine unfinished source consumers until trusted stop/close evidence, and
acquire the shared admission fence before install/row locks. This is a required
ownership/custody integration decision, not a new grant-foundation behavior.

The original-caller prerequisite remains unmerged
[AUTH-P1 #1018](https://github.com/Midtown-Technology-Group/bifrost/pull/1018),
candidate `d82219f5aada66d879f2da71b386f50675c66d4d`. Actual agent/SDK integration
must name that dependency; this packet does not change agent admission or infer
missing caller authority. Workspace main is
`e8605dc8edb6df8a997c171b534b324ac7ebd8ec`; authored source/SDK stays unchanged.

Owned paths: new `api/src/core/runtime_sdk_credentials.py`,
`api/src/models/orm/runtime_sdk_grants.py`,
`api/src/services/runtime_sdk_grants.py`, minimal ORM registration, one forward
Alembic migration, and dedicated new unit/service persistence tests. Existing
routers, global auth/security decoders, worker/pool, Rust, SDK and workspace are
not owned. HTTP integration/renewal is a subsequent reviewed packet; there is
no public mint/revoke endpoint or automatic runtime provisioning here.

## Durable model

Create exactly two tables in the authoritative Alembic history, currently headed
by `20261001_solution_src_account` after #1023. Recheck the parent before creating the
migration. Do not create a source-content/lookup table or another attempt model.

`workflow_runtime_sdk_grants` has these immutable fields, in this digest order:
`id`, `schema_version`, `workflow_attempt_id`, `execution_id`, `attempt_number`,
`claim_token_digest`, `worker_incarnation_id`, `supervisor_incarnation_id`,
`runtime_session_id`, `started_at`, `issued_at`, `timeout_seconds`,
`credential_deadline`, `initial_access_expires_at`, `caller_user_id`,
`caller_organization_id`, `effective_organization_id`, `caller_email`,
`caller_name`, `caller_admin`, `caller_provider`, `caller_external`,
`caller_snapshot_digest`, `workflow_id`, `solution_install_id`, `source_kind`,
`source_id`, `source_manifest_digest`, `source_resolution_digest`,
`source_global_permission`, `source_digest`, `operations_digest`. Its derived
`grant_digest` is not included in its own digest. Only `revoked_at` and
`revocation_reason` are mutable; reasons are `session_closed`,
`supervisor_replaced`, or `explicit_revoke`, with both fields absent or present.

Use native UUIDs, timezone-aware timestamps and bounded integer/string columns.
All fields are nonnull except the two organization snapshots, finite-only
credential deadline, and revocation pair. `schema_version` is `cred-p1/v1`;
`source_kind` is only `solution-deployment`. Unique attempt and session bindings
prevent replacement grants for the same attempt. The attempt and actual
SolutionDeployment references cascade credential deletion on parent deletion;
do not add caller/install deletion cascades or new blockers to existing deletion
behavior. Existing protected-deployment guards remain authoritative.

`workflow_runtime_sdk_grant_operations` stores `grant_id`, `ordinal`, `operation`,
`integration_name`, `scope_kind`, nullable `scope_organization_id`, nullable
`resolved_organization_id`, nullable `solution_install_id`. Primary key is
`(grant_id, ordinal)`, with ordinal 0..1 and unique `(grant_id, operation)`;
the database therefore permits at most two operations. Operation is
`integration-get` or `mapping-get`; scope kind is `default`, `global`, or
`organization`. Only organization scope carries its UUID; global resolves NULL.
Mapping-get forbids a Solution selector. OAuth override/entity selector are
fixed absent/null, without wildcard permissions. Insert the main row and its
closed operations atomically; expose credentials only after commit.

The genuine deployment/manifest/resolution identity is validated using existing
`validate_runtime_closure`. Preserve its existing `sha256:<64 lowercase hex>`
manifest/resolution digests in 71-character fields. Other private digests are
bare 64-character lowercase SHA256. Do not manufacture a workspace-release UUID.
The source digest includes an **empty** authorized source-lookup sequence: this
packet grants no module/source HTTP access. Source extraction remains blocked.

## Private codec and token

The private digest codec is fixed-order length-framed UTF-8: each field is
ASCII byte length, `:`, value bytes, `,`; nested records are length-framed too.
Use explicit presence 0/1 then value for optionals; UUIDs are lowercase canonical
strings, integers canonical unsigned decimal, timestamps integer UTC epoch
microseconds. Domains are `cred-p1/{claim,caller,source,operations,grant}/v1`.
No arbitrary JSON, float, model serialization or runtime context bag is accepted.
Caller includes its original identity/flags plus sorted unique role names;
operations sort by operation/name/scope kind/scope UUID. Source preserves the
accepted deployment/install/manifest/resolution/global-permission identity.
Recompute the immutable row/operation digests at lookup and compare the signed
grant digest; do not rely on the stored digest alone.

Private input records are frozen/closed typed values: committed workflow Start,
authorized original-caller snapshot with separate effective org, selected closed
SDK policy, and accepted manifest identity. Reject extras and invalid types.
Byte bounds: email 320, name/integration/role/issuer 255, at most 256 roles;
timestamps 0..253402300799999999 microseconds, attempt number >=1, timeout >=0.
Reject NUL, lone surrogates and truncation. Boolean inputs are strict booleans,
stored as 0/1. Freeze deterministic synthetic codec vectors in the tests.

JWT payload keys are exactly `iss`, `aud`, `sub`, `type`, `purpose`,
`grant_digest`, `iat`, `exp`, `jti`. Audience is the single string
`bifrost-workflow-runtime-sdk`, purpose `workflow-runtime-sdk/v1`, type `access`,
sub the grant UUID and jti a fresh UUID. Use existing configured algorithm,
issuer and trusted signing key; refuse application-audience collision.
Strict integer times exclude booleans/floats/strings. Maximum token size is
4096 bytes. No engine/admin/context claims or raw fence appear in the token.
Global legacy decoders remain application-only and reject this audience.

## Issuance, lookup and renewal

Trusted private creation independently loads the committed workflow attempt,
execution and actual deployment, comparing identity, number, claim digest,
worker incarnation, Start, workflow, effective org and source pins. Initial
creation requires running/execution phase, nonnull Start and no completion;
a merely flushed/claimed attempt is insufficient. Repeat identical creation is
idempotent; changed authority/session is rejected. No delivery lease substitutes
for a workflow attempt. Original caller inputs come from trusted authorization,
never headers/model arguments or effective-org inference.

Initial finite expiry preserves the trusted parent's **predeclared** credential
deadline, not post-Start issuance plus a fresh lifetime. No-timeout initial expiry
also preserves its predeclared bound. Reject expired initial issuance; floor
microseconds to JWT seconds. Finite-timeout grants cannot renew; their JWT
expiry cannot exceed `credential_deadline`. Timeout-zero grants explicitly have
`credential_deadline = NULL`: private renewal issues only a bounded now+600-second
access window, retaining the same audience/purpose/grant/operations/session.
Each renewal freshly validates the committed active attempt, exact stored fence
and unrevoked grant. There is no renewable hard deadline in this private profile.
It cannot use ordinary-user/legacy-engine refresh fallback or expand authority.
Adding a hard lifetime for timeout-zero executions is a separate authorization
policy decision, not an implicit interpretation of this nullable field.

Lookup retains existing active-fence semantics: completed_at NULL and claimed/
running, plus exact stored identity/Start/worker and unrevoked grant. No new
heartbeat expiry or CANCELLING policy. Store failure denies; no cache fallback.
Revocation is trusted-only, row-locked/idempotent and never changes lifecycle.
Fresh validations after committed revocation deny; already admitted SDK effects
may finish, as in the current request-bound model. Do not hold locks over vendor
calls. Actual supervisor close/restart hooks, original-org provenance, trusted
deadline capture and lifecycle ownership remain real integration gates.

The private issuer uses the existing trusted platform session factory and an
independent transaction, rather than a caller-owned session that could see its
own uncommitted Start. It first checks committed eligibility, then locks and
refreshes the relevant rows. While holding the locks, it rechecks every
eligibility and fencing predicate against the refreshed state before atomic
insertion. Do not wait for an uncommitted claimed-to-running write and call it
preexisting committed Start.

The attempt is the first blocking row lock, serializing identical provisions.
Subsequent execution, deployment and Solution share locks use `NOWAIT`. Existing
Python writers use both execution-before-attempt and attempt-before-execution
orders; adding another blocking edge can deadlock terminal persistence. A later
lock conflict denies provisioning and rolls back the whole private transaction,
releasing its attempt lock without exposing a token or partial grant. Do not
retry automatically or change Python terminal writers. PostgreSQL interleaving
tests must prove prompt denial, lock release and absence of partial grants,
alongside uncontended concurrent idempotence. This unwired helper's contention
behavior is not acceptance of a public runtime admission or retry contract.

First-packet eligibility is deliberately conservative: non-bypass original org
must match the resolved SDK org, in addition to effective-execution/default
binding. The existing SDK's local default is the execution's effective org;
legitimately authorized foreign-effective admission therefore needs genuine
trusted entitlement evidence before this profile can support it. Do not invent
an approval flag or substitute the engine transport's superuser authority.
Active-install/source eligibility and real source/session/deadline/caller hooks
are additional unresolved integration cases, not changes to legacy SDK behavior.
Nothing uses these private helpers for a real runtime yet.

## Historical private foundation candidate evidence

Candidate `ddb5131887ba22dc9038efc99b32b7d3a6d70bdd`, on
`fix/runtime-sdk-purpose`, contains exactly the seven owned paths above and
current main `fcfbedfe189e8efab0546890828806c91cec358d`. Independent Sol
security/persistence review identified the initial lock cycle and accepted the
narrow repaired source conditionally; the corresponding three PostgreSQL
contention cases then passed in supported CI.

[Comprehensive run 36923189109](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36923189109)
passed at that exact branch checkout, not a synthetic merge. The API test-image
digest was `sha256:9ebfafdac5b89cbb295b0e51fca6fe9d9633ea49d0d9e39044467802fab6a4c7`.
Alembic upgraded from `20261001_ws_declaration_digest` to the single new
`20261001_runtime_sdk_grants` revision. All 81 new token/codec and 32 new
PostgreSQL grant cases passed, including committed-Start/idempotence, flush-only
denial, failed-commit rollback, secondary-lock contention, fencing and renewal.
The full unit lane passed 11,642 tests, with three existing skips and 35
deselections. Lint/type, client unit/E2E, all four backend E2E shards and MCP
conformance also passed; release/build/deployment jobs were skipped by their
normal policies.

This establishes private foundation behavior at the historical candidate only.
It did not establish the then-uncompleted publication or application gates.
Physical-host runtime substitution is forbidden. The private functions have no
HTTP/runtime callers, source permission remains empty, and neither full CRED
nor C2/C3 acceptance follows from this run.

## Current prerequisite and reference candidates

Separate [CRED-P1-S #1024](https://github.com/Midtown-Technology-Group/bifrost/pull/1024)
is open, unmerged and unwired at
`6419069da195b053c885ab349f431ff4fae62098`. It contains main
`658283e8c8707ceecf9e2c0b56b779d074b7bdaf` and migration parent
`20261001_solution_src_account`. It verifies signatures and claims through
locked PyJWT 2.15.1 `decode_complete` before inspecting the closed header.
Candidate Sonar and CodSpeed analysis passed. The historical cross-environment
benchmark issue #1003 remains open; it is not a current-head CodSpeed failure.
No threshold waiver or benchmark retry is accepted.

The supported literal clean-current-main `./test.sh pre-pr` gate passed in
[run 36933342485](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36933342485),
job `110614360691`, artifact `11198251722`. Its candidate receipt records that
exact head, tree `fdcbdc08f15d6841c46e5de1d58e4e929eb9aad8`, main `658283e8c`,
exit zero, cleanup status zero, and empty owned container/network/volume
readback. The comprehensive candidate run also completed successfully under the normal
repository lanes.
Earlier e049/ddb passes retain their historical pins; no result is relabeled.

Independent test-only [CRED-P1-REF #1026](https://github.com/Midtown-Technology-Group/bifrost/pull/1026)
is open at `50efd185dfd0b98682650e93db6022581184eaa7`. Its preceding candidate
`99ab8dd653ba8f42381f8d627aa56e2f4903935a`, containing main `658283e8c`, passed
[comprehensive run 36933538470](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36933538470).
The literal pre-PR job `110608843578`, artifact `11196429549`, records tree
`185e43207bfe3e9a3efbd9a5fee8282ae34e81c7`, exit zero and empty owned resources.
All 25 selected public HTTP/unchanged-SDK reference cases passed, with no selected
skip. All 18 OAuth fixture unit cases passed; the full unit lane recorded
11,592 passed, three unrelated existing skips and 35 deselections. Reference
shard 4 recorded 509 passed and 21 unrelated existing skips. Later 50efd only
moves a pure predicate before its guarded use and explains awaited task/IPC
failure handling for CodeQL review. Its own supported run
[36936246934](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36936246934)
remains pending; 99ab results are evidence for 99ab, not a later candidate pass.

The reference observed these compatibility differences:

- All four caller profiles received organization secret/non-secret config.
  Direct external humans excluded global defaults and OAuth; ordinary humans
  and both legacy engine delegation profiles received those values through get.
- Mapping reads omitted global/default secrets for both engine profiles, while
  ordinary-human mapping included them. Replacing transport authority with an
  ordinary caller would expand the mapping response.
- Entity-template recovery performed exactly one synthetic provider exchange
  with committed token changes for ordinary humans and both engine profiles.
  Direct external humans performed zero exchanges with unchanged committed
  provider/token state.
- Actual application request-context observation attributed both engine profiles
  to System/Engine in the effective organization; humans retained their actual
  identity. These reads emitted no owned-resource AuditLog event. That absence
  is not proof of actor attribution or a durable recovery audit event.

These are executed observations, not approved dedicated-purpose policy. The
engine token records `delegated_is_external`, but the selected data filter reads
primary principal/user-row external status. H/R must explicitly settle external
admission, the mapping response profile, route-local actor attribution,
mixed-credential/default-deny ingress and maintenance disposition before public
wiring. Neither copying engine external disclosure nor switching to direct-human
restrictions is silently authorized. The narrow credential mechanism approval
remains unchanged. Public H/R, real caller/session/source/supervisor custody and
all C2/C3 gates remain uncompleted; source permissions remain empty.

## Legacy SDK reference characterization packet

**CRED-P1-REF** is frozen as test-only characterization before the public H/R
policy decision. Its initial design baseline was main
`c0931d119198c538ad7c7cc8a577a6928fe42780`; the executed 99ab candidate includes
main `658283e8c` as recorded above, with the public SDK unchanged.
This is independent of private S issuance and does not prove consumer admission,
installed-source custody or runtime extraction.

The packet owns exactly three paths: new
`api/tests/e2e/platform/test_legacy_engine_sdk_reference.py`, the existing internal
`api/scripts/scheduler_fixture_server.py`, and
`api/tests/unit/test_scheduler_fixture_oauth.py`. It pairs genuine ordinary and
external users with actual `mint_engine_token` delegation, using both real HTTP
and the unchanged SDK. Exact name binding never trims input. Original/effective
orgs match for this packet; foreign-effective entitlement remains a separate gate.
No production auth, router, SDK, schema, consumer or CI change belongs here.

Compare global/org secret and non-secret projections, mapping precedence/null
behavior, exact request actor identity, and actual entity-template OAuth recovery
without a scope override. The engine/external differences are now executed observations recorded above,
not approved dedicated-purpose policy. Each recovery arm uses separately
owned synthetic configuration and fresh committed PostgreSQL observations.
Unexpected provider calls or persistence fail rather than being normalized away.

The existing internal OAuth fixture may record an exact synthetic audience marker
`cred-p1-reference:<32 lowercase hex>`. Freeze a thread-safe maximum of 1,024
markers, no eviction or reset, and at most 65,535 admitted attempts per marker.
Reserve/count admission atomically before entering the existing OAuth handler;
record its finite integer HTTP status (100–599) at most once before reply.
The next attempt marks permanent exhaustion and receives a static 503 before
handler effects; already admitted requests may finish. GET returns bounded
`attempted`, `statuses` and `exhausted`; zero/one-call proof requires
`exhausted=false`. These counters cover admitted attempts, not rejected requests
after exhaustion. Use static 503 on marker capacity and 400 for malformed markers.
Its test-service-only receipt GET accepts exactly the marker's 32 hex characters;
an unseen valid key returns zero. Preserve existing untagged behavior. Never
retain or report credentials, request bodies, URLs or provider exception text.
Actual HTTP unit tests must prove isolation, capacity and zero-call evidence.

These SDK reads do not emit an audit event; absence cannot establish an actor.
A passive test-only observer may run inside the actual request-context middleware
in a separate, owned `create_app`/uvicorn process with unchanged production
lifespan and real loopback HTTP. It observes bounded actor/request-user identities
without setting contexts, overriding auth, inventing audits or modifying requests.
Ordinary positive control and failed/anonymous context isolation are mandatory.
Compare the instrumented result profile against the existing API. Do not overwrite
or dispose pytest's global database resources. Unsafe duplicate startup, shared
state mutation or unavailable safe lifetime blocks this observation arm; no
lifespan override substitutes for it.

Execute only in supported hosted CI or verified VM106. Skipped E2E cases are a
blocked gate. Verify child/socket/client shutdown and owned fixture cleanup;
retain guarded history until disposable-stack teardown rather than bypassing
guards. No live vendor, source-policy redesign or public credential acceptance
follows from this packet. Observed discrepancies return to architect review
before H/R is frozen; the test packet cannot silently fix or ratify them.

## Verification and stop conditions

Required tests cover exact codecs/claims, legacy audience rejection, invalid
signature/algorithm/issuer/audience/type/purpose/extra claims, grant field drift,
pre-Start/flush-only denial, idempotence/conflicting provision, failed commit,
cross-attempt/org/session/worker fencing, deletion/terminal/revocation, finite
expiry nonextension and restricted renewable grants. Use real PostgreSQL for
transaction/constraint/race proof; mocked validators do not establish it.

Commands: scoped `./test.sh` for the new files, existing engine-token lifetime
tests, `./test.sh quality api`, and clean current-main `./test.sh pre-pr`.
Runtime tests use supported hosted CI or verified VM106, never physical-host
Docker/host pytest or VM101 restart. Static checks alone are not acceptance.

Stop/report unknown source/caller/session provenance, migration-head drift,
required wildcard authority, public minting, expiry extension, legacy decoder
changes or SDK/workspace accommodation. This packet does not pass the paired
real HTTP/SDK authorization gate; that still needs narrow handler/renewal/ingress
integration and unchanged positives with the same restricted provision. Runtime
DB/Redis/environment confinement, source custody, provider slots, mechanical
writer exclusion and C2/C3 remain separately unproved.
