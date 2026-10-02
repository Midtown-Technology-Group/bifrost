# C1-R: unchanged agent/workflow Python reference

Status: bounded reference foundation in progress; no runtime acceptance. This
packet establishes the Python reference for the [MVP amendment](rust-core-mvp-amendment.md).
It does not authorize Rust ownership, public credential wiring, merge,
deployment, live model/vendor calls, or another #1017 repair cycle.
Architect review uses MTG guidance package 2026-10-01.5 and Sol builders.

## Exact source and unchanged inputs

Current platform main is `e58db4955ddd30177bd613f1d85b7e203ad7832a`.
The retained C1-R foundation remains pinned to
`f770094eb28d8315a414fe8cb306f510da752d89`; it has not been rebased or
runtime-tested against the newer main.
Latest workspace main is `c1856d2fbc7530c65c67adfd28e4896ec402ccbf`; retained
inputs come from `e8605dc8edb6df8a997c171b534b324ac7ebd8ec`. The intervening
change affects one Ninja iDRAC PowerShell script only; selected Python and
boundary checker bytes are unchanged.
The workspace boundary audit covers 2,107 Python files with zero forbidden
platform imports and an empty internal-import allowlist. Prerequisites #1001
and #1112 remain ancestors. Advancement from `658283e8c` to the retained foundation changed CI capacity.
New main #1027 changes reviewed Live-to-Solution handoff readback: it validates
current ownership/lineage and active/dependency resource contracts, keeps
historical byte proof separate, and repeats omission proof under the release
fence and native Solution locks. It changes no selected agent executor or SDK
source. C1-R does not exercise Live handoff; C2/C3 must still preserve these
checks. No prior CI result is relabeled as testing this newer source.

The local reference branch `test/agent-capacity-reference` combines the retained
`f770094e` main
with the two approved, unmerged AUTH-P1 behavior commits
`31dd41f519b5c510c77a7ac6f70b9400c94a65e3` and
`24347e799bc7bd63c24563b2fc2cd4ee80eda624`. Composite
`60da4685f9515649b03dee00d44b1386475dcc84` has tree
`c5ba53419c9a3c9c208229c93fe849ec6c152513`; all ten AUTH implementation/test
paths are byte-identical to #1018 candidate
`d82219f5aada66d879f2da71b386f50675c66d4d`. This is an explicitly identified
reference overlay, not a claim that the corrected behavior is merged main.

Input commit `9a53f516376ed1fda952fe732289b8f4c000cd73` retains the declaration,
four authored Python files and separate inert bootstrap in
`test-fixtures/agent-reference/`. Its `provenance.json` records exact workspace
Git blobs, byte lengths and SHA256 values. The bootstrap is fixture-owned.
The actual future platform candidate containing these blobs is the installation
source commit; workspace origin and installation source are separate identities.

Keep both declared tools unchanged:

| Authored tool | UUID | Model wire name |
| --- | --- | --- |
| `inspect_capacity` | `0760d416-be3a-4a33-b540-5b6b0658075d` | `cove_data_protection_recovery_steward_inspect_capacity` |
| `preview_restore` | `93b7f115-de11-444d-8309-7aee9bac2bec` | `cove_data_protection_recovery_steward_preview_restore` |

Retain the full authored agent prompt, access/channels, model and budgets.
The single nominal case dispatches capacity only. Preview remains advertised;
neither preview nor bootstrap may execute. Do not retag tools, shorten the
prompt, patch the SDK, import source to discover metadata, or use sibling paths.

Reviewed recipes require runtime and repository source paths ending in `.py`.
Root-level inert inputs therefore remain `.py`, outside the API implementation
package. `.py.source` is unsupported. Do not format unchanged authored bytes or
add quality exclusions. The named lane mounts inputs read-only at
`/app/reference-assets`. Recipe repository mapping, carried runtime paths,
compiler closure and installed hashes must be compared independently.

Installation is a pending public-API acceptance gate: activate an inert reviewed
workflow candidate, then a reviewed workflow revision adding both authored
tools. Expected complete closure is five Python files. Never substitute direct
ORM registration, decorator introspection, a mocked consumer or direct tool call.
The bootstrap has no grants/agent attachment/enabled invocation endpoints;
fixture roles grant the authored tools. Global source permission stays false.

Agent creation is a distinct unresolved setup seam. Current
`api/src/models/contracts/agents.py:AgentCreate` accepts a profile UUID but no
agent UUID, Solution ownership or legacy model-name field;
`api/src/routers/agents.py:create_agent` generates its own UUID. A public-create
fixture must record declared-to-actual agent identity and compare every
configured prompt/tool/access/channel/budget/model property. It cannot claim
Solution-managed agent installation or preserve the declared UUID merely by
assertion. Root must select and freeze the supported public registration path
before C1-R-CASE; direct ORM registration and silent omission of declared model
selection remain forbidden.

## Named isolated lane

Entry point is `./test.sh agent-reference`. The explicitly selected case file is
`api/tests/e2e/platform/agent_reference_cases.py`, intentionally outside ordinary
`test_*.py` collection. This is a separately required reference lane, not a skip
or substitute for ordinary CI. Missing case/fixture/observer files must fail.

The lane derives a closed Compose configuration without changing stock Compose
or ordinary test commands. Use a fresh exclusively owned project; refuse any
existing containers, networks or volumes, including stopped resources. Build
and pull before creating the internal runtime network. Start real API/replica,
PostgreSQL, worker and scheduler processes; delivery configuration is PostgreSQL.
Worker consumers are `workflow,agent-run,summarize`, with one worker/concurrency
slot. RabbitMQ may remain a stock readiness dependency, never delivery support.

No published ports, external networks, host credentials/socket, ambient model
keys, proxies or optional vendor-secret files. The fixture serves only synthetic
model/Cove transport on `scheduler-fixtures:8080`. Strip ambient OpenAI
organization/project/custom headers as well as model keys. All inner operations
skip builds. Capture actual service settings, image identities and network/mount
custody using sanitized independent inspection.

The actual pytest container is named, detached and retained until its exit,
logs and configuration are independently inspected. Its lane-only stdlib
startup gate waits for host-issued custody approval before executing the exact
selected pytest arguments. Host inspection must compare saved prebuild image IDs
for all services and exact actual commands/entrypoints, mounts and network before
atomic release, then inspect again after exit. A probe container cannot stand
in for its identity. The dedicated project prefix is `bifrost-agent-reference`,
using the ordinary worktree hash but never the ordinary test project. Fail closed on inspection/wait/removal errors.
Register cleanup before startup, preserve failure/ambiguity, and independently
prove empty owned resources after teardown. Physical-host runtime testing is
forbidden; only supported hosted CI or verified VM106 may execute this lane.

## Model and Cove transport oracle

Source derivation uses locked Pydantic AI 2.35.3 and OpenAI 3.3.0 wheel bytes;
this is source evidence, not an executed model-client proof. Preserve separate
canonical registration schema and actual transformed model wire schema.
Both tools omit function `strict`; optional/defaulted parameters are retained.
Do not manufacture `required: []` for the capacity schema.

| Call | Exact semantic body keys |
| --- | --- |
| Responses detection | `model`, `input`, `max_output_tokens`, `store` |
| Chat detection | `model`, `messages`, `max_completion_tokens` |
| Agent rounds | `model`, `messages`, `tools`, `tool_choice`, `stream`, `max_completion_tokens`, `store` |
| Summary | `model`, `messages`, `stream`, `stream_options`, `store` |

Agent execution is nonstreaming; summary actually streams. Both agent rounds
advertise both tools; the second round contains the real assistant tool call and
real JSON tool result. Summary uses the original prompt prefix, not the agent's
execution-only suffix. Preserve actual adapter/protocol choices, tool descriptions,
optional defaults and synthetic credential equality. Two transport-detection
pairs can occur because independent resolution flushes roll back; do not invent
a committed transport cache or reuse a detection transcript for another run.

Cove accepts the unchanged client's login and the exact recovery-agent/dashboard
queries, including empty `fields` and partner/type/sort selectors. Empty recovery
inventory is a genuine read-only capacity success, not a canned successful tool
result. No fixture may return an invented SDK response, execution identity or
successful domain result.

## Passive SDK and event observation

The future lane-only ASGI factory wraps unchanged `src.main.create_app()` in
both API processes. Product startup/routes/auth/dependencies remain untouched.
Use a plain ASGI tee: preserve scope, receive/send messages, headers, chunks,
disconnects, exceptions and lifespan acknowledgements. No eager body reads,
replay, buffering before dispatch or response reconstruction. Observation adds
overhead and is not claimed timing-neutral.

Observe actual `/api/sdk/integrations/get` requests, including malformed attempts.
The nominal safe projection is exactly integration name, global scope and actual
installed Solution UUID. Verify the observed bearer with the existing trusted
signature/issuer/audience/access-token verifier inside the API. Retain only
allowlisted canonical identity UUIDs and strict authority flags, never token,
token digest, secret, raw headers, exception text, SDK response body or its hash.
SDK response observation is status/byte count/completion only. The legacy engine
remains privileged: signed identity does not prove fresh attempt/lease admission
or accepted dedicated-runtime authority.

SDK receipts use global private ingress
`POST /__agent-reference/observer/sdk-receipts`; case readback uses
`GET /__agent-reference/{32hexcase}/observer/sdk-receipts`. A separate synthetic
256-bit capability file belongs only to API/replica/fixture/runner. Receipt
transport disables redirects, retries, ambient proxy and cookies. One sender
per role, one in-flight request, deadlines at most one second; failed/oversize
acknowledgements and overflow are permanent observation failures.

Only one case is active. Producer receipts contain no invented case/run ID.
Associate actual signed child execution identity with the independently returned
public run, committed child Execution and committed AgentRunStep. Before-arm,
after-close, foreign-run and unverified observations cannot be dropped/reassigned.
The exact closed receipt/ack/status/arm/bind/close schemas and shutdown counter
equations are an outstanding interface freeze: **no observer/fixture builder
starts before architect approval of that freeze**.

| Shared bound | Value |
| --- | --- |
| Whole-lane case allocations | 32; first packet permits one nominal case |
| Model/Cove plus SDK requests per case | 64 |
| Model/Cove input bytes | 65,536 |
| SDK request/header/receipt bytes | 4,096 / 8,192 / 8,192 |
| SDK response bytes counted, never retained | 65,536 |
| SDK receipts per case across roles | 32 |
| Receipt queue / whole-lane receipts per role | 32 / 128 |
| Events per case including pre-bind/channel duplicates | 64 |
| Bytes per event | 65,536 |
| Acknowledgement / atomic status file bytes | 1,024 / 2,048 |

No reset, wrap, eviction, retry or truncation into apparent success. A private
owned status volume is API-writable/runner-read-only, absent from worker/child.
It records bounded counters/static failures only. Missing/stale/unacknowledged
status prevents acceptance even when the collector could not receive a failure.
Retention caps do not prove upstream HTTP/Redis allocator confinement.

Subscribe before public trigger, wait for actual Redis acknowledgements on org
(or global), all, and `bifrost:agent-run:*` channels. Pattern subscription is
allowed only in this exclusive empty lane. First detail publication can precede
the public trigger response and any model-response hold; bounded pre-bind events
must all correlate to the actual returned run. No DB/stream backfill substitutes
for a live event. Export only selected synthetic-safe fields.

Preserve source ordering honestly: `_record_step` publishes before buffered
steps are persisted. Nominal parent terminal and successful summary publishers
follow their commits; transport settlement is a separate later transaction.
Do not demand row absence at subscriber receive time or invent missing event
fields. Pubsub failures can be swallowed by Python; a missing required event is
failed observation, never repaired by manually invoking a publisher.

Fresh read-only committed readback joins real parent/child attempts, workflow
pins, tool-result steps, delivery identities, summary and usage. Agent usage is
22+40=62; summary usage is a separate 50. The run's combined usage may be 112.
Never export delivery envelopes, lease tokens or attempt claim tokens. Completed
summary event alone does not prove delivery settlement. Require actual final
settlement separately, and no preview/bootstrap execution.

## Work packages and evidence gates

| Package | Owned paths | Acceptance / stop conditions |
| --- | --- | --- |
| C1-R-LANE | `test.sh`, named lane coordinator/renderer, stdlib runner gate, lane unit tests, additive hosted workflow | Static review then supported isolated execution; stop on ambiguous resource ownership, leaked credentials, ineffective isolation or incomplete actual-container readback. |
| C1-R-WIRE | Shared contract proposal, then `api/scripts/agent_reference_contract.py` after approval | Freeze exact closed messages/counters/shutdown before implementation; stop if completeness requires rewriting product/lifespan behavior. |
| C1-R-OBS | Test-only observer factory and focused unit file | Transparent ASGI forwarding plus red-capable tamper/overflow/redirect/queue/status tests; no auth override/product edits. |
| C1-R-FIXTURE | Dedicated fixture, additive scheduler-fixture dispatch, focused unit file | Closed model/Cove transcript and receipt ledger; independently prove real client wire shape, no fabricated result/identity. |
| C1-R-CASE | Explicit case file/public installation/readback | Real worker, unchanged authored source/SDK, actual events/DB joins/settlement; missing custody/correlation is STOP. |

Root owns shared interfaces and case orchestration. Freeze paths before parallel
builders; preserve other dirty work. Static AST/Ruff/bash checks are not runtime
acceptance. Run ordinary repo checks and literal clean-current-main pre-PR gate
before publication, plus the separately required named lane in supported CI.
Default collection must not accidentally execute this reference or make its
absence appear green. No gate thresholds, expectations or skips are weakened.

Acceptance must detect assertion-input drift in actual SDK receipt, event/step
identity, committed child/usage, summary event and delivery settlement, reusing
the already-observed transcript without another nominal execution. Unit tests
may use a synthetic ASGI app only to prove observer transparency; E2E must use
the real application and consumers. Report product failure, observation failure
and unobserved ambiguity separately. No fixture resets/retries until green.

Current proof: byte-exact inputs and AUTH source composition, independent source
reviews and static lane checks only. Independent lane review found shared
ordinary-project identity, post-effect custody checking, missing prebuild image
binding and unchecked actual argv. The six-file correction is committed locally
as `7b24f9d21d0849188bcc2c94d75b67d63a6e75a1`, with no push or PR. Independent
source re-review verifies the dedicated project, actual-runner pre-effect
release, all-service saved image IDs and commands/entrypoints/users/working
directories, and finite evidence publication from an unmounted host directory.
The evidence directory is mode 0700; exclusive no-follow writes prevent
container-supplied filenames from becoming trusted artifacts. Bash syntax,
AST parsing, scoped Ruff/format and whitespace checks passed. These checks
did not execute the authored negative tests, Docker or pytest. Source defects
are corrected; supported runtime proof is still required. Public installation, model/Cove wire,
effective image/environment, SDK custody, actual events/commits, ordinary and
named CI remain unexecuted for this packet. Dedicated-runtime public policy,
runtime extraction, source custody, mixed Rust writers and rollback remain
separate CRED/C2/C3 gates. No Rust CONTINUE decision follows from this foundation.

Closed-wire source review also found a real lifecycle gap: pinned Uvicorn 0.46.0
can finish after forwarding the application's shutdown acknowledgement without
awaiting the observer's remaining lifespan task. A promised post-ack receipt
therefore has no reserved execution window under the stock factory command.
Furthermore, waiting for pytest exit before stopping APIs cannot let that same
runner verify final API closure. Neither a timer nor teardown after a pytest
pass supplies the missing proof.

The source-only alternative has been reviewed; its pure codec interface is
frozen in the [private observation contract](rust-core-mvp-agent-observation-wire.md).
Runtime integration remains gated: a lane-only
server shim preserves the real application and its lifecycle messages, then
awaits the retained actual lifespan task after normal Uvicorn shutdown; a
host-owned finish phase stops the exact API pair while collector and runner
remain alive. The runner must verify actual final receipts/status before exit.
This phase applies only to the nominal case, never the preceding unit runner.
It changes no production startup or public route. Captured task completion alone
is insufficient because Uvicorn can record an error internally; real failure
flags and process exits must also be checked. Only the stdlib codec and rejection-test package is released to a builder.
Capability provisioning and server/host integration still require root review
and supported execution; no runtime closure has been accepted.

At complete observation, each role's started/finished SDK-request counts must
equal its actual SDK-kind collector record count. Nominal receipt totals are
SDK count plus exactly one ready and one closed record; otherwise a missing
request receipt could satisfy aggregate drain equations. Fixture arm metadata
cannot fabricate authority or actual child identity. Separate ingestion and
runner control capabilities prevent the case client from manufacturing producer
receipts. Exact file ownership/mount custody, including UID-1000 runner access,
remain required construction/runtime gates rather than assumed readability.
