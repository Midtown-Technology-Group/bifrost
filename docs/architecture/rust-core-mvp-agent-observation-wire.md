# C1-R private observation contract: architect disposition

This is a test-only reference contract, not the Python/Rust execution protocol
or a public authentication contract. It supports the unchanged agent/workflow
reference described in [C1-R](rust-core-mvp-agent-reference.md).

Architect decision: freeze the eleven JSON families, canonical serialization,
stateless semantics and shared constants below for **C1-R-CODEC only**. Accept
MAX_ROLE_ACTIVE_CAPTURES=32 and the stated shared phase/deadline/poll limits as
finite test-lane failure bounds. They neither extend product timeouts nor waive
failed execution. Consumers must use the same module; independently copied
validators/constants are forbidden. Stateful sequence/nonce/association checks,
signature verification, actual UID/mount construction, application/server
lifecycle and host coordination remain integration responsibilities and gates.
Structural validation alone is never evidence of any of those properties.

Current documentation base includes platform main
`e58db4955ddd30177bd613f1d85b7e203ad7832a` (#1027); the source proposal and
local reference foundation below retain their explicit older `f770094e` pin.
New handoff readback is outside this pure codec package. No evidence is
transferred across those source identities.

Reviewed source candidate SHA256 is
`961bfd5d4e9f6f0517b7e2177fabacfbd64f569cc3272e398dc7215dfd024c3f`.
The retained proposal below includes historical source-review dispositions;
this architect paragraph authorizes only the pure codec package. No Docker,
pytest, product imports, credentials, vendor calls, push, merge or deployment
was performed to accept this interface. Supported execution remains required.

C1-R-CODEC owns only `api/scripts/agent_reference_contract.py` and
`api/tests/unit/test_agent_reference_contract.py`. Inputs: this contract and
C1-R foundation `7b24f9d21d0849188bcc2c94d75b67d63a6e75a1`.
Implement the shared stdlib-only helpers and finite constants. Preserve all
closed key/type/byte/enum/conditional rules and local semantic constraints.
Do not introduce I/O, clock/random calls, auth/product imports, a schema
framework, dependency changes, runtime state or consumers. Include positive
fixtures for every family and adversarial canonicalization, exact-key/type,
byte-limit and semantic mutations; each must exercise a public codec helper.

Static commands: scoped Ruff check/format, AST parsing and `git diff --check`.
Executed acceptance, only on supported CI or a verified dedicated test VM:
`./test.sh tests/unit/test_agent_reference_contract.py -v` and ordinary API
quality/CI plus clean-current-main `./test.sh pre-pr` before publication.
Source tests may be written but must not be executed on the physical host.
Stop/report contradictory semantics, undefined enum/field/cap, a need for
product/imported auth or I/O, or any dependency/security authority expansion.
Do not resolve ambiguity by coercion, defaults, skipped tests or invented claims.
Root reviews the module independently before consumers are implemented.

The shared helpers take exactly the family names below. Enforce the selected
byte cap before parsing and after encoding; no fallback family or guessed cap.

| Family | Shared byte cap |
| --- | --- |
| `receipt` | MAX_SDK_RECEIPT_BYTES (8192) |
| `ack` | MAX_ACK_BYTES (1024) |
| `status` | MAX_STATUS_BYTES (2048) |
| `arm` | MAX_CONTROL_BYTES (4096) |
| `bind` | MAX_CONTROL_BYTES (4096) |
| `close` | MAX_CONTROL_BYTES (4096) |
| `control_ack` | MAX_CONTROL_BYTES (4096) |
| `readback` | MAX_READBACK_BYTES (65536) |
| `finish_request` | MAX_HOST_STATUS_BYTES (4096) |
| `host_status` | MAX_HOST_STATUS_BYTES (4096) |
| `error` | MAX_ACK_BYTES (1024) |

`ContractError.code` contains only a frozen private-error enum value; its string
representation is that value, with no payload or parser/validator exception
text. Unknown family and structural/stateless semantic violations use
`invalid_schema`; wrong raw-byte type, JSON syntax, duplicate keys, non-finite
or any JSON floating-point/exponent literal (including `1.0`, `1e0` and
`-0.0`), UTF-8/BOM or noncanonical wire use `invalid_json`;
selected byte-cap excess uses `capacity_exhausted`. Encoder rejects unsupported
Python values, including all Python floats, via `invalid_schema`. Live `invalid_state`, identity/sequence
failure and unauthorized decisions belong to consumers, not this validator.
Errors must not carry the offending value in exception arguments or chained
parser exceptions. Helpers raise no framework-specific validation exception.

Stateless edge dispositions: unsaturated counter equations apply to failed as
well as successful reports. A counter-equation exemption is diagnostic-only,
requires a relevant counter at 2147483647 and an enclosing non-null
`first_failure`, and never permits nominal acceptance. Do not overwrite an
earlier latched failure with `counter_exhausted`. Generation/counter saturation
also forbids successful closure. Failed closed status/receipt representations
are allowed as diagnostics; success-only prior/drain/ACK constraints apply when
`first_failure` is null. Neither representation proves actual lifecycle/ACKs.

For a readback snapshot, require `offset <= total`, record count exactly
`min(4, total-offset)`, and `next_cursor = offset+record_count` when that value
is below total, otherwise null. A live null cursor denotes only the current
snapshot tail. Only final readback after both real closed ACKs may claim an
immutable tail; consumers prove that separately. Filtered case offsets do not
replace each record's original global ledger index. In every case snapshot,
`sdk_receipt_count <= request_count`: the request count includes all SDK-kind
records plus model/Cove attempts, including invalid attempts. This local bound
also applies to bounded failure diagnostics; it is not a substitute for the
independent observed-count join.

Saturation exemptions are per equation: only R/Q/E saturation may exempt
`R=Q+E`; only Q/A/F saturation may exempt `Q=A+F+P+T`; only S/D/I saturation
may exempt `S=D+I`, each with enclosing non-null failure. Saturating another
counter cannot hide unrelated inconsistency. Always enforce `N=min(R,128)`,
`C<=E` and `last_ack_seq<=N`, even on saturated diagnostics.

The global `entry.index` is the zero-based admitted-ledger ordinal; rejected
admissions create no row. Lane readback is unfiltered, so every record index
must equal `offset+position`. Case readback retains possibly noncontiguous
global indices; its filtered offset must not be substituted for those indices.

Local lane snapshots require non-null `active_case_id` to imply `case_count>=1`
and `roles_closed` to be a subset of `roles_ready`. These relationships do not
certify actual collector readiness, nonce history or permission for more cases.

SDK failure lists must agree with their retained safe projections: the six
auth-reason categories contain exactly `verification.reason`, or none when that
reason is null; for the selected SDK path the three body-reason categories
contain exactly `request.reason`, or none when null. Multiple raw input defects
are represented by the single recorded verification/body cause, not additional
unwitnessed cause assertions. This private reporting rule changes no application
authentication, request handling or error precedence. The following categories
are present **if and only if** the corresponding retained fact holds:

| Category | Retained fact |
| --- | --- |
| `method_unexpected` | method differs from POST |
| `query_present` | query_present is true |
| `app_exception` | response.app_exception is non-null |
| `disconnect_seen` | response.disconnect_seen is true |
| `response_incomplete` | response.complete is false |
| `response_oversized` | response.bytes exceeds 65536 |
| `unexpected_sdk_path` | receipt kind is unexpected_sdk_path |

Unknown-path receipts have no body witness; absence of that field cannot be
used to invent a body-cause exclusion. Other internal/stateful categories remain
consumer obligations. This validator certifies projection consistency only;
the independent ASGI/source/collector tests must prove facts were observed.

---

# C1-R closed private observation wire contract v1 (source-only)

Disposition: **closed source-only candidate for independent root review, including narrow test-only server and host lifecycle coordination; no implementation authorized by this document.** The original stock `uvicorn --factory` shutdown-closure STOP remains valid and is preserved below. Root subsequently selected a shim design for source review rather than weakening closure claims. This supplement to `/tmp/bifrost-agent-reference-observation-seam.md` (SHA256 `42b7a65502bfd12e1a72e49c1f4ce5732c016bc29ad59a174b2813bd990ec1c2`) supersedes its provisional shutdown mechanism, abbreviated field names and counters; it does not change its product/event/auth exclusions. Exact worktree is `/home/thomas/src/bifrost-agent-capacity-reference`; inspected asset commit `9a53f516376ed1fda952fe732289b8f4c000cd73` adds only the retained fixtures to approved unmerged AUTH composite `60da4685f9515649b03dee00d44b1386475dcc84` on main `f770094eb28d8315a414fe8cb306f510da752d89`. The product-source diff 60da→9a53 is empty. Asset provenance SHA256 is `8e88c4972b5b578169cbb7df319ab21bbbd851523a7c9dcac27ad6aa533032fb`; declaration and five Python assets match that manifest, including the 219-byte fixture bootstrap. BOTH authored tools remain unchanged. Package metadata is bifrost-sdk 1.0.0; installed image/interpreter/API/SDK versions remain runtime gates. Guidance already read remains mtg-engineering-flow 2026-09-30.1, router 2026-10-01.1 and selected design helper from package 2026-10-01.5.

## Shared module and serialization

ONE future stdlib-only shared module is `api/scripts/agent_reference_contract.py`, imported as `scripts.agent_reference_contract` by wrapper, fixture and case client. Current API/replica mounts do not supply `/app/scripts` or `/app/tests`, and Dockerfile.dev does not bake them. Root integration must add narrowly selected read-only script/test mounts for those two API processes and independently verify actual targets/source/permissions before the shim can run; a bind allowlist alone is not an installed mount. Existing runner/fixture script mounts do not establish API availability. Root designates one contract owner; consumers must not copy constants/validators. It holds frozen enums/caps, strict family validators and encode/decode helpers; it imports no product/auth/SDK/ORM or authored assets. The wrapper separately calls existing `src.core.security.decode_token`; contract validation is not authentication. No JSON Schema dependency is introduced. The JSON Schema below plus its explicitly stated semantic constraints are the contract; a small stdlib validator may implement them directly.

Every private JSON body/file is exactly UTF-8 without BOM, using `json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))` and no trailing newline. All permitted strings below are ASCII. For private POST, inspect the raw header list before reading a body: exactly ONE capability header, ONE Content-Type with value exactly application/json and ONE canonical decimal Content-Length within endpoint cap; duplicates are rejected even when values agree. ANY Transfer-Encoding or Content-Encoding is rejected before body read. Require the declared number of bytes, no trailing bytes, and reject duplicate object keys, non-finite constants, noninteger numbers, invalid UTF-8/BOM and noncanonical re-encoding. Private GET requires exactly one capability header, absent or exactly one Content-Length: 0, no body, no Content-Type, Transfer-Encoding or Content-Encoding; duplicate capability/length/type headers are rejected. No folded/comma-joined capability is accepted. Header names are case-insensitive; selected credential value is compared without normalization/echo. Invalid headers yield bounded private error, not another parsed representation. This canonical test wire does not replace observed SDK/model/Cove serialization. JSON integer validation excludes bool. Explicit null is accepted only where the schema permits it; no omitted/extra keys/default coercion/aliases.

Shared caps: MAX_CASES=32; MAX_REQUESTS_PER_CASE=64 (model/Cove + SDK, invalid included); MAX_FIXTURE_INPUT_BYTES=65536; MAX_SDK_BODY_BYTES=4096; MAX_AUTH_HEADER_BYTES=8192; MAX_SELECTED_RESPONSE_BYTES=65536; MAX_SDK_RECEIPT_BYTES=8192; MAX_SDK_RECEIPTS_PER_CASE=32 across roles; MAX_EVENT_RECEIPTS_PER_CASE=64; MAX_PREBIND_EVENTS=64 consuming that same event budget; MAX_EVENT_BYTES=65536; MAX_ROLE_QUEUE=32; MAX_ROLE_RECEIPTS_PER_LANE=128 including lifecycle/failure; MAX_ACK_BYTES=1024; MAX_STATUS_BYTES=2048; MAX_CONTROL_BYTES=4096; MAX_READBACK_BYTES=65536; READBACK_PAGE_SIZE=4; OBSERVER_CLOSE_SECONDS=2; MAX_PRIVATE_TRANSPORT_STAGE_SECONDS=1. Proposed explicit retention bound for root review: MAX_ROLE_ACTIVE_CAPTURES=32, separate from32 queued receipts. No eviction, reuse, reset, wrap, auto resend or implicit next case. Per-role whole-lane cap can STOP before case cap. Extra case allocation returns 503 and permanently fails the lane.

Sequence is role-local, starts at **1**, ready is 1, and increments at every receipt creation attempt, before queue admission. At most 128 materialized receipt identities exist per role; the 129th creation attempt records a capacity rejection in status without allocating/reusing a sequence. No global causal ordering follows from role-local sequence. Nonce and lane_id are 32 lowercase hex. UUIDs use canonical lowercase hyphenated form. `mono_ns`/`started_ns`/collector `received_ns` are actual `time.monotonic_ns()` values encoded as canonical unsigned decimal STRINGS, <=9223372036854775807. They are not wall-clock/commit time, and different process clocks are never compared or normalized. Counter bounds are 0..2147483647; overflow saturates at that bound and sets counter_exhausted, never wraps or counts as acceptance.

## Normative closed JSON Schema

This schema describes ALL private families. All objects have additionalProperties=false and every listed property required. Conditional rules following the schema are mandatory; validators must not use this schema's structural pass as acceptance by itself.

```json
{
  "$schema":"https://json-schema.org/draft/2020-12/schema",
  "$id":"bifrost.agent-reference.closed-wire/v1",
  "oneOf":[{"$ref":"#/$defs/receipt"},{"$ref":"#/$defs/ack"},{"$ref":"#/$defs/status"},{"$ref":"#/$defs/arm"},{"$ref":"#/$defs/bind"},{"$ref":"#/$defs/close"},{"$ref":"#/$defs/control_ack"},{"$ref":"#/$defs/readback"},{"$ref":"#/$defs/finish_request"},{"$ref":"#/$defs/host_status"},{"$ref":"#/$defs/error"}],
  "$defs":{
    "hex32":{"type":"string","pattern":"^[0-9a-f]{32}$"},
    "uuid":{"type":"string","pattern":"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"},
    "nullable_uuid":{"anyOf":[{"$ref":"#/$defs/uuid"},{"type":"null"}]},
    "sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
    "docker_image_id":{"type":"string","pattern":"^sha256:[0-9a-f]{64}$"},
    "mono":{"type":"string","pattern":"^(0|[1-9][0-9]{0,18})$"},
    "count":{"type":"integer","minimum":0,"maximum":2147483647},
    "role":{"enum":["api","api-replica"]},
    "seq":{"type":"integer","minimum":1,"maximum":128},
    "method":{"enum":["GET","HEAD","POST","PUT","PATCH","DELETE","OPTIONS","TRACE","CONNECT","OTHER"]},
    "phase":{"enum":["startup","request","transport","status","shutdown"]},
    "code":{"enum":["auth_missing","auth_duplicate","auth_oversized","auth_malformed","jwt_rejected","claims_schema","body_incomplete","body_oversized","body_invalid","method_unexpected","query_present","app_exception","disconnect_seen","response_incomplete","response_oversized","unexpected_sdk_path","observer_exception","queue_exhausted","receipt_exhausted","transport_failure","ack_invalid","status_io_failure","shutdown_incomplete","counter_exhausted","collector_capacity","unbound_sdk","cross_case_sdk","extra_run","case_aborted","control_invalid","event_invalid","event_missing","event_overflow","correlation_failed","product_not_settled","late_witness"]},
    "nullable_code":{"anyOf":[{"$ref":"#/$defs/code"},{"type":"null"}]},
    "failures":{"type":"array","items":{"$ref":"#/$defs/code"},"uniqueItems":true,"maxItems":36},
    "claims":{
      "type":"object","additionalProperties":false,
      "required":["sub","engine_execution_id","engine_solution_id","org_id","delegated_user_id","engine","is_superuser","engine_global_repo_access","delegated_is_superuser","delegated_is_provider_org","delegated_is_external"],
      "properties":{
        "sub":{"$ref":"#/$defs/uuid"},"engine_execution_id":{"$ref":"#/$defs/nullable_uuid"},"engine_solution_id":{"$ref":"#/$defs/nullable_uuid"},"org_id":{"$ref":"#/$defs/nullable_uuid"},"delegated_user_id":{"$ref":"#/$defs/nullable_uuid"},
        "engine":{"type":["boolean","null"]},"is_superuser":{"type":["boolean","null"]},"engine_global_repo_access":{"type":["boolean","null"]},"delegated_is_superuser":{"type":["boolean","null"]},"delegated_is_provider_org":{"type":["boolean","null"]},"delegated_is_external":{"type":["boolean","null"]}
      }
    },
    "verification":{
      "type":"object","additionalProperties":false,"required":["outcome","reason","claims"],
      "properties":{"outcome":{"enum":["verified","unverified"]},"reason":{"anyOf":[{"enum":["auth_missing","auth_duplicate","auth_oversized","auth_malformed","jwt_rejected","claims_schema"]},{"type":"null"}]},"claims":{"anyOf":[{"$ref":"#/$defs/claims"},{"type":"null"}]}},
      "allOf":[{"if":{"properties":{"outcome":{"const":"verified"}}},"then":{"properties":{"reason":{"type":"null"},"claims":{"$ref":"#/$defs/claims"}}},"else":{"properties":{"reason":{"enum":["auth_missing","auth_duplicate","auth_oversized","auth_malformed","jwt_rejected","claims_schema"]},"claims":{"type":"null"}}}}]
    },
    "sdk_body":{
      "type":"object","additionalProperties":false,"required":["name","scope","solution"],
      "properties":{"name":{"const":"Cove Data Protection"},"scope":{"const":"global"},"solution":{"$ref":"#/$defs/uuid"}}
    },
    "request_witness":{
      "type":"object","additionalProperties":false,"required":["complete","bytes","value","reason"],
      "properties":{"complete":{"type":"boolean"},"bytes":{"type":"integer","minimum":0,"maximum":4097},"value":{"anyOf":[{"$ref":"#/$defs/sdk_body"},{"type":"null"}]},"reason":{"anyOf":[{"enum":["body_incomplete","body_oversized","body_invalid"]},{"type":"null"}]}},
      "allOf":[{"if":{"properties":{"reason":{"type":"null"}}},"then":{"properties":{"complete":{"const":true},"bytes":{"maximum":4096},"value":{"$ref":"#/$defs/sdk_body"}}},"else":{"properties":{"value":{"type":"null"}}}}]
    },
    "response_witness":{
      "type":"object","additionalProperties":false,"required":["status","bytes","complete","disconnect_seen","app_exception"],
      "properties":{"status":{"anyOf":[{"type":"integer","minimum":100,"maximum":599},{"type":"null"}]},"bytes":{"type":"integer","minimum":0,"maximum":65537},"complete":{"type":"boolean"},"disconnect_seen":{"type":"boolean"},"app_exception":{"enum":[null,"exception","cancelled"]}}
    },
    "ready_payload":{"type":"object","additionalProperties":false,"required":["startup_forwarded"],"properties":{"startup_forwarded":{"const":true}}},
    "sdk_payload":{
      "type":"object","additionalProperties":false,"required":["started_ns","method","path","query_present","request","verification","response","failures"],
      "properties":{"started_ns":{"$ref":"#/$defs/mono"},"method":{"$ref":"#/$defs/method"},"path":{"const":"/api/sdk/integrations/get"},"query_present":{"type":"boolean"},"request":{"$ref":"#/$defs/request_witness"},"verification":{"$ref":"#/$defs/verification"},"response":{"$ref":"#/$defs/response_witness"},"failures":{"$ref":"#/$defs/failures"}}
    },
    "unexpected_payload":{
      "type":"object","additionalProperties":false,"required":["started_ns","method","query_present","verification","response","failures"],
      "properties":{"started_ns":{"$ref":"#/$defs/mono"},"method":{"$ref":"#/$defs/method"},"query_present":{"type":"boolean"},"verification":{"$ref":"#/$defs/verification"},"response":{"$ref":"#/$defs/response_witness"},"failures":{"$ref":"#/$defs/failures"}}
    },
    "failure_payload":{"type":"object","additionalProperties":false,"required":["phase","code"],"properties":{"phase":{"$ref":"#/$defs/phase"},"code":{"$ref":"#/$defs/code"}}},
    "counters":{
      "type":"object","additionalProperties":false,
      "required":["receipts_seen","queued","enqueue_rejected","acknowledged","send_failed","queue_pending","sending","requests_started","requests_finished","requests_in_flight","seq_allocated","last_ack_seq","capacity_rejected"],
      "properties":{"receipts_seen":{"$ref":"#/$defs/count"},"queued":{"$ref":"#/$defs/count"},"enqueue_rejected":{"$ref":"#/$defs/count"},"acknowledged":{"$ref":"#/$defs/count"},"send_failed":{"$ref":"#/$defs/count"},"queue_pending":{"type":"integer","minimum":0,"maximum":32},"sending":{"type":"integer","minimum":0,"maximum":1},"requests_started":{"$ref":"#/$defs/count"},"requests_finished":{"$ref":"#/$defs/count"},"requests_in_flight":{"$ref":"#/$defs/count"},"seq_allocated":{"type":"integer","minimum":0,"maximum":128},"last_ack_seq":{"type":"integer","minimum":0,"maximum":128},"capacity_rejected":{"$ref":"#/$defs/count"}}
    },
    "closed_payload":{
      "type":"object","additionalProperties":false,"required":["shutdown_forwarded","prior","first_failure"],
      "properties":{"shutdown_forwarded":{"type":"boolean"},"prior":{"$ref":"#/$defs/counters"},"first_failure":{"$ref":"#/$defs/nullable_code"}}
    },
    "receipt":{
      "type":"object","additionalProperties":false,"required":["schema","lane_id","role","nonce","seq","mono_ns","kind","payload"],
      "properties":{"schema":{"const":"bifrost.agent-reference.sdk-observation/v1"},"lane_id":{"$ref":"#/$defs/hex32"},"role":{"$ref":"#/$defs/role"},"nonce":{"$ref":"#/$defs/hex32"},"seq":{"$ref":"#/$defs/seq"},"mono_ns":{"$ref":"#/$defs/mono"},"kind":{"enum":["ready","sdk_request","unexpected_sdk_path","failure","closed"]},"payload":{"type":"object"}},
      "allOf":[
        {"if":{"properties":{"kind":{"const":"ready"}}},"then":{"properties":{"seq":{"const":1},"payload":{"$ref":"#/$defs/ready_payload"}}}},
        {"if":{"properties":{"kind":{"const":"sdk_request"}}},"then":{"properties":{"payload":{"$ref":"#/$defs/sdk_payload"}}}},
        {"if":{"properties":{"kind":{"const":"unexpected_sdk_path"}}},"then":{"properties":{"payload":{"$ref":"#/$defs/unexpected_payload"}}}},
        {"if":{"properties":{"kind":{"const":"failure"}}},"then":{"properties":{"payload":{"$ref":"#/$defs/failure_payload"}}}},
        {"if":{"properties":{"kind":{"const":"closed"}}},"then":{"properties":{"payload":{"$ref":"#/$defs/closed_payload"}}}}
      ]
    },
    "ack":{
      "type":"object","additionalProperties":false,"required":["schema","lane_id","role","nonce","seq","accepted","case_id"],
      "properties":{"schema":{"const":"bifrost.agent-reference.observer-ack/v1"},"lane_id":{"$ref":"#/$defs/hex32"},"role":{"$ref":"#/$defs/role"},"nonce":{"$ref":"#/$defs/hex32"},"seq":{"$ref":"#/$defs/seq"},"accepted":{"const":true},"case_id":{"anyOf":[{"$ref":"#/$defs/hex32"},{"type":"null"}]}}
    },
    "upstream":{"type":"object","additionalProperties":false,"required":["startup_forwarded","shutdown_forwarded"],"properties":{"startup_forwarded":{"type":"boolean"},"shutdown_forwarded":{"type":"boolean"}}},
    "status":{
      "type":"object","additionalProperties":false,"required":["schema","lane_id","role","nonce","generation","mono_ns","phase","counters","upstream","ready_acked","closed_acked","first_failure"],
      "properties":{"schema":{"const":"bifrost.agent-reference.observer-status/v1"},"lane_id":{"$ref":"#/$defs/hex32"},"role":{"$ref":"#/$defs/role"},"nonce":{"$ref":"#/$defs/hex32"},"generation":{"$ref":"#/$defs/count"},"mono_ns":{"$ref":"#/$defs/mono"},"phase":{"enum":["starting","active","stopping","closed"]},"counters":{"$ref":"#/$defs/counters"},"upstream":{"$ref":"#/$defs/upstream"},"ready_acked":{"type":"boolean"},"closed_acked":{"type":"boolean"},"first_failure":{"$ref":"#/$defs/nullable_code"}}
    },
    "arm":{
      "type":"object","additionalProperties":false,"required":["schema","lane_id","case_id","mode","solution_id","deployment_id","agent_id","declaration_sha256"],
      "properties":{"schema":{"const":"bifrost.agent-reference.case-arm/v1"},"lane_id":{"$ref":"#/$defs/hex32"},"case_id":{"$ref":"#/$defs/hex32"},"mode":{"const":"nominal-capacity"},"solution_id":{"$ref":"#/$defs/uuid"},"deployment_id":{"$ref":"#/$defs/uuid"},"agent_id":{"$ref":"#/$defs/uuid"},"declaration_sha256":{"const":"b79b5064f375098dabc119083cf2ff16778b683722b5df394f6cb8a06ca22914"}}
    },
    "bind":{"type":"object","additionalProperties":false,"required":["schema","lane_id","case_id","run_id"],"properties":{"schema":{"const":"bifrost.agent-reference.case-bind/v1"},"lane_id":{"$ref":"#/$defs/hex32"},"case_id":{"$ref":"#/$defs/hex32"},"run_id":{"$ref":"#/$defs/uuid"}}},
    "close":{"type":"object","additionalProperties":false,"required":["schema","lane_id","case_id","run_id","finish_id","disposition"],"properties":{"schema":{"const":"bifrost.agent-reference.case-close/v1"},"lane_id":{"$ref":"#/$defs/hex32"},"case_id":{"$ref":"#/$defs/hex32"},"run_id":{"$ref":"#/$defs/nullable_uuid"},"finish_id":{"$ref":"#/$defs/hex32"},"disposition":{"enum":["finish","abort"]}}},
    "control_ack":{
      "type":"object","additionalProperties":false,"required":["schema","lane_id","case_id","operation","state","run_id","finish_id","first_failure"],
      "properties":{"schema":{"const":"bifrost.agent-reference.case-control-ack/v1"},"lane_id":{"$ref":"#/$defs/hex32"},"case_id":{"$ref":"#/$defs/hex32"},"operation":{"enum":["arm","bind","close"]},"state":{"enum":["armed","bound","closing","closed"]},"run_id":{"$ref":"#/$defs/nullable_uuid"},"finish_id":{"anyOf":[{"$ref":"#/$defs/hex32"},{"type":"null"}]},"first_failure":{"$ref":"#/$defs/nullable_code"}}
    },
    "entry":{
      "type":"object","additionalProperties":false,"required":["index","received_ns","case_id","association","receipt"],
      "properties":{"index":{"type":"integer","minimum":0,"maximum":255},"received_ns":{"$ref":"#/$defs/mono"},"case_id":{"anyOf":[{"$ref":"#/$defs/hex32"},{"type":"null"}]},"association":{"enum":["lifecycle","active-arm-pending-db","unbound","late"]},"receipt":{"$ref":"#/$defs/receipt"}}
    },
    "case_state":{
      "type":"object","additionalProperties":false,"required":["case_id","state","mode","solution_id","deployment_id","agent_id","run_id","finish_id","request_count","sdk_receipt_count","first_failure"],
      "properties":{"case_id":{"$ref":"#/$defs/hex32"},"state":{"enum":["armed","bound","closing","closed"]},"mode":{"const":"nominal-capacity"},"solution_id":{"$ref":"#/$defs/uuid"},"deployment_id":{"$ref":"#/$defs/uuid"},"agent_id":{"$ref":"#/$defs/uuid"},"run_id":{"$ref":"#/$defs/nullable_uuid"},"finish_id":{"anyOf":[{"$ref":"#/$defs/hex32"},{"type":"null"}]},"request_count":{"type":"integer","minimum":0,"maximum":65},"sdk_receipt_count":{"type":"integer","minimum":0,"maximum":33},"first_failure":{"$ref":"#/$defs/nullable_code"}}
    },
    "lane_state":{
      "type":"object","additionalProperties":false,"required":["case_count","active_case_id","roles_ready","roles_closed","first_failure"],
      "properties":{"case_count":{"type":"integer","minimum":0,"maximum":32},"active_case_id":{"anyOf":[{"$ref":"#/$defs/hex32"},{"type":"null"}]},"roles_ready":{"type":"array","items":{"$ref":"#/$defs/role"},"uniqueItems":true,"maxItems":2},"roles_closed":{"type":"array","items":{"$ref":"#/$defs/role"},"uniqueItems":true,"maxItems":2},"first_failure":{"$ref":"#/$defs/nullable_code"}}
    },
    "readback":{
      "type":"object","additionalProperties":false,"required":["schema","lane_id","scope","case_id","offset","next_cursor","total","records","case","lane"],
      "properties":{"schema":{"const":"bifrost.agent-reference.observer-readback/v1"},"lane_id":{"$ref":"#/$defs/hex32"},"scope":{"enum":["lane","case"]},"case_id":{"anyOf":[{"$ref":"#/$defs/hex32"},{"type":"null"}]},"offset":{"type":"integer","minimum":0,"maximum":256},"next_cursor":{"anyOf":[{"type":"integer","minimum":1,"maximum":256},{"type":"null"}]},"total":{"type":"integer","minimum":0,"maximum":256},"records":{"type":"array","items":{"$ref":"#/$defs/entry"},"maxItems":4},"case":{"anyOf":[{"$ref":"#/$defs/case_state"},{"type":"null"}]},"lane":{"$ref":"#/$defs/lane_state"}}
    },
    "role_identity":{"type":"object","additionalProperties":false,"required":["role","nonce"],"properties":{"role":{"$ref":"#/$defs/role"},"nonce":{"$ref":"#/$defs/hex32"}}},
    "finish_request":{
      "type":"object","additionalProperties":false,"required":["schema","lane_id","case_id","run_id","finish_id","disposition","state","roles"],
      "properties":{"schema":{"const":"bifrost.agent-reference.finish-request/v1"},"lane_id":{"$ref":"#/$defs/hex32"},"case_id":{"anyOf":[{"$ref":"#/$defs/hex32"},{"type":"null"}]},"run_id":{"$ref":"#/$defs/nullable_uuid"},"finish_id":{"anyOf":[{"$ref":"#/$defs/hex32"},{"type":"null"}]},"disposition":{"enum":[null,"finish","abort"]},"state":{"enum":[null,"armed","bound","closing","closed"]},"roles":{"type":"array","items":{"$ref":"#/$defs/role_identity"},"maxItems":2}}
    },
    "host_role":{
      "type":"object","additionalProperties":false,"required":["role","container_id","image_id","observer_nonce","stop_requested","exit_observed","exit_code"],
      "properties":{"role":{"$ref":"#/$defs/role"},"container_id":{"$ref":"#/$defs/sha256"},"image_id":{"$ref":"#/$defs/docker_image_id"},"observer_nonce":{"anyOf":[{"$ref":"#/$defs/hex32"},{"type":"null"}]},"stop_requested":{"type":"boolean"},"exit_observed":{"type":"boolean"},"exit_code":{"anyOf":[{"type":"integer","minimum":0,"maximum":255},{"type":"null"}]}}
    },
    "host_status":{
      "type":"object","additionalProperties":false,"required":["schema","lane_id","runner_container_id","case_id","run_id","finish_id","generation","mono_ns","phase","roles","first_failure","failure_stage"],
      "properties":{"schema":{"const":"bifrost.agent-reference.host-status/v1"},"lane_id":{"$ref":"#/$defs/hex32"},"runner_container_id":{"$ref":"#/$defs/sha256"},"case_id":{"anyOf":[{"$ref":"#/$defs/hex32"},{"type":"null"}]},"run_id":{"$ref":"#/$defs/nullable_uuid"},"finish_id":{"anyOf":[{"$ref":"#/$defs/hex32"},{"type":"null"}]},"generation":{"$ref":"#/$defs/count"},"mono_ns":{"$ref":"#/$defs/mono"},"phase":{"enum":["watching","stop_requested","exited","failed"]},"roles":{"type":"array","items":{"$ref":"#/$defs/host_role"},"minItems":2,"maxItems":2},"first_failure":{"$ref":"#/$defs/nullable_code"},"failure_stage":{"enum":[null,"poll","identity","runner","api-stop","api-exit","status"]}}
    },
    "error":{
      "type":"object","additionalProperties":false,"required":["schema","error"],
      "properties":{"schema":{"const":"bifrost.agent-reference.private-error/v1"},"error":{"enum":["unauthorized","invalid_json","invalid_schema","invalid_state","unknown_case","capacity_exhausted","identity_mismatch","sequence_mismatch","unbound_observation","internal_failure"]}}
    }
  }
}
```

Schema strings are discriminators, not public API contracts. All family/byte caps apply before parsing/retention. Counter equations, actual signature verification, state/sequence checks and source/result/DB joins are semantic rules outside JSON Schema. Closed/terminal status requires the separately reviewed test-only server/host lifecycle below; stock command is still blocked. Field names are frozen in this candidate: earlier `request_seen` becomes requests_started; `receipts_seen` is creation attempts, not acknowledged/queued; optional wire/token SHA256 fields are REMOVED. Request safe value is not raw wire bytes; actual body is parsed from the tee and then discarded. Response retained/hash bytes are ZERO. Arm catalog_sha256/profile_sha256 are REMOVED because their preimages were undefined. Host container IDs are bare64 lowercase hex; image IDs are EXACT actual Docker sha256:<64lowerhex> with no stripping/aliases. They are actual readbacks, not observer-generated content/security hashes. Event receipts stay in the independent Redis case observer, not a fabricated event_receipt_count in SDK fixture readback. Shared MAX_EVENT caps apply there separately.

## Witness semantics

`verification.outcome=verified` means existing decode_token(access) returned a signed/issuer/audience/expiry/type-valid payload and selected values were well typed, not that the real router converted that payload identically or validated a live lease. Missing permitted UUID/boolean claims become explicit null; an explicitly supplied malformed/wrong-typed selected claim makes unverified/claims_schema, with NO partial claims retained. `sub` must be canonical UUID to export. Decode returning None is only jwt_rejected: do not infer expired versus bad signature from it. No raw JWT/digest/header/email/name/role/attempt token or verifier exception is stored/logged/exported. Nominal independently requires engine=true, actual source engine subject, engine_execution_id/solution ID nonnull, and actual delegated caller fields; broad legacy transport is_superuser is not original user's privilege.

SDK body witness reason=null only when app actually consumed full body <=4096, strict JSON keys are exactly name/scope/solution and values validate sdk_body. Incomplete, oversized or schema/duplicate-key/UTF-8 errors get value=null and a static reason, no offending bytes/hash. bytes saturates at 4097. Any query bytes mean query_present, never retain query values. Allowed methods are encoded exactly; unknown HTTP method maps to OTHER without exporting raw text. All selected methods except POST add method_unexpected. Unknown SDK path variant retains no raw path/body but still verifies actual bearer for possible pending child correlation; failures MUST contain unexpected_sdk_path.

Response status is what actual `http.response.start` successfully forwarded; bytes count every actually forwarded body chunk and saturates at 65537. complete=true only after the final `http.response.body` with more_body false was successfully forwarded. disconnect_seen records only an actual `http.disconnect` observed by the app's receive; false does not prove no network disconnect. app_exception names only whether unchanged app/cancellation propagated, never its text. Receipt common mono_ns is actual completion/finalization instant; started_ns is actual selected request entry. Both are original clock values. Response bytes retained/hashed=ZERO. Capture exception/oversize NEVER changes original request/body/header/response/status/lifespan messages. Nominal additionally requires POST/no query/captured body/verified expected identity/actual HTTP200/complete response/no failure.

Failures arrays follow the declaration order of code enum and contain exactly detected categories, no invented cause. There are only 36 categories, so list cap36 is sufficient without truncation. first_failure latches the first actual error until teardown, never resets when a case is armed/closed. Failure receipt is at most ONE auxiliary diagnostic per role for first internal observer error where queue/transport remains usable; it is not mandatory on a broken channel. Enqueue/transport/status failures update private status; they NEVER recursively enqueue failure receipts or resend the failed record. A failed SDK request has its own sdk_request receipt when admission is possible; acceptance does not require duplicating it as failure.

## Counter equations and status

All counter updates/snapshots happen in one role-local event-loop owner; one writer serializes atomic fixed-schema status replacement at `/app/reference-observer-status/{api|api-replica}/status.json`. Worker/authored child has no mount. Runner is RO. Existing valid status never represents a later failed write as success: required terminal generation/closed acknowledgement is independently checked. generation starts0 and increments for each in-memory snapshot; missing/stale/unreadable snapshot, schema/permissions/path mismatch, writer error or counter saturation fails acceptance. No mutable dictionary is written concurrently; no older generation may replace a newer one. Status holds only latest complete state plus ONE immutable in-flight write, not an unbounded per-chunk/snapshot queue; generation gaps are allowed, counters/sticky failure never reset. This status mechanism does not evict receipt records. Final closure waits for the exact final generation's write/readback. File <=2048 bytes; atomic temporary sibling name is fixed per role, regular-file/no-symlink checked, then replaced. No request-supplied filenames.

Selected active capture retention is separately capped32/role before allocating body/header/verifier copies. Overflow ALWAYS forwards original app traffic, counts real S/I and later D, permanently records observer_exception, then accounts its failed receipt creation/admission R/N/E/C at finalization without fabricating body/claims/outcome fields. It cannot produce a successful SDK witness; missing record/gap/counters independently FAIL. Retained raw body prefix is <=4096 per active slot, temporary selected bearer <=8192 only until actual verification, safe projection bounded by receipt8192, response retained/hash bytes0; release temporary references on finalization. Scope/original message objects remain owned by actual server/app and are never placed in observer queues. This bounds EXTRA observer retention, not all product/Uvicorn allocations or a lossless network tee. If this additional32-slot bound is not accepted, bounded producer retention remains an explicit architecture STOP; per-case collector count alone cannot bound concurrent active captures before receipt admission.

Use R=receipts_seen, Q=queued, E=enqueue_rejected, A=acknowledged, F=send_failed, P=queue_pending, T=sending, S=requests_started, D=requests_finished, I=requests_in_flight, N=seq_allocated, C=capacity_rejected. At EVERY published status snapshot before saturation:

```text
R = Q + E
Q = A + F + P + T
S = D + I
0 <= P <= 32; T in {0,1}; 0 <= N <= 128
N = min(R,128)
0 <= C <= E
last_ack_seq <= N
```

Creation attempt increments R even if queue full/cap exhausted/schema serialization fails; it increments N only if N<128. Successful admission increments Q and P. Failed admission increments E; capacity errors additionally increment C and latch first_failure. Dequeue transfers one P→T atomically; successful matching HTTP201 ACK transfers T→A and advances last_ack_seq; any send/ack failure transfers T→F and latches failure. Every selected SDK request increments S/I at entry; finalization increments D and decrements I in a finally path even if receipt admission fails. Only lifecycle/auxiliary failure receipts affect R without affecting S/D. Cancellation of sender while in-flight transfers T→F; unsent queue remains P (never clear/evict it to report drain). Allocation saturation preserves status equations until count bound; after count saturation, equations may be unverifiable and counter_exhausted permanently forbids acceptance.

Collector accepts only the NEXT contiguous seq for a registered role/nonce and matching lane. Ready(seq1) registers role exactly once after startup forwarded. No resend/idempotent duplicate acceptance: duplicate/gap/nonce conflict is permanent failure, with bounded error reply. This intentionally makes failed enqueue visible as a gap if a later diagnostic arrives. HTTP201 means safe receipt was actually admitted once, not a DB commit/result. ACK is <=1024 bytes, matches schema/lane/role/nonce/seq and accepted=true; SDK ACK case_id must equal the actual active-arm association, while ready/failure/closed lifecycle ACK case_id=null. Sender validates syntax/identity, not a client-supplied expected case claim. Lost ACK is send failure even if collector retained record; do not resend.

Final complete role status requires phase=closed, both upstream flags=true, ready_acked=true, closed_acked=true, first_failure=null, I=0, E=F=P=T=C=0, R=Q=A=N=last_ack_seq<=128. Ready is first and closed is final. All receipt identities appear in complete collector lane readback exactly once. Independently, S=D equals the number of actual sdk_request plus unexpected_sdk_path records for that role/nonce in complete lane readback; nominal permits no auxiliary failure receipt and exactly one ready plus one closed, so R=S+2. A missing SDK record cannot pass by making receipt-channel counters alone agree. Every admitted SDK-kind record increments its associated case request_count once; fixture model/Cove attempts, including invalid attempts, increment it once separately at entry. Final case request_count equals case-associated SDK-kind records plus independently retained model/Cove admitted-and-invalid attempt counts. Saturation/unbound/late cases fail rather than erase attempts. sdk_receipt_count equals those case SDK-kind records and is <=32. These are observed-count completeness conditions for this bounded selected seam, NEVER a claim the tee was lossless for all network traffic, product effects or server allocations. Any missing final condition is acceptance FAIL, not a fabricated failure/success app response.

## Lifecycle: original stock-command STOP and reviewed shim candidate

Before entering actual app lifespan, initialize bounded observer sender/status writer and starting status. Forward EVERY incoming lifespan message and every app-generated acknowledgement unchanged and in original order; no observer readiness/closure operation runs before forwarding an app acknowledgement. Only after `await original_send(app_startup_complete)` returns successfully, mark startup_forwarded, create ready(seq1) and queue it. Real API health remains its actual health; the private test waits for BOTH ready ACKs/status before arming the first case. Startup.failed/raise/cancel forwards/propagates unchanged, latches shutdown_incomplete/observer_exception and cannot yield nominal ready/closure evidence.

On shutdown, do NOT gate the app's shutdown handler, rewrite/delay its acknowledgement or manufacture another message. Actual source `api/src/main.py:207–223,243–257` closes app/MCP resources normally; observer does not touch those resources. After `await original_send(app_shutdown_complete)` returns, mark shutdown_forwarded/stopping; asynchronous finalization has NO guaranteed window under stock Uvicorn.

Source proof: `requirements.lock:3426–3428` pins uvicorn[standard]0.46.0. Its [LifespanOn source](https://github.com/Kludex/uvicorn/blob/0.46.0/uvicorn/lifespan/on.py#L64) waits on shutdown_event, which send(shutdown.complete) sets; it does not await the lifespan task. [Server.run/shutdown](https://github.com/Kludex/uvicorn/blob/0.46.0/uvicorn/server.py#L74) returns through asyncio.run after that event. [CPython runner cleanup](https://github.com/python/cpython/blob/v3.14.7/Lib/asyncio/runners.py#L207) cancels remaining tasks. Inference from those sources: asynchronously posting/draining a closed receipt AFTER forwarding app_shutdown_complete races server/event-loop termination; merely assigning a2-second timeout cannot reserve that time. Local cached uvicorn0.46.0 source/METADATA agreed with the primary tag; this is source evidence, not proof of actual installed image/runtime.

This established the mandatory historical STOP before builders. The earlier assumed2-second post-ACK window was WITHDRAWN: timeout alone cannot keep the server loop alive. Root then authorized SOURCE REVIEW of a test-only server seam; that is the candidate below. Stock `uvicorn --factory tests.e2e.platform.agent_reference_observer:create_app ...` remains STOP even if it occasionally drains successfully. No pre-shutdown seal is labeled real shutdown closure.

Future owned shim path: `api/tests/e2e/platform/agent_reference_server.py`, observation builder only AFTER root scope review. Subclass only `uvicorn.Server.shutdown(self, sockets=None)`; no replacement of listeners/protocols/HTTP handling/loop/signal handlers/startup/main-loop/graceful-connection logic. Call `await super().shutdown(sockets=sockets)` first, untouched. Then retain the loop by awaiting the actual lifespan task for <=2 seconds. This is a narrow additional server wait AFTER upstream shutdown, not an app acknowledgement delay. The [pinned Server source](https://github.com/Kludex/uvicorn/blob/0.46.0/uvicorn/server.py#L74) provides run/shutdown; [pinned Config source](https://github.com/Kludex/uvicorn/blob/0.46.0/uvicorn/config.py#L438) loads the factory and protocol/middleware defaults. Installed version/source verification remains a root runtime gate, not established by the local cache.

The wrapper retains a strong reference to `asyncio.current_task()` exactly once on its actual lifespan-scope entry, before awaiting the unchanged app; that is Uvicorn's actual LifespanOn.main task, including normal middleware forwarding. Do NOT look up a nonexistent `main_lifespan_task` attribute: the pinned LifespanOn.startup uses a local variable. A no-argument factory closure in the server supplies exactly one observer instance to Config(factory=True), and retains it independently of Config.loaded_app's normal proxy wrapper. No task scanning/name inference, second app, duplicate lifespan invocation or LifespanOn subclass is needed. Missing task, duplicate lifespan/factory, wrong loop, same task as shutdown waiter, app raise/cancel, forwarded startup/shutdown failure, force_exit or absent actual forwarded shutdown.complete is shutdown_incomplete and FAIL.

Shared in-process lifecycle handshake, not a private HTTP contract: the wrapper's lifespan coroutine first awaits real app return and checks actual shutdown.complete forwarding; it then awaits a test-server closure-grant Event. No grant wait occurs inside the send wrapper or before any app acknowledgement. After super().shutdown returns, the server validates retained task/forwarded flags, sets one absolute closure deadline from loop.time()+2.0, releases the grant synchronously, and uses `asyncio.wait({actual_task}, timeout=remaining)` to await that exact task. Unlike wait_for, this wait does not silently cancel the task at expiry. Wrapper finalization drains its already admitted queue, admits closed last, waits for its actual matching ACK, writes/replaces and reads back final status, stops/joins its bounded sender/writer, then returns normally. All observer waits use the SAME deadline; each transport stage <=min(1 second,remaining), private request has overall remaining-deadline bound, and no per-record/queue budget restart. Grant/task wait requires no app/SDK/source change. Do not claim task completion alone: LifespanOn.main catches BaseException. AFTER awaiting that retained task, require the actual pinned LifespanOn instance's error_occurred, startup_failed and shutdown_failed ALL exactly False, plus observer closure result/final status readback/both forwarded flags/closed_acked. Missing/type-mismatched attributes or lifespan-class mismatch fails; NO getattr(...,False) defaults. Do NOT require server.should_exit=false: normal SIGTERM sets it true.

Before allocating closed, snapshot prior counters: prior.R=prior.Q=prior.A=prior.N=seq-1, prior.E/F/P/T/C/I=0, all started requests finalized and real shutdown acknowledgement forwarded. Closed payload covers PRIOR drain only; its actual ACK/final status account for closed itself. Nominal prior.S=prior.D=actual SDK-kind record count. If prior state fails, there is no successful closed record: preserve queue/counters/sticky failure. Late SDK work during stopping also fails. On deadline expiry or missing/failed closure result, cancel the retained task once where still pending, latch shutdown_incomplete where possible, and raise one static harness-only ObservationClosureError. Entry-point catches only that dedicated exception for fixed value-free diagnostic and nonzero exit; do not print observer/product exceptions or turn them into app HTTP/lifespan responses. Original app exceptions/cancellation still propagate through the unchanged ASGI chain; observer never swallows/uncancels them. Unsent P remains, in-flight cancelled T becomes F, never evict/auto resend. Missing status is FAIL even when a best-effort failure write cannot finish.

Inherit Server.run, therefore the pinned Config.get_loop_factory/auto loop choice; do NOT replace it with bare asyncio.run or removed setup_event_loop. Fixed future lane argv, BOTH API roles only: `["coverage","run","--parallel-mode","-m","tests.e2e.platform.agent_reference_server","--host","0.0.0.0","--port","8000"]`. Compare to actual default `docker-compose.test.yml:210`; root changes only named-lane override, retains coverage/COVERAGE_FILE/working directory/UID/mounts and independently inspects actual command. Parser accepts only those exact host/port values; factory is fixed internally, no user app/module selector, reload, workers, custom protocols, lifespan-off, logging/body/trace flags or env-file. Config uses host0.0.0.0/port8000/factoryTrue with all other pinned defaults, including auto HTTP/WS/loop/lifespan/interface, normal proxy/date/server headers, access logging, keep-alive5/backlog2048 and unchanged upstream graceful timeout. Preserve FORWARDED_ALLOW_IPS resolved exactly as original command; reject unexpected UVICORN_* settings and WEB_CONCURRENCY other than absent/1 before app creation (their CLI/supervisor semantics cannot be silently emulated). Root retains effective safe config/image/source/env custody. No installed default/product command changes.

Normal upstream signal handling remains inherited. A completed SIGTERM shutdown may exit143 because pinned capture_signals restores/re-raises it; record actual exit code, not false success0. Shim failure raises static nonzero failure; startup not started fails nonzero like stock startup failure. Force-exit/repeated signal/kill cannot yield successful closure. The2-second bound is the cooperative event-loop observer wait AFTER normal super().shutdown, not a bound on existing product shutdown, kernel/filesystem stalls or asyncio runner cancellation cleanup. Never add blocking background threads to force it, indefinitely join cancelled tasks, or assert bounded process termination if an upstream task ignores cancellation. Root's separate bounded stop/exit coordinator below fails/kills owned resources when needed and accepts no missing closure. This residual does not require delaying an app ACK or a security-significant server rewrite; if a builder cannot keep the seam this narrow, STOP for architecture review.

Source-test plan (not executed): (1) synthetic UNIT ASGI app records object-identical scope/messages and receive/send order, HTTP exceptions/cancellation and startup/shutdown acknowledgements; blocked observer sender cannot postpone acknowledgement. (2) Regression demonstrates stock Server can return after ACK before observer closes; shim test parks observer AFTER forwarded shutdown.complete, checks normal super shutdown finished, checks shim remains pending on the actual lifespan task, then releases real collector ACK and validates final status before server exit. (3) Missing task, no grant, failed/late ACK, persisted-status mismatch, app error, force_exit and never-completing observer all fail and cannot emit fabricated complete status. (4) Task timeout leaves real counters/queue visible and nonzero shim disposition; signal test expects actual normal exit policy, not fake0. (5) Captured Config values and Server.run loop factory match locked default lane configuration. UNIT fake ASGI/sender exercises instrumentation only; nominal E2E uses actual unchanged create_app/SDK/consumers. Supported hosted CI/verified VM106 only; no host execution here.

## Private endpoint, arm/bind/close and readback contracts

Only fixed internal origin `http://scheduler-fixtures:8080` for API/runner; fixture-local host-disposition helper uses ONLY fixed `http://127.0.0.1:8080`. Existing httpx client trust_env=false/follow_redirects=false, no cookies/URL userinfo/product JWT, no retries, max1 in-flight/role. All routes below are test fixture routes, no API control/public-auth route. Header is exactly `X-Agent-Reference-Observer-Key: <64 lowercase hex>`; private random256-bit synthetic capabilities are mounted only into API/fixture/runner with endpoint-specific permissions. Compare constant-time with privately loaded expected value; do not log header/token or include it in schema. Case path/hex ID is not auth. Unauthorized requests get401 error(unauthorized), with no parsed echo. Every acknowledged SDK identity came from actual unchanged API tee/verifier; runner cannot POST observer records. API ingress credential and runner control/read capability are **distinct random256-bit files** (same header name, separate endpoint permission), preventing control client from fabricating SDK receipts. API has ingestion only; runner has control/read only; fixture holds both. Neither file reaches worker/authored sources/model payload. Host helper reads the control/read capability privately in the verified fixture container, never in argv/stdout/host logs; it receives no ingestion capability from host arguments.

| Route | Request / cap | Success / semantics |
| --- | --- | --- |
| POST `/__agent-reference/observer/sdk-receipts` | receipt,8192; API ingestion capability only | 201 ack. ready/failure/closed are lifecycle, case_id null. Selected/unexpected SDK needs active arm; invalid/unknown identity is retained failed witness, never silently dropped. |
| POST `/__agent-reference/{case_id}/arm` | arm,4096; runner control capability | 201 control_ack(operation arm,state armed,run_id null). Exactly one active case. Both API ready ACKs required. Body/path case IDs must match; hash/source/installed IDs are private setup observations, not authority. |
| POST `/__agent-reference/{case_id}/bind` | bind,4096; runner | 200 control_ack(operation bind,state bound,run_id actual). One binding only, from returned real public AgentRun trigger; fixture records it as a runner observation, not a verified DB association. |
| POST `/__agent-reference/{case_id}/close` | close,4096; runner | 200 control_ack(operation close,state closing,finish_id matching). Finish requires bound matching nonnull run_id; abort permits null only when never bound and latches case_aborted. Fresh128-bit finish_id identifies this one host disposition request; it never declares PASS. |
| GET `/__agent-reference/observer/sdk-receipts?cursor=N` | no body; runner read capability | 200 readback(scope lane,case_id/case null), lane ledger including lifecycle/unbound records. |
| GET `/__agent-reference/{case_id}/observer/sdk-receipts?cursor=N` | no body; runner | 200 readback(scope case,case_id and case state match), SDK witnesses associated with this case. No lifecycle rows invented in case list. |
| GET `/__agent-reference/observer/host-disposition` | no body/query; control/read capability | 200 finish_request,<=4096 bytes. Minimal actual collector state, not a ledger mutation, observed process exit or accepted domain result. |

Query parser admits exactly one optional cursor, canonical decimal0..256, no duplicated/extra query keys. Default cursor0. Each read returns <=4 entries and <=65536 encoded bytes; next_cursor advances by actual number returned, null only at immutable tail. A lane has <=256 receipt records (128/role); a case has <=32 SDK records. No records change after admission, association included. Collector index is arrival/ledger index, not execution/commit ordering. Pages taken while live are provisional: final read begins again at cursor0 ONLY after both closed ACKs, totals/IDs must then remain stable; this is readback, not receipt resend/reset. At most64 final lane pages and8 final case pages. role arrays in lane_state are in api,api-replica order. Body/path case_id must match everywhere; readback scope case requires case nonnull/consistent, scope lane requires nulls. offset/next_cursor/total are consistent with selected filtered list, not necessarily entry.index.

Arm accepts ONLY mode nominal-capacity. Additional negative modes require a separately reviewed enum/protocol extension; MAX_CASES32 is a resource limit, not authorization for32 runs. Arm cannot contain caller/admin/org/grants/tool result/claims/child ID/observed receipt fields. Known declaration digest is frozen from committed asset. Arm has NO catalog/profile digest: root's case separately compares actual public registration/profile and actual model wire to source-backed frozen expected values, while fixture independently validates actual request tool catalog/profile selector. Any later projected digest requires a closed preimage contract. The runner supplies actual installed Solution/deployment/agent IDs; actual SDK body solution and signed engine_solution_id must match that selected install. Authentic child identity remains observed signed engine_execution_id, joined later to committed Execution and AgentRunStep/run_id by the independent case assertions. There is NO added SDK case header or request rewrite and no fixture endpoint that sets an expected child ID/confirmed correlation.

Case transitions armed→bound→closing→closed, no backwards transition/idempotent duplicate control/reset/reopen. Bind arrives after public trigger; first live step/SDK may arrive before bind, so active-arm-pending-db witnesses are legal pending evidence, not a failure solely because bind is later. Bound run_id is immutable. Arm/bind ACK and armed/bound case state have finish_id=null. Close carries one fresh finish_id, retained immutably in closing/closed case state, close ACK, finish_request and host status; lost close ACK fails case acceptance without resend. Both finish and abort close admission to model/Cove requests; extra calls are failed late_witness and fixture503. SDK arriving during closing is retained as late with sticky failure; original app forwarding stays unchanged. Therefore runner must verify acknowledged SDK queue/quiescence and actual domain settlement before requesting finish. At most one case is armed/bound/closing at a time. After finish, host coordinator below stops only the verified API pair while actual runner/collector remain live; fixture marks case closed after both role closed ACKs. Closed means transcript/observation admission ended, NOT domain success or accepted proof. Missing role closure leaves closing/unverified. Further sequential cases require separately approved nonterminal case-finalization design; v1 supports exactly one nominal case, never automatic close-and-arm while APIs remain active.

Cross-case/unbound/unknown SDK witnesses are stored once in the bounded global ledger with association=unbound (case_id null), or late when closing case is identifiable; latch lane failure, plus case failure where applicable. Association active-arm-pending-db is the trusted fixture's one active arm, not verified runtime lineage. Missing auth/claim/body witness cannot be ignored merely because no child ID is available. Collector can ACK a valid STRUCTURAL failed witness (201 accepted=true) to preserve complete observation; its lane/case first_failure and independent acceptance still fail. This ACK is not successful product behavior. Gap/duplicate/nonce/schema/capacity errors cannot admit a record; update bounded static lane failure and return error, never echo rejected bytes/claims.

Errors use ONLY the schema above, no detail/message/path/key/value echo. Invalid JSON/schema400; unknown case404; invalid state/identity/sequence409; exhaustion503; unauthorized401; internal observer failure500. Any wrong status or error instead of201 is sender F, no retry. Exhausted per-case counts saturate at bound+1 for an explicit failure indicator (fixture request65,SDK33; independent Redis observer event65); no extra payload admitted. Global over-cap starts no new case/record and keeps sticky failure counters, never wraps/evicts. Ready/closed lifecycle admission is still subject to role128 cap; reserve nothing by fabricating missing records.

## One-case host/runner finish coordination (source-only candidate)

Historical second STOP: foreground runner logs/docker wait until pytest exits, followed by API shutdown only in cleanup, deadlocks a case requiring actual API closed proof before returning. Root requested a narrow coordinator; the server shim alone does not fix this. This coordination applies ONLY to capacity-reference invocation running actual `tests/e2e/platform/agent_reference_cases.py`. The earlier fixture-units invocation uses its existing pre/post actual-runner custody/wait/exit; it MUST NOT arm a real case, stop/restart the APIs or require a nominal finish signal. Both API processes keep the SAME observer nonces/cumulative counters from unit invocation through the single nominal case. Coordinator UNIT fakes exercise instrumentation, never stand in for actual API closure. Actual nominal pytest stays detached/alive with its own existing startup custody binding/release. No Docker socket, host credential/process authority or API-stop callback reaches case code. No observer witness swap or fake lifecycle result.

Future `api/scripts/agent_reference_fixture.py --host-disposition` is a NEW short-lived reader process in the exact independently verified scheduler-fixtures container. It reads only the actual running fixture's fixed GET `http://127.0.0.1:8080/__agent-reference/observer/host-disposition`; no ledger globals/POST/product endpoints/reset. Privately read control/read capability at fixed `/run/agent-reference/observer-control-key` (ingress is separate `/run/agent-reference/observer-ingest-key`); files contain64 lowercase hex/no newline, regular/no symlink, root-owned mounted RO with selected consumer permissions. Existing httpx uses trust_env=false/follow_redirects=false/no cookies and overall HTTP deadline1 second/per-stage <=remaining, response<=4096. Stdout success is exactly one canonical finish_request/no newline. Failure is fixed value-free stderr/nonzero, no raw response/key/header/exception. Host bounds each Dockerexec reader to3 seconds and strictly validates output. It never invents state after read failure.

finish_request rules: no active case means case_id/run_id/finish_id/disposition/state all null. Armed has case_id/state and run_id=null; bound has actual run_id; both have finish_id/disposition null. Closing/closed retains immutable finish_id/actual close disposition; finish requires bound nonnull run_id. roles are actual ready-ACK registered role/nonces, unique ordered api,api-replica; finish requires both. Abort fails acceptance. No comparison of collector/host timestamps. This is test disposal request state, not observed process exit, product settlement or authority.

Host ACK is atomic host_status at `<owned-capacity-invocation-context>/custody/host-disposition.json`, actual nominal runner RO `/app/reference-custody/host-disposition.json` via its existing root-owned custody-directory mount. No API/worker/authored source/fixture writer/mount. This extends root's lane scope only after review; existing binding.json/release.json schemas/serialization remain unchanged. Before NOMINAL release root writes phase watching,generation0,case/run/finish null, ordered verified API container IDs/bare64hex and exact image IDs/sha256:<64lowerhex>, observer_nonce null, stop_requested/exit_observed false, exit_code null. Learn only actual already-ready nonces from the same API pair; do not restart/reset them after units. Generation increases without reset/wrap; fixed sibling pending filename/regular-no-symlink checks/atomic replace/no older write. MAX_HOST_STATUS_BYTES=4096. Missing/stale/unreadable/overflow/write failure prevents acceptance; host_status is a disposition acknowledgement, never observer/domain success.

One shared proposed limit set for root review: HOST_FINISH_WAIT_SECONDS=240 from verified pytest release; MAX_HOST_POLLS=960; HOST_POLL_INTERVAL_MS=250 minimum between starts; MAX_HOST_EXEC_SECONDS=3; MAX_PRIVATE_HOST_HTTP_SECONDS=1; HOST_STOP_PAIR_SECONDS=15 after valid finish; DOCKER_API_STOP_GRACE_SECONDS=10; RUNNER_CLOSURE_WAIT_SECONDS=20 after close ACK; MAX_RUNNER_CLOSURE_POLLS=80 with250ms minimum interval. These are named test-lane failure/disposal limits, not increased product/job timeouts. First reached deadline/count fails; no extension/restart/recovery retry. Repeated successful GET polling is explicit bounded observation, not receipt/control resend. Any failed/malformed host poll permanently fails. Consumers use one shared constant set after root acceptance.

Required future root coordinator order:

1. Verify actual project/labels/source/image/commands/mounts, exact fixture/API pair/detached pytest container BEFORE release/effects. Begin bounded background secret-safe logs; log following cannot block coordinator. Collector/runner remain running. Record initial watching status then release real pytest; launcher exit is not actual runner exit.
2. Actual case observes nominal authored SDK child/model/Cove, later committed domain/summary settlement and provisional queue/status completeness, then POSTs one close(finish,actual run_id,fresh finish_id). Required public API reads precede close. It remains alive awaiting actual closed/host/status/ledger proof. Case has no Docker/process calls; final PG/private reads use surviving resources.
3. Host polls `docker exec <verified-fixture-full-ID> python -m scripts.agent_reference_fixture --host-disposition`. Validate exact lane/state/case/run/finish/two actual nonces and compare fixed custody identities; actual pytest must still be RUNNING. Nonclosing/null state is bounded waiting. Unknown/multiple/changed/unbound run/nonce, abort, early runner exit, failed poll/deadline or custody drift writes failed status and invokes owned failure cleanup; no nominal stop result.
4. Exactly one valid bound finish gives phase stop_requested with immutable identities. Reverify and SIGTERM only exact API/API-replica full container IDs concurrently, Docker grace10 seconds and pair stop+exit observation<=15 seconds. Keep runner/fixture/PG/Redis and actual consumers alive until later owned cleanup. No project-wide/service-name-glob stop. Independently inspect actual stopped container State/ExitCode; stop CLI return alone proves nothing. Normal inherited Uvicorn SIGTERM143 or actual graceful0 are recorded unchanged. Other code/timeout/identity/OOM/kill/error fails; disposal force-kill is failure cleanup, never accepted closure.
5. Publish exited only after BOTH exact processes are observed exited, with stop_requested/exit_observed true and actual exit codes. Exited has immutable case/run/finish/nonces and null first_failure/failure_stage. Failure uses failed plus nonnull static first_failure/failure_stage, preserving observed flags/codes; no invented missing values. Watching/stop_requested normal snapshots have null failures; failed never becomes exited. Status write failure, host loss/cancellation or old file causes missing terminal proof, not old success.
6. Runner matches final host_status lane/case/run/finish/actual runner ID against its own close/current custody release, expected ordered API identities/nonces and real ready/closed ledger. Require observed normal exits/no host failure AND both closed ACKs/final RO API status equations S=D=actual SDK-kind counts/nominal R=S+2, immutable complete paged ledger, no sticky error, signed actual SDK->committed child/step join, real Redis and independent PG/public settlement. Host file cannot replace any check. Then actual pytest returns PASS; missing/late/mismatched proof is actual FAIL, no fake successful payload/reset.
7. Root separately observes actual pytest exit/result, captures bounded custody/log artifacts, disposes only owned resources/private files and records teardown. Cleanup cannot supply first closure after pytest returned. Startup/effect failure/early pytest exit uses failure disposition/cleanup, no fabricated finish. Premature runner/collector stop or receipt loss prevents acceptance even if domain happened to settle.

host_status semantics: exactly one api then api-replica; full container IDs are bare64 lowercase hex and image IDs EXACT sha256:<64lowerhex> verified Docker readback, matching root's independently saved prebuild image witness/lane pins with NO stripping/projection/alias. They are not fixture/case arguments. Exit_code null iff exit_observed=false. Successful exited requires both stop_requested true. Watching keeps case/run/finish null; stop_requested/exited requires one bound finish/all nonnull identities. Failed abort may have null run_id. first_failure/failure_stage both null for normal phases, both nonnull for failed. mono_ns is actual host monotonic string, never compared to observer/collector time; generation/identity are cross-process checks. Existing custody files keep their own contracts; only this new host file uses this private codec.

Capability/status/custody construction remains an explicit root lane gate: "root-owned" means owned and provisioned by the root coordinator's task, not an assumed UID0 or readability claim. API and fixture preserve their existing configured User AND Entrypoint (Compose overrides or image defaults as applicable); actual effective application UID/GID requires separate independent readback, with no API/fixture nonroot or UID1000 claim. Config.User must not be assumed to equal effective application UID. Runner UID1000 is distinct from actual observed host coordinator UID/GID (commonly GitHub host UID1001). Root must record actual host/container UID/GID/mode/mount ownership, provision private reader/writer permissions deliberately, and verify actual selected processes can read required capability/RO custody/status files and API roles alone can write their own status subdirectories before effects. No secret bytes are exported by read proof. A source assertion, mode0600 on a host1001 file or regular-file check does not prove container1000 access. Failed construction/cross-role writable mount/world-exposed private directory/stale shared resource is STOP, never permission loosening or assumed proof. This candidate does not perform that provisioning.

Future coordinator UNIT source plan uses ONLY synthetic instrumentation: a fake runner waits after a synthetic fixture finish request; a fake coordinator reads it before fake runner exit, records stops of exact synthetic API identities, preserves fake fixture/runner, and releases the simulated case only after independently supplied synthetic closure evidence. This does NOT describe the real first fixture-units invocation: it has no real case arm/finish request or API stop/restart and uses its existing actual-runner custody/wait/exit. Wrong container/nonce/finish, early runner exit, blocked logs, bad/slow poll, stale/missing host/status, one role live, kill137 or shim failure/lost ACK all fail. Fake process/ASGI consumers exist only in UNIT instrumentation tests, never nominal. If lane still blocks in foreground logs/wait or cannot preserve verified custody/RO transports, STOP before implementation/runtime acceptance.

## Deliberate exclusions and proof

Event messages remain actual pre-persistence Python publications. Accepted pre-trigger real Redis PSUBSCRIBE acknowledgement/bounded64 prebind buffer/exact org-or-global+all subscription avoids first llm_request race; actual public run ID binds later, foreign/unbound runs fail. No event field/order/timestamp is synthesized; summary fields absent from events need fresh committed PG/public readback. Redis retention bounds do not bound the client allocator. CurrentUser's broad legacy engine path has no fresh SDK lease check; projected signed identity is not C2/new-runtime authority. Fixture ACK/control state/case association cannot certify a committed row or domain result.

Future source/unit/E2E gates must demonstrate exact-key/type/byte/sequence rejection, all counter invariants including rejected enqueue/lost ACK/cancelled send, distinct ingestion/control credentials, no capability at redirect target, safe negative token/body capture, byte-identical upstream messages and original exception/cancellation propagation. Shutdown tests must go red if ack/status/drain evidence disappears; only observed final closure accepts. No test here executes consumers/mints nominal credentials or modifies domain state. #1017 STOP remains; no Rust/new-runtime ownership accepted.

Future ownership requires root freeze: contract owner `api/scripts/agent_reference_contract.py`; observation builder `api/tests/e2e/platform/agent_reference_observer.py` plus new `api/tests/e2e/platform/agent_reference_server.py` and observer UNIT path; fixture builder `api/scripts/agent_reference_fixture.py` plus fixture UNIT path; root/case owner minimal observer client in `api/tests/e2e/platform/agent_reference_cases.py`; root/lane owns `scripts/agent-reference-lane.sh`, `scripts/render-agent-reference-compose.py`, `test.sh`, existing runner custody and host-status file/RO mount. No product import/export/caller, dependency/lock, SDK/workspace/Rust/ORM/publicauth/DB/control-route change. ONE shared stdlib module exports frozen constants/enums plus `encode_private(family,value)->bytes`, `decode_private(family,raw_bytes)->dict`, `validate_private(family,value)->None`; static ContractError codes only private-error.error. Family/key/semantic checks precede use; helpers perform no I/O/auth/time/random/product import. Lifecycle task/grant belongs to test observer/server instance, not validator.

This task changed ONLY `/tmp/bifrost-agent-reference-closed-wire-contract.md`. Static source/asset/git, JSON-document structural validation and primary pinned dependency-source reads/proposal review were performed; no repository change/product import/runtime test/Docker/CI/commit/push/vendor action. Root's lane work was preserved; final read observed its committed HEAD7b24f9d21d0849188bcc2c94d75b67d63a6e75a1 with clean worktree and empty product-source diff against60da (api/src,api/bifrost,api/shared). Smallest next step is independent root review of schemas/counters, distinct private capabilities, proposed capture/lifecycle/phase limits, then explicit builder ownership freeze. Source supports narrow normal-shutdown mechanism; installed-source/command/config custody, transparent forwarding/failure races, actual API exits and all-evidence completeness remain runtime gates. Stock-command and foreground-runner-order STOP history remains explicit.
