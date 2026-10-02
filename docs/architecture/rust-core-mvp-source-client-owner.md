# Agent reference source-client owner: bounded design checkpoint

Status: accepted bounded source design; **implementation and runtime release
remain blocked**. This is a supplement to the existing material-adapter design,
not a completed reference consumer or Rust lifecycle implementation.

The architect adopts the exact candidate below subject to the independent
review reproduced afterward. Candidate and review hashes identify their
original bytes; this document adds headings and this disposition only.
The source baseline is platform main
`01cadfe09710d293a40da14d6cf4056165289e31`, reader branch
`d35451eebfbaee4c5b0246356955a1f0302147d5`, and workspace main
`6bb2399a4e185ffadbd2651953ab9c90320b4d5a`.

The accepted seam uses public SDK local signing and a separate standard HTTPX
client for eleven fixed source-object reads. Explicit credentials alone do not
prove absence of SDK token/configuration/network lookups. All acquisition,
reads and actual close awaits share one deadline. An uncertain close produces
no usable object set and retains the actual owner.

The original proposal below left the bootstrap and owner call site unresolved.
The subsequent bounded bootstrap checkpoint at the end of this document selects
same-interpreter pytest startup, avoiding exec in the nominal branch. It remains
independent of the missing nominal owner call site. No second observer process
or IPC framework is required. This checkpoint does not authorize adding a
speculative consumer or changing shared API, worker or collector logging.

Root must freeze that bootstrap, sanitized SDK/profile inputs, import order,
actual client ownership and disposal before releasing the two proposed transport
files. Installed zero-send signing, actual storage compatibility, cancellation,
close and secret-sentinel tests remain required afterward. Reader CI success
proves none of these conditions. Host construction and #1017 remain stopped;
no merge, deployment, public credential wiring or C2/C3 acceptance follows.

## Reviewed candidate

Original SHA256:
`c9f83f8fe7c455b5e9591f83f61e0645418bf84cc08cc934809aa239d0fb1843`.

# T-S source-client owner contract — AUTHOR candidate, no self-approval
2026-10-02. Proposed bounded supplement to dce310 plus controlling0ae2c conditions; independent review/root freeze REQUIRED. No builder/runtime/authority release. Engineering-flow2026-09-30.1 retained. Full unchanged Agent→capacity Workflow→final answer→summary MVP remains the goal; #1017/host literal SOURCE STOP/Rust/C2/C3 gates unchanged.
Reference /home/thomas/src/bifrost-agent-capacity-reference clean HEAD d35451eebfbaee4c5b0246356955a1f0302147d5, origin/main01cadfe09710d293a40da14d6cf4056165289e31. Parent reports CI37001683085 SUCCESS (6 PG/191 reader units); not independently polled here and not nominal/storage/owner proof.
Owned future T paths ONLY: api/tests/e2e/platform/agent_reference_source_transport.py and api/tests/unit/test_agent_reference_source_transport.py. This author task writes only this /tmp packet. No changes to reader, product, runner, renderer, consumer or previous reports.

## Source-backed feasibility and explicit STOP
Existing S3StorageClient.get_client uses shared SDK session and explicit Settings; generate_presigned_download_url uses public async generate_presigned_url then rewrites URLs. T must use a NEW standard session/public signer directly, avoiding shared session/rewrite/bulk reads. Locked aiobotocore3.5.0, botocore1.42.91, HTTPX0.28.1 match inspected cache METADATA/requirements.lock; cache source is not installed custody.
aiobotocore session.py173–181 constructs AioCredentials when BOTH keys are explicit, skipping credential resolver; credentials.py273–291 returns a static ReadOnlyCredentials tuple. signers.py397–474 builds/serializes the modeled request and delegates local signing;182–201 prepares and returns URL without calling SDK endpoint.send. Explicit s3v4-query/regular non-ARN bucket uses static credentials, not bearer/identity-cache auth. This is source feasibility, NOT runtime zero-send attestation.
Client creation ALSO calls get_auth_token and reads profile/config/defaults. Explicit keys alone do not prove zero IAM/SSO/metadata lookup. New session public set_config_variable('config_file','/dev/null') and ('credentials_file','/dev/null'), plus source-constrained empty ambient AWS profile/token/config/mount slots and AioConfig defaults_mode='legacy', are required before create_client. The empty Linux files provide no SSO profile; unscoped token lookup returns None. No ambient-profile discovery, default credentials, refresh/session token, account lookup or smart-default IMDS branch is permitted. A None profile override is NOT claimed to suppress an environment profile. Do not inspect private hooks, replace credential/token providers, unregister handlers, copy signing or add custom transport.
Existing renderer/lane supplies fixed synthetic Settings and closed process/env/source/image custody. Existing agent_reference_runner.py execs pytest; logging changes before exec do NOT survive. No dedicated storage-observer bootstrap/call site exists in the two T paths today. Therefore actual isolated-process producer/invocation/logging/SDK-installed custody is an explicit STOP-to-builder/integration-release gate, not an implemented owner or caller-provided verified flag. Root must separately freeze that producer; this packet adds no process/IPC framework or runner mutation.

## Finite owner and constructor seam for independent review
Proposed private SourceClientOwner is a slots/repr-redacted single-use lifecycle object, not a trust DTO. It retains actual session, SDK creator context, actual entered AioBaseClient, actual HTTPX AsyncClient, actual outstanding response, started_ns/end_ns, fixed source/case binding and finite state acquiring|ready|reading|closing|closed|tainted. Actual objects never enter results, logs or oracle state. A class annotation/constructor cannot attest their origin.
Proposed stdlib-only constructor: `SourceClientOwner()` allocates an acquiring owner with no clients/I/O; root retains it BEFORE any await. Proposed method: `await owner.open(*, deadline_ns: int) -> TransportCode|None`; None only after actual ready state, static failure keeps closed/tainted owner available to root. Cancellation propagates with root retaining this same owner. No supplied Settings/secrets/client/session/factory/key/URL/callback/VerifiedClient. It is called only by root's separately frozen actual dedicated observer after logging bootstrap; it calls current get_settings locally in that supported process and requires the fixed profile. No configuration mutation/cache clear or seventh privileged material.
Actual profile: object_storage_provider='s3', s3_endpoint_url='http://seaweedfs:8333', s3_bucket='bifrost-test', s3_region='us-east-1', existing source-defined synthetic s3_access_key/s3_secret_key, no s3_public_endpoint_url rewrite. Actual API/worker/runner Settings/config/image ownership must match independently retained root custody. No Azure/live credentials/new TLS listener/IAM/profile/backend fallback. Preserve SAME ACTUAL existing isolated BIFROST_SECRET_KEY/default init; C still calls decrypt_secret→get_settings, receives no explicit application key or new secret.
Root bootstrap must execute literal `logging.disable(logging.CRITICAL)` in the DEDICATED observer interpreter BEFORE importing T, SDK, HTTPX or product modules. T module must defer those imports; no global logging change inside a shared API/worker/collector/pytest interpreter. Root observes actual interpreter/import sequence and disabled relevant INFO/DEBUG logging; no supplied boolean. Never print SDK arguments/URLs/headers/Settings/raw exceptions/warnings; static diagnostics only. Supported sentinel tests restore test logging state outside the nominal observer.
owner.open samples actual monotonic start, sets ONE end=min(unchanged deadline,start+3s), and rejects invalid native int deadlines/bools. All acquisition, signing, response/body/context-close, HTTP close and SDK exit plus final sample share THIS end. Before/after synchronous portions check remaining end; they are not preemptible CPU/heap claims.
Create fresh aiobotocore.session.get_session(); public config_file/credentials_file overrides above; no custom event hooks/default client config/profile or session reuse. Construct AioConfig(signature_version='s3v4',s3={'addressing_style':'path'},proxies={},retries={'mode':'standard','total_max_attempts':1},endpoint_discovery_enabled=False,defaults_mode='legacy',ignore_configured_endpoint_urls=True). Public create_client('s3',endpoint_url=fixed,region_name=fixed,aws_access_key_id=actual Settings key,aws_secret_access_key=actual Settings secret,aws_session_token=None,aws_account_id=None,verify=True,config=config), then actual await creator.__aenter__ under remaining end. Do not invoke any service operation except fixed public presign.
Create dedicated standard httpx.AsyncClient(trust_env=False,follow_redirects=False,proxy=None,auth=None,cookies=None,headers={'Accept-Encoding':'identity'},timeout=remaining); no custom transport/hooks/mounts/client factory/shared pool. Root supplies no HTTP objects. Default library noncredential headers are permitted; additional credential/default Cookie/Authorization headers are forbidden. The selected origin is closed-network HTTP, not TLS confidentiality proof.
Partial acquisition uncertainty taints actual owner and yields no success. An entered/unentered partially constructed SDK context cannot be assumed closed; root retains actual process owner until separately verified disposal. No private recovery/extra cleanup deadline is authorized.

## Transport seam, exact bindings and one-shot closure
Proposed `read_deployment_objects(setup: SetupSnapshot, source: InstalledSourceInputs, *, owner: SourceClientOwner) -> TransportResult` consumes ONLY the internally constructed root-owned owner once. It is not callable with arbitrary signer/http/URL/key/plan/factory. Owner acquires no DB or caller authority; transport admits setup/source/IDs and fixed eleven-object plan before network. No case can hand-build an owner and thereby certify custody.
Reuse dce310 immutable ObjectTransfer/RangeObservation/DeploymentObjectSet/TransportResult fields and static codes. Bind actual SetupIds S/D1/D2/role and source_commit_sha/carried bytes; zero actual edges; exact actual deployment source_artifact_key/runtime_storage_prefix equal existing pure product constructors. Existing recipe helper admits exact five carried assets; no manifest-selected arbitrary keys or copied validator. Two manifests cap65536 each; archives EXACT510/112105; D1 runtime219/23; D2 runtime219/23/17642/32957/60566; admitted aggregate355336; eleven fixed reads only, no HEAD/list/retry/continuation/second profile.
For each internal key/cap, await ONLY public signer.generate_presigned_url('get_object',Params={'Bucket':'bifrost-test','Key':key,'Range':f'bytes=0-{cap}'},ExpiresIn=60,HttpMethod='GET') under remaining end. Native str<=4096 UTF8 bytes; exact http/seaweedfs:8333/path='/bifrost-test/'+internally derived ASCII key; no userinfo/fragment/path normalization. SDK query unchanged/private, no signature/query parser or URL exported. Reject wrong URL before HTTP.
Before EACH HTTP call require actual dedicated jar empty; reject ANY Set-Cookie response immediately (including empty value/first response), never issue next request; don't clear/normalize cookies into success. Await existing http.stream('GET',actualURL,headers={'Range':sameSignedRange,'Accept-Encoding':'identity'},follow_redirects=False,timeout=remaining) entry. Actual response origin/path/history must match; only200/206; no redirect/error body read, foreign Location handling, proxy/netrc/auth/header fallback or automatic application retries.
Admit<=64 raw header pairs/8192 aggregate bytes AFTER library header parsing; reject duplicate Content-Length/Content-Range/Content-Encoding/nonidentity encoding. Native canonical bounded response-length/range parsing only, no raw HTTP parser. 206 requires first0,0<=first<=last<total<=cap,last=total-1, actual payload length=total and matching Content-Length if present. Partial range/unknown total fails; no second range request. All objects/manifests nonempty. 200 ignored Range still requires whole EOF and lengths within cap.
Use unchanged response.aiter_raw(chunk_size=min(4096,cap+1)); track aggregate consumed bytes, admit at mostcap+1 sentinel, stop/fail immediately on oversize, require actual EOF and exact lengths. Never treat a cap-length short read as EOF. Underlying HTTPX/core/socket/server buffers remain outside this yielded/retained admission; no raw socket<=cap+1 claim. Full canonical bytes/digests/source semantic checks stay in C.
Every await, including iterator EOF's internal response.aclose and stream context exit, must be within the same end. No success solely from Response.is_closed: source sets it before awaiting underlying stream close. Retain actual outstanding response/context; one actual context exit; code must prove true close awaits completed before advancing. T source review must inspect cancellation paths, not accept an outer async-with as proof.
After last EOF/response exit, actual owner close sequence is HTTP client.aclose then SDK creator.__aexit__(None,None,None), each awaited exactly once under SAME remaining end; actual end sample<=end. No detached task/shield/background cleanup/retry/new timeout. SDK's close exits its real HTTP session; local presign does not itself prove client cleanup. Release DeploymentObjectSet/TransferAcquisition ONLY after both actual owners closed and sampled completion. Caller cannot take partial successful objects out of closing owner.
On deadline/transport/error/close uncertainty return no usable objects/acquisition, latch tainted ownership; cancellation propagates after preserving owner disposition, never static success. If budget is gone, do not start an unbounded finally close or assert cleanup. Root separately owns actual observer-process disposal/readback; the operation invents no cleanup allowance. Retained uncertain owner prevents nominal acceptance. No reuse/reconnect or new source adoption.

## Required independent gates and future commands (not executed/authorized here)
Root must freeze actual dedicated observer entry/call site: logging BEFORE imports, closed sanitized env/config/no credential mounts, Settings source binding, installed interpreter/SDK/HTTP/source/image/SeaweedFS identities, one constructor→one transport→closed/tainted readback, actual owner disposal. Existing source-defined renderer/runner receipt alone is not this missing process evidence. STOP if obtaining it requires changing shared application logging or inventing a consumer/process/IPC API.
Different reviewer must accept this packet plus dce310/0ae2c; root explicitly decide source authoring. Future source checks: stdlib AST and locked Ruff0.15.12 on ONLY the two T files, then independent implementation review. No host product/dependency imports or pytest.
After source authorization/publication, supported isolated tests must exercise ACTUAL SDK construction/presign with zero SDK HTTP/IAM/SSO/metadata sends observed under root-owned network/source instrumentation, actual Settings/custody and no ambient profiles. Fake clients alone are not zero-send proof; no nominal SDK monkeypatch/private hooks are permitted. Wrong origin/keys/cookies/Set-Cookie/redirect/region/errors/partial206/shortEOF/sentinel/growth/nonidentity/close stall/cancellation/shared-end negatives plus actual owner teardown/source/secret sentinel proof required.
Future supported commands after separate authorization: `./test.sh tests/unit/test_agent_reference_source_transport.py -v`, `./test.sh quality api`, clean committed `./test.sh pre-pr`; real selected storage/owner compatibility uses the separately frozen dedicated supported consumer command, currently UNFROZEN/STOP. Existing `./test.sh agent-reference` is the full lane, not permission to run unfinished nominal or #1017 retry. Root must name exact zero-SDK-send/actual-close acquisition test call before runtime; no guessed acceptance command here.
This packet does not supply installed clients/owner evidence, runtime local-signing/Range compatibility, actual close or public/source/SDK/model/events/process/summary/fresh-postclosure nominal proof. Those gates remain unresolved even if the finite source constructor is independently accepted.
Only source/text/cache-METADATA/stdlib hash/read-only git inspected. No imports/execution/tests/SDK/DB/Docker/env/credentials/network/CI or repository edits. Prior reports unchanged. Smallest next step: independent review of this finite source seam and root freeze or explicit STOP on actual dedicated producer ownership.

## Exact source/input hashes
| Absolute path | SHA256 |
|---|---|
| `/tmp/bifrost-agent-reference-material-adapters-interface.md` | `dce310218cdc04c0872afd096d13fc6f302dfd7ba879b029aba8eb5e28c8971f` |
| `/tmp/bifrost-agent-material-adapters-independent-review.md` | `0ae2c2d7af2f4529cca8fa7d98481eacc3265a340911ef71ba0ada85c9247fc3` |
| `/home/thomas/src/bifrost-agent-capacity-reference/requirements.lock` | `be76160e9eb3b3ba9ab0d9af9e77f311db3578cfaf02b7042d9697bd7c2feea1` |
| `/home/thomas/src/bifrost-agent-capacity-reference/api/src/services/file_storage/s3_client.py` | `7e3a11628052b5679bb270c501c7ddaf838f7a4f251b020a63924c946208f6ea` |
| `/home/thomas/src/bifrost-agent-capacity-reference/api/src/config.py` | `cdcbf0fa364b48f2d93b16b95e5476bdd9482a595603de59f88500faf3578fbd` |
| `/home/thomas/src/bifrost-agent-capacity-reference/docker-compose.test.yml` | `09f2a1e0bd84827bf9dd215d25fd697ffedf6d0bad3a0210296690a4e7a3aa12` |
| `/home/thomas/src/bifrost-agent-capacity-reference/scripts/render-agent-reference-compose.py` | `d18928d306431185114e43d6a8949cabcb5229655bc086325e6d3b65460aa043` |
| `/home/thomas/src/bifrost-agent-capacity-reference/scripts/agent-reference-lane.sh` | `7764317042c6f72e83aac8cd11993863db363ce886888fb06d048f254ecdb774` |
| `/home/thomas/src/bifrost-agent-capacity-reference/api/scripts/agent_reference_runner.py` | `aceeb4cfb0e5679dbfa8b5bf5453a7e80e9624ee15daf3ad436398d3e1f8dfd7` |
| `/root/.cache/uv/archive-v0/oMsczHT6TdqmoQr6/aiobotocore/session.py` | `f49cc53616182b2c6c23219d171e7e84cae3b5630934a98425786d5c6ee2a16a` |
| `/root/.cache/uv/archive-v0/oMsczHT6TdqmoQr6/aiobotocore/signers.py` | `40b3e95c9293875337ca21d73256fc0c5d7d76fe20aa2737b544c245103a7022` |
| `/root/.cache/uv/archive-v0/oMsczHT6TdqmoQr6/aiobotocore/credentials.py` | `93908dbb0733159d05a3fea8b6fa5df024c6d42cc2759883cc8d64664a88d009` |
| `/root/.cache/uv/archive-v0/oMsczHT6TdqmoQr6/aiobotocore/client.py` | `85b986c192448b47087c1375cb52486a1de89d5f697a279a977396fddb780c0b` |
| `/root/.cache/uv/archive-v0/oMsczHT6TdqmoQr6/aiobotocore/httpsession.py` | `f7986bf680f4e1e3ffe300eead144a117ecac51fc86bbf86333adbba05b352e2` |
| `/root/.cache/uv/archive-v0/yvxVF0GXJaHRGRJJ/botocore/session.py` | `1da939f2ac5ee705775c5e5601177d97174f5ce0755ed695015118cb64c61d6c` |
| `/root/.cache/uv/archive-v0/yvxVF0GXJaHRGRJJ/botocore/tokens.py` | `2d22d00f7c3fdd48bbda1a76793cec15dae53f521662c06f82639f27e95352f8` |
| `/root/.cache/uv/archive-v0/yvxVF0GXJaHRGRJJ/botocore/config.py` | `3ff88b9f5af4b19a41792e5696918135e0bfc64011dcb4843fb515922239014f` |
| `/root/.cache/uv/archive-v0/PNXO-Nb8l9tHBSg7/httpx/_client.py` | `c43f941baefe58c91e96d00039e1868fe719d91453026d7db1647194563bff8d` |
| `/root/.cache/uv/archive-v0/PNXO-Nb8l9tHBSg7/httpx/_models.py` | `e3ffc6bb2bf580bc6e6428708a6f247d036220e709f5a72b785392babbd97e6b` |

## Independent review

Original SHA256:
`fe3e98459ece36b29394f7b95fd917e5452e457c0a1656888fc8dd428e70ee0d`.

# Independent source-client owner review

2026-10-02. **VERDICT: ACCEPT BOUNDED SOURCE DESIGN WITH CONDITIONS; STOP-TO-BUILDER AND RUNTIME ACCEPTANCE REMAIN.**

I did not author the reviewed owner packet. No architectural contradiction remains within its explicit closed-profile, dedicated-process and fail-closed conditions. This accepts a proposed finite owner seam, not an implemented owner, invocation, cleanup proof, source-authoring release or nominal MVP acceptance.

Engineering-flow package `2026-09-30.1` read; both repository AGENTS.md and applicable project/SDK/verification guidance read. Review used source/text/cache files, stdlib hashing and read-only git only. No product/dependency imports, pytest, SDK invocation, Docker, credentials/environment inspection, runtime/network/CI access, repo edits, agent spawning or previous-report changes. Only this new report written.

## Exact custody

- Reference `/home/thomas/src/bifrost-agent-capacity-reference`: clean HEAD `d35451eebfbaee4c5b0246356955a1f0302147d5`.
- Docs `/home/thomas/src/bifrost-rust-core-mvp`: clean HEAD `213d4db1dd02ff76d09d78bff795bc9792ed1cb8`.
- Owner packet `/tmp/bifrost-agent-source-client-owner-contract.md`: SHA256 `c9f83f8fe7c455b5e9591f83f61e0645418bf84cc08cc934809aa239d0fb1843`.
- Parent interface `/tmp/bifrost-agent-reference-material-adapters-interface.md`: `dce310218cdc04c0872afd096d13fc6f302dfd7ba879b029aba8eb5e28c8971f`.
- Controlling review `/tmp/bifrost-agent-material-adapters-independent-review.md`: `0ae2c2d7af2f4529cca8fa7d98481eacc3265a340911ef71ba0ada85c9247fc3`.
- All 19 owner-packet listed input/source hashes independently match. Cached locked SDK source is source feasibility evidence, not installed-process custody. Historical reader CI is not independently verified here.

## Source findings and conditions

1. **Finite acquisition is a plausible public seam.** Synchronous zero-I/O allocation before await gives root an actual owner to retain through cancellation. One sampled `min(deadline,start+3s)` shared by acquisition, eleven transfers and closure prevents per-operation renewed budgets. Rejecting caller sessions/factories/keys/URLs/verified flags removes authority injection at this seam. Settings still supply real authority: custody must come from independently frozen process/source/profile evidence, never constructor type or successful Settings comparison alone.

2. **Local SDK presign feasibility is supported, zero-send execution is unproved.** `/root/.cache/uv/archive-v0/oMsczHT6TdqmoQr6/aiobotocore/session.py:173–181` constructs static credentials when both keys are explicit; `signers.py:397–474` resolves/serializes a modeled operation and delegates signing without invoking endpoint send. Static credentials, ordinary non-ARN bucket, explicit s3v4 and a fresh standard session are compatible with the proposed split. Public event paths still run; source feasibility is not permission to skip installed-source/event/network zero-send observation.

3. **Explicit keys alone do not suppress ambient SDK configuration.** `session.py:199–218` still gets auth tokens/config and may invoke smart defaults; `:238–249` passes scoped config and registers monitor/plugins. `/root/.cache/uv/archive-v0/yvxVF0GXJaHRGRJJ/botocore/session.py:413–424` accepts default config when profile is absent but raises if an ambient selected profile is missing. `/dev/null` public config/credentials overrides plus a genuinely absent profile/token/config environment and `defaults_mode='legacy'` make the selected source path plausible. An empty-string profile or `profile=None` assertion must not be treated as absence. Root must freeze actual sanitized process inputs and standard installed plugin/event custody. No private provider replacement or copied signer is justified.

4. **SSO/token no-send is conditional, not a static-credentials consequence.** Cached botocore `tokens.py:211–218` loads SSO only from a configured profile; `:331–338` returns no token without SSO config; `:354–357` returns None for unscoped environment-token lookup. These support the proposed empty-files/no-profile path. They contradict any interpretation that explicit access keys by themselves prove no token/config lookup. The packet correctly retains this condition and does not claim resolver bypass for all ancillary setup.

5. **The actual producer is missing.** `/home/thomas/src/bifrost-agent-capacity-reference/api/scripts/agent_reference_runner.py:42–49` admits only the two closed pytest command shapes and `:88` executes them with `os.execvp`. It supplies neither the proposed dedicated observer invocation nor logging initialization surviving exec. Existing lane/renderer receipt cannot stand in for this missing actual interpreter. The packet explicitly marks this gap STOP; it has not closed dce310/0ae2c's client-acquisition builder blocker.

6. **Logging ownership is a necessary process condition.** HTTPX `_client.py:1740–1747` logs the signed request URL at INFO. Result redaction after HTTP cannot prevent that emission. Literal `logging.disable(logging.CRITICAL)` before relevant imports in a dedicated observer is a coherent narrow condition. It is not established by the current runner or a caller boolean. Imports, warnings and exception printing also require the packet's fixed diagnostics discipline. Source authoring cannot silently change shared pytest/API/worker logging to obtain this condition.

7. **Close-state booleans cannot attest completed closure.** HTTPX `/root/.cache/uv/archive-v0/PNXO-Nb8l9tHBSg7/httpx/_models.py:1063` closes at raw EOF; `:1075–1078` sets `is_closed` before awaiting stream close. A cancelled EOF close may therefore make later stream-context exit return without completing underlying close. Future T must latch uncertainty and return no objects; it must prove EOF-close completion, not merely perform context exit. SDK `/root/.cache/uv/archive-v0/oMsczHT6TdqmoQr6/aiobotocore/session.py:31–36` stores the client only after creation completes and does not automatically clean failed entry. `httpsession.py:110–115` awaits its actual exit stack. These directly support retaining partial/tainted owners and prohibit assuming an unentered creator is safe to close.

8. **Shared deadline bounds admission, not guaranteed preemption or disposal.** Public awaits and synchronous work are not intrinsically bounded by a remaining-time HTTP timeout. Cancellation may interrupt cleanup or wait for library cancellation processing; no design-only packet proves an absolute wall-time return. Accept only sampled completion before the common end for success. Expiry/cancellation/uncertain close means no successful acquisition, retained owner and separately verified process disposal. No shield/detached close/extra cleanup budget may manufacture acceptance. Runtime close-stall and cancellation tests remain necessary.

9. **No hidden authority release.** Same actual isolated application secret, local get_settings, exact fixed storage profile and source-derived eleven-key plan preserve the declared authority boundary. No supplied key/Settings/client/profile, credential-policy change, IAM fallback, generic fetch plan or DB/lease authority is accepted. Runtime plaintext/source/storage/model/event/process/summary/fresh-postclosure evidence remains outside this transport seam. The bounds on yielded bytes/parsed headers are not wire-memory or CPU bounds.

## Exact disposition and smallest root decision

Root may freeze this exact packet as an accepted bounded supplement to dce310 under controlling0ae2c. It must separately decide and freeze the actual dedicated observer producer: exact entry/call site, logging-before-import sequence, sanitized profile/config/mount custody, installed source/package/image identity, one retained actual owner lifecycle and disposal readback. No producer, no builder/integration release. Do not invent a process/IPC framework or alter shared logging to bridge the gap.

The design does not intrinsically require a second observer process. An already dedicated nominal pytest-runner interpreter could own the observer if a separately approved narrow bootstrap in that actual post-exec interpreter disables logging before T/SDK/HTTPX/product imports and provides the retained owner call/disposition. The present runner has no such bootstrap; its nominal argv alone is not a producer or import-order proof. No new process/IPC layer is necessary as a design prerequisite.

The smallest next decision is therefore **name and source-freeze that concrete post-exec bootstrap/call site in the dedicated nominal runner, or explicitly retain STOP-to-builder**. After that decision, any narrowly scoped T source authoring still needs root authorization and independent implementation review. Supported SDK zero-send, HTTP/Range compatibility, cancellation/actual-close, source/secret sentinel and nominal MVP gates remain unrun. No #1017 retry, host SOURCE STOP change, Rust/C2/C3 acceptance, merge, deployment or runtime acceptance follows from this review.


## Separately reviewed nominal bootstrap source checkpoint

Root proposal `f93fc3fb775f96cb46b15d28c18c6c3dd876a40c33ed00b61cc24f87d348adfe`
and different design review
`8fca267b428d9512711e6fae0abc1e22fba456e837208ca512675b5ce7676658`
freeze only the existing runner and lane-unit paths. After actual successful
release validation, the exact nominal logical argv selects stdlib logging
suppression before importing pytest, followed by `SystemExit(pytest.main(argv[1:]))`
in the same dedicated interpreter. No exec follows. Unit argv retains exec.
Binding fields, approved arguments, hostname, wait, receipts and service logging
remain unchanged. This supersedes the proposed need for a post-exec bootstrap,
without claiming an actual nominal consumer or owner.

Public pytest.main is a supported programmable API; it does not duplicate
console_main's broken-pipe handling. Logical argv does not prove effective ini,
PYTEST_ADDOPTS, PYTEST_PLUGINS, installed entry-point or conftest custody.
Actual supported source/image/interpreter/plugin/environment readback and
secret/logging sentinels remain gates. caplog can lower logging suppression;
the future closed case must not use it or re-enable logging. Timeout diagnostics
can bypass normal teardown; they cannot establish disposal. No new plugin or
environment policy is implemented merely to obtain a pass.

Different implementation review
`e47a051b33cf494c95c3109f8c776501fa5fb0a07e153facbbe1f470cf07271f`
accepts exact runner `7fe29af975566fd710ab00d4feda6be69d3492d4ddb31ee83fdcc15f43c83a20`
and lane units `4041026ee62b63dceacb41e7bb2dfb15e3afd33f4085e18d330f32df8897f20b`.
Only those two paths changed, 68 insertions/8 deletions. Tests characterize
import order, native integer exit propagation, no nominal exec, unchanged unit
exec, failed-custody no-import/no-exec and logging restoration. Fake pytest is
branch characterization, not installed startup or SDK custody proof.
AST, exact Ruff0.15.12 check/format-check and whitespace checks pass.

Source candidate `701aaccd7956b1a3224fc561e4edc72368e88ace`, tree
`2bcf168cae405a0c4b8052585348d50d76f5fa12`, includes main01cadfe and is clean/pushed.
Supported ordinary CI plus unchanged pre-PR was requested once as
[run37007142830](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37007142830)
and is pending. No nominal agent-reference invocation, host helper or #1017
repair was requested. Full transport release remains blocked by the actual
nominal owner call site and acquisition/profile/installed-source custody.
