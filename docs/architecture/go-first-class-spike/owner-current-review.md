# Current isolated lifecycle writer review

Candidate implementation: `50ccb0ef3432b39f939e0e6e361e4299f2681478`.
SDK `0.0.0-spike.2`. Interface release: Thomas's approval of
`b8b44cbc01b1fb6be86f4fba050e93eb0da55924` for isolated implementation.
This is the accountable source review, not human writer or runtime acceptance.
[Historical review and fault candidates](owner-schema-review.md) retain the
original diagnoses, decisions and source-bound component evidence.

## Schema, identity and immutability considered together

The canonical storage definitions are
[`runtime_execution.py`](../../../api/src/models/orm/runtime_execution.py).
The migration chain, rather than ORM declarations alone, supplies actual
constraints, generated claims, close guards and immutable-record triggers.

| Record | Identity and constraint | Authority limit |
| --- | --- | --- |
| Deployment artifact | Existing workflow/Solution deployment, reviewed source/deployment, native descriptor and accepted archive/build digests; immutable association | The owner verifies actual archive entries and staged source bytes. A source hash is not a binary or archive identity. |
| Execution owner | Composite actual execution/workflow/deployment/artifact; one immutable owner incarnation and literal caller snapshot | A replacement process retains this logical owner. No recovery creates or reassigns ownership. |
| Session | Owner plus typed workflow attempt/claim/worker; one per attempt; immutable Prepare/binding/custody digests and permanent close tombstone | Process PID/start ticks, container/StartedAt/cgroup and protected custody must independently agree. A UUID or hash alone is insufficient. |
| Start | One per session, exact owner/attempt/message, input/context hashes and decision clock | Existing execution/attempt Running projection commits before outbound permission or SDK material. |
| SDK grant/operations | Same session/claim/Start/worker/supervisor/caller/deployment/Solution; one finite grant per attempt/session and closed operation rows | Dedicated SDK access only. No ordinary-route, lifecycle/admin, renewal or authority upgrade. Session close irrevocably revokes it. |
| Provision/release | One admission per purpose, exact prior grant/Start/operations/expiry and immutable release/delivery evidence | Material requires observed matching release commit and live guardian serialization. Uncertain commit sends no material. |
| Result receipt | Session/Start/message and retained raw-payload SHA256; immutable decision/winner | Result/Cancel serialize under common locks. Receipt follows observed commit; lost ACK never authorizes replay. |
| Resource settlement | Session/execution/owner/typed-attempt composite FK; one fact per session; exact 1–65,536 raw bytes and database SHA256; immutable UPDATE/DELETE and RESTRICT retention | Closed-session constraint is necessary but does not prove physical retirement. The Rust writer consumes opaque actual guardian/source-retirement evidence. |

The resource migration is
[`20261011_runtime_resource_settlements.py`](../../../api/alembic/versions/20261011_runtime_resource_settlements.py).
It rejects facts for an open session and forbids downgrade with retained facts.
It supplies no operational role or production dispatcher. Source accounting uses
the common settled fact; Result/Stopped alone cannot release source consumers.

## Common lock order and writer exclusion

[`lock_session_state`](../../../spikes/go-native/owner-rs/src/lib.rs) acquires:

1. The existing shared workspace-release fence, exactly
   `pg_advisory_xact_lock_shared(hashtext('bifrost:workspace-release'))`.
2. The matching typed workflow attempt.
3. NOWAIT owner, execution root, deployment and Solution refresh.
4. The matching session.
5. Start, admissions ordered by purpose/id, grants ordered by id, admitted
   operations where needed, receipts ordered by Result message, then resource fact.

Contention or stale identity aborts the transaction. Session locking serializes
close against an absent grant as well as existing grants. FK/trigger locks and
incumbent execution-first cancellation/poison are included in the actual race
suite; changing only a written lock diagram would not qualify this writer.

The isolated
[`runtime_writer_guards.sql`](../../../api/tests/e2e/platform/fixtures/runtime_writer_guards.sql)
uses distinct authenticated `session_user` values (`wex_core`, `wex_incumbent`),
non-owner/non-superuser/non-BYPASSRLS principals and no role membership or schema
escape. It guards OLD/NEW parents, actual roots/typed and generic attempts,
logs/usage/events, logical non-FK links, source accounting and retention paths.
The SQL and consumer tests also observe external effects: rejection of a database
statement alone is not exclusion of Redis/publication effects.

This is a disposable fixture, not a replacement for the application's privileged
production login. Existing Python-owned positives must remain unchanged; no
production routing, role provisioning or dispatcher is enabled by this spike.

## Recovery and uncertainty

The shared writer in
[`resources.rs`](../../../spikes/go-native/owner-rs/src/resources.rs) validates
the same fence and durable root/attempt/session/grant/receipt agreement. Closed
Result or Cancel recovery uses the original protected custody and exact source
identities, proves the original actor dead and consumes exclusive stop-only
retirement. It cannot attach, Start, deliver material or replay SDK/Result frames.
Open-session owner-loss recovery uses that same preflight before the existing
cancellation writer; uncertainty denies continuation rather than retrying it.

Actual Result, release and resource-fact COMMIT acknowledgements are withheld
by the PostgreSQL fixture. Read-only reconciliation checks the exact immutable
receipt/fact and digest under the common order. It never reconstructs the consumed
retirement token or inserts a second fact. Wrong digest/owner is denied.

[Actual Docker create reply-loss evidence](docker-create-loss-component-proof.json)
proves one daemon-accepted create, no reply forwarded, no second create, denied
fresh Start and exact container drain. This physical case supplies no tenant
Start, SDK execution or durable finalization claim.

Interrupted partial source retirement remains unproved. The current exclusive
recovery intent denies a retry after uncertainty; partial removal yields no token
or finalization. Missing paths cannot be treated as proof of completed cleanup.
Any future automatic continuation must retain the same identity, exclusive phase
custody and verifiable observations without replay or ownership reassignment.

## Partial-retirement negative candidate

At `56d1fb006`, a separate tenth execution exercises a negative outcome while
preserving the nine completed cases. It runs the same accepted Go binary and
real Rust Start/SDK/cancellation, then drains the original container/CLI. Only
after that drain does the first-party owner add an inert fixture obstruction
to its protected source directory; accepted file bytes and workflow code are
unchanged. The ordinary retirement routine verifies and removes accepted source,
material and gate, then its final directory removal must return CleanupRequired.
No RetiredSession token or resource-settlement write is permitted.

The driver SIGKILLs and waits the original owner after the failure, then tries
the existing stop-only `--drain` entrypoint. Missing/changed original resources
must deny adoption. Real database assertions require one retained owner, revoked
finite grant, no Result or resource fact, API Cancelling/null result and a pinned
source consumer. The SDK request and Cancel/Stopped/Start correlation are checked.
Only after those assertions does the first-party fixture dispose its exact inert
obstruction; source accounting must remain pinned. Disposal is not a runtime fact
or durable completion. The disposable database/stack then follows normal teardown.

This candidate tests partial failure and owner death after that failure, not
arbitrary SIGKILL during every unlink or automatic cleanup resumption. Fresh
hosted tests are pending; the reporting defect found during source review of its
predecessor is [retained](partial-retirement-candidate-correction.json). No
production behavior, shared protocol, role, token construction or recovery writer
is added. Required human acceptance remains separate.

## Evidence and acceptance boundary

The last fully independently inspected live/security package is
[`nine-case-proof.json`](nine-case-proof.json), source `c739480ce7f79168d5df173158147f7be2d94700`.
It includes actual open-session owner-loss recovery, 13,898 unit tests, 126 baseline
and both 248-case guard suites, API quality and verified task cleanup. The newer
`50ccb` current-source full live/security lanes remain pending. Their old
statuses do not qualify this candidate. The complete source-bound results and
cleanup records must be inspected before submitting runtime evidence for acceptance.

Thomas's conditions still require human consideration of this concrete schema,
identity, immutability, writer exclusion and lock order together, and acceptance
of the required runtime evidence. Green conformance is not that acceptance.
No production, deployment, merge, credential expansion, renewal, extra agents or
profile freeze is authorized or claimed.
