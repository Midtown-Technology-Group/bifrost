# Device reference characterization (W0-B)

Version: 1. Device-source baseline: `d39aa0adf15ba12dfde5f2f39628cac88c2e98a0`.
Platform main merged through `e77947fab5762e49dd5fdc390bd65ca84bc22c3c`;
its changes do not alter the device authority used here.
Procedure: `mtg-engineering-flow` 2026-09-30.1. RFC #1005 is provisional.
This package is an executable Python reference, not Rust or Go compatibility
evidence. Rust routes deliberately raise `BackendUnavailable` until W2.

Supported hosted reference run
[36827831696](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36827831696/job/110257404657)
at harness head `b392ebcac796f4cc5cf1cc2a21948025a93b1725`, synthetic merge
`3d939da790e438d116a7e853d9c3dfec4407f20b`, passed **90 tests in 116.69s**.
It confirms all five Python routes, exact Bifrost validation envelopes,
handled-conflict partial persistence, reclaim/loss, independent seeded Python
comparison and shared competing claims. Fresh captures independently detect
forwarded real HTTP status/body mutations, an actual committed owned-job SQL
mutation and a scope-preserving wrong-channel Redis publication. These synthetic
harness self-tests establish drift detection, not Rust or Go compatibility.
The prior run 36826144370 passed 69 of 87 tests; its 18 failures came from the
characterization assuming FastAPI's default validation detail list. The final
run confirms the correction against Bifrost's existing global handler without
changing server behavior.

The exact downloaded, sanitized 12-step lifecycle observation is retained as
[python-device-lifecycle-v1.json](reference/python-device-lifecycle-v1.json).
It is observed evidence, not redefined expected behavior or formal W0 acceptance.
Its source is hosted artifact
[device-reference-3d939da790e438d116a7e853d9c3dfec4407f20b](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/36827831696/artifacts/11145639528);
SHA256 `dcd0d7d17f9e44473d20c4f464929a1c37666957f21218d67851d07f186f7aa7`.
The architect and independent reviewer inspected it before retention: fixture
identities and claim tokens are labels, and credentials/raw hash bytes are absent.
The lifecycle test emits sanitized `/tmp/bifrost/parity-lifecycle.json` only after
actual successful execution. No observation has been fabricated.

Acceptance remains outstanding. The authenticated clean-candidate normal
`./test.sh pre-pr` gate at `b392ebcac796f4cc5cf1cc2a21948025a93b1725`
finished with exit 1: API quality reported 3,954 errors and 89 warnings,
predominantly unresolved third-party imports. The supported local `pve-t340`
project `bifrost-test-35c8fc78` separately could not start PostgreSQL/RabbitMQ
because `docker-default` AppArmor denied Unix sockets (E-01). The passing hosted
reference job does not waive existing gates. No host pytest or security-profile
bypass was used. W1/W2 remain stopped pending architect disposition of the
possible-spawn replay and partial-log persistence risks; Rust/Go, mixed-writer
and cutover proof remain outstanding.

Run through the supported Linux Docker harness:

```sh
./test.sh stack up
./test.sh tests/parity/test_device_reference.py -v
./test.sh tests/e2e/api/test_device_protocol.py tests/e2e/api/test_device_job_failures.py -v
./test.sh quality api
```

The architect owns `test.sh`, Compose and affected-test planning. No dependency,
business route, global fixture, schema or production setting changes here.

## Frozen harness interface

`api/tests/parity/harness.py` exposes `DeviceAdapter(name, base_url, capture,
transport=None)`. `request(step, route, body, key)` returns `Observation(step,
status, body, database, events, before, after)`. Transport injection does not
imply a mocked server: acceptance requires actual HTTP implementations.
`EvidenceCapture.snapshot()` and `drain_events()` are the evidence seam.
`PostgresRedisCapture(environment, redis_url)` supplies the real implementation.
It pattern-subscribes `bifrost:*` before requests and uses a unique Redis FIFO
barrier to establish event absence without a guessed quiet period. Publications
on fixture channels or carrying a fixture's top-level device/job identity are
captured with their actual channel, including incorrect prefixes/targets.
Unrelated tenants' events are excluded; messages losing both channel and payload
scope cannot be attributed safely in a shared environment and require dedicated
infrastructure to characterize. Expected channels are `bifrost:device:<device>`
and `bifrost:device_job:<job>`; payloads stay intact. A deliberately misrouted
synthetic publication is a capture self-test, not API or consumer proof.

`SeededEnvironment(engine)` owns two synthetic organizations and devices and a
job; `seed()`, `update()` and `cleanup()` commit via independent sessions.
`bindings` maps fixture UUIDs to explicit role names. It deletes only its own
organizations through current cascade constraints. Credentials are generated
in memory and never included in observations. Hash presence and exact equality
to each environment's seeded key hash are captured, with hash bytes omitted
from reports; any backend changing its hash is visible. All other device/job/log
columns are observed.

Differential mode creates separate environment objects with disjoint tenant,
device, session and job identities. Both can use one migrated test database
because all owned route queries are device-scoped; callers can supply separate
engines and Redis endpoints for separate stacks. Neither instance is reused
or reset to simulate the other backend. Shared mode intentionally passes the
same environment to two adapters and captures evidence after their concurrent
requests complete. The independent-environment and shared Python/Python
competing-claim tests passed in the supported run above. Python/Rust mixed
writers require W2 and an actual Rust endpoint.

`lifecycle(adapter, environment)` and `reclaim_and_loss(adapter, environment)`
are reusable scenario drivers. `route_body()` supplies the same five-route
vocabulary to auth, scope and validation cases. Python cancel/watchdog calls
remain the current service implementations in either candidate environment.
The watchdog's session-local ORM query listener restricts both selects to the
owned job before calling the existing transition implementation; it cannot
mutate another fixture's rows. This characterizes owned-row transitions, not
the scheduler's global scan/leadership. No global session listener is installed.

`assert_parity(reference, candidate, reference_bindings, candidate_bindings)`
compares step order, HTTP status, entire JSON body, committed rows and ordered
Redis channel/payload lists separately. There are no expected Rust differences.
Generated claim tokens are mapped bijectively only in `claim_token` fields.
Explicit fixture UUIDs map only in declared protocol/database identity columns
and top-level event identity fields; exact expected channel identities map to
fixture roles. UUID-looking strings inside scripts, params, output, logs or
nested event content are opaque and remain byte-for-byte unchanged.
Only listed server timestamp fields may alias: each new timestamp must be
inside its operation's measured wall-clock window, and aliases retain equality,
relative ordering and change/no-change across all observations. Seed timestamps
stay fixed. Nulls, missing fields, client log timestamps, output, durations,
limits, tokens reused across requests, errors, scopes and event order remain
meaningful. Different timestamp equality/order is an unexplained difference,
not permission to broaden normalization. Clock-skewed separate hosts require
an approved clock capture mechanism before accepting comparisons.

## Source-derived behavior matrix

Authority is the checked-in `device_protocol.py`, `device_jobs.py`,
`device_keys.py`, Pydantic `device_jobs.py`, ORM models and current Alembic
device/job/log migrations, plus `main.py`'s global validation handler and
`models/contracts/common.py`'s ErrorResponse. These were checked against existing
unit/device E2E suites. Native Pydantic errors pass through Bifrost's global
handler: HTTP 422 body is exactly `{error: "validation_error", message: <joined
field messages>, details: null}`, not FastAPI's default detail list.

| Boundary | Executable characterization |
| --- | --- |
| All five routes | Auth matrix: missing header is Bifrost validation_error 422 with message `header.X-Bifrost-Key: Field required` and null details; malformed/wrong-secret/unknown-ID/wrong-prefix/uppercase UUID/key-disabled is 401; disabled status takes precedence over key-enabled and returns 403. Rejections do not touch rows or publish. |
| Heartbeat | Pending poll 5s, otherwise 10s; version strips control chars/whitespace; session mismatch touches device but preserves job activity; cancel flag visible only to owning session. |
| Claim | Response includes script/params/limits and 60s lease; DB claim token/session/activity committed; idle 204; competing claimers have exactly one winner; 55s old claim remains fenced and 65s old claim reclaims, with measured cutoff windows. |
| Running | First transition accepts and replaces another session; current-token replay preserves stored session and started_at while renewing activity. No additional session fence is invented. |
| Logs | Out-of-order seqs sort ascending, gaps allowed; durable log_sequence is highest; exactly one ordered event for successful inserted rows; replay ignores ts, renews activity, inserts/publishes nothing; changed text conflicts, but earlier staged inserts can commit during dependency teardown without a log event. |
| Result | Terminal status/output/exit code commit; duration_ms/truncated/extra input are not stored; result replay is terminal 409; wrong fence takes precedence over terminal state. |
| Scope/errors | Foreign-device and unknown-job are 404 before fence; authenticated error still commits device touch. |
| Validation | Entry/batch Pydantic limits give Bifrost's exact global validation_error envelope/messages at 422; duplicate seq gives domain 422; aggregate log chars above 1 MiB gives domain 413 before fence. Only domain errors touch device. Lost is rejected as agent result. |
| Cancel | Python pending/claimed cancel terminal immediately; running remains running with flag, then agent may finish cancelled. |
| Reclaim/loss | Claimed age beyond lease yields fresh token, old token fences all mutations; committed running ACK replay is idempotent; watchdog silence and timeout backstop each yield lost; late current-token mutations terminal; lost never requeues. |
| Comparator | Fresh captures must detect real forwarded HTTP status/body mutations, an actual committed owned-job SQL log_sequence mutation, and a misrouted Redis publication independently. Cloned successful observations also exercise comparator-only mutations. Opaque payload UUIDs must survive mapping and independent Python environments must compare equal. |

The deliberate mutation tests are synthetic harness self-tests, not Rust or Go
compatibility evidence. `ResponseDriftTransport` forwards each request through
`httpx.AsyncHTTPTransport` to the real Python API, records the original successful
response, then changes status or body before `DeviceAdapter` parses it. Fresh
committed-state/event capture must remain equal while the intended HTTP plane
fails comparison. The SQL test commits a log_sequence change to the exact owned
job between identical heartbeat steps; a fresh connection must observe it and
the database comparison must fail while HTTP/event planes remain equal. Neither
test edits a captured Observation to simulate these boundaries. The existing
wrong-channel Redis publication test exercises the actual event subscriber.

## Critical spawn ambiguity retained from reference

`claimed` means the server has not committed `/running`; it cannot prove that
no external process has started. If all `/running` requests fail to reach or
commit at the server after a real spawn, claim age still permits reclaim and
a fresh fence. Heartbeat updates activity, not claimed_at. The scenario
`reclaim-unreported-spawn` records this server behavior without spawning a
process or claiming effect-once safety. By contrast, an ACK lost after running
commits is recoverable through the current idempotent running replay. W1/W2
approval remains blocked on the architect's review of the uncommitted-report
ambiguity and unchanged pinned Go runner evidence. The harness changes no
Python behavior to hide this issue.

## Handled log conflicts can commit partial rows

Additional authority: `core/db_deps.py` binds `DbSession` to `get_db`, whose
normal dependency teardown calls `commit()`. `append_logs` stages rows while
iterating the sorted batch. If a later accepted seq conflicts, the route catches
`DeviceOperationError` and returns a normal JSONResponse, so teardown can commit
the earlier staged inserts despite HTTP 409. Activity and log_sequence assignment
are bypassed and no log publication occurs. The source-derived
`log-conflict-partial-persistence` scenario retains this discrepancy; runtime
confirmation passed in the hosted Python reference run above. It does not assert stronger rollback semantics or
change the Python implementation. This is a separate baseline delivery risk
requiring architect disposition before W1/W2 acceptance.

Dependency cleanup versus response-delivery ordering is FastAPI-version dependent.
The supported reference run verifies the current dependency and capture behavior.
Capture sets a transaction-local five-second lock timeout, then acquires `FOR SHARE` on the owned jobs to wait for any domain
writer's `FOR UPDATE` lock to release and reads all rows in that fresh session.
Observation's clock window
ends after committed-state/event capture, so teardown-generated receive clocks
remain observable. A plain post-response read alone is insufficient here.

Redis publications observed here establish payload/order after completed HTTP
requests and committed rows, not authenticated WebSocket delivery or Go client
compatibility. Transaction commit fault injection, Redis outages, full mixed
Rust races, pinned Go behavior, cutover/reversal and benchmark proof remain
later package acceptance gates. A mock consumer is never Go proof.
