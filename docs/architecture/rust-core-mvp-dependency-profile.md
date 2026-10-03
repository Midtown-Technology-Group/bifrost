# Selected MVP dependency profile: approval proposal

Status: proposed runtime scope decision; **not approved or implemented**.
This is a concrete proposal for the unchanged A readiness and full B
Agent → capacity workflow → answer → summary scenarios. It does not substitute
a simpler workload or require authored workspace changes.

Recommend a digest-bound, separately prepared existing API image for these
selected runtime profiles, without mutable workspace-global dependency
installation. Existing Python workers and package-management behavior remain
unchanged. Actual nonempty global requirements, ambiguous absence, or reliance
on additional global packages stops this profile; no deletion, fallback masking
or package substitution may make it pass.

The decision deliberately excludes mutable global-package dependencies from
this selected adapter. Explicit scope approval is required before implementation;
the earlier caller and credential approvals do not resolve dependency semantics.
Approval would select the profile only. It would not establish installed custody,
a closed entrypoint, permission enforcement, public SDK credentials, C2/C3
acceptance, merge, deployment or vendor use.

The independent review below accepts the proposal conditionally. Root applies
its one required source correction: a requirements API404 is a worker control-flow
choice, not proof of storage absence. Both originals retain their exact hashes.
The candidate text below contains that correction; no other candidate contract
was changed. Actual storage/cache/API custody must prove the profile prerequisite.

Fresh source reconciliation after review: platform main remains
`01cadfe09710d293a40da14d6cf4056165289e31`; workspace main advanced to
`7fa1c2e045c103b5a118cf1d44c95e749eb184b6`. Its thirteen-file reviewed-delivery/
CI/queue delta changes no selected authored input or boundary-checker byte.
The current audit passes2,115 boundary/1,992 standard-authored files, zero forbidden
imports and explicit empty allowlist. Candidate source identities below remain
historical review pins; installed runtime custody is still unproved.

## Reviewed candidate with required correction

Original candidate SHA256:
`531d90493e7f5267c2f81e03a75873a9ac592d353b48b1ac7aa8d4b5aed21bde`.

# C2 dependency policy decision proposal — author source only

2026-10-02. AUTHOR proposal, requiring independent review and explicit user ratification; neither dependency policy is approved by this file. Engineering-flow 2026-09-30.1; applicable platform/workspace AGENTS and source/testing guidance read. No implementation or runtime acceptance. Prior reports unchanged.

## Decision and preserved end state

Recommend a **selected-MVP prepared-image policy with zero workspace dependency installation**, using the existing locked unified API/worker image as the candidate dependency artifact. This is a concrete specialization of separately prepared dependency custody, not approval of the current worker/pool startup and not a general restricted installer. It requires explicit approval of the selected profile's dependency semantics. Do not silently impose this on existing Python workers or workspace package management.

Preserve A: unchanged `features/utilities/workflows/check_integration_readiness.py`, actual public integrations.get/get_mapping, scoped successful readiness result and durable context/state/events. Preserve B: unchanged Cove Escalation Analyst declaration and both advertised tools; actual Agent→capacityWorkflow→answer→summary, real queue/worker/provider adapter, ordered tool/model steps, usage, attempts, committed run/output and required events. Preview stays advertised but uninvoked; bootstrap uninvoked. A simpler workflow or direct capacity call cannot replace these outcomes.

Source identities independently read: platform reference `/home/thomas/src/bifrost-agent-capacity-reference` clean HEAD `701aaccd7956b1a3224fc561e4edc72368e88ace`; workspace `/home/thomas/src/bifrost-workspace-rust-mvp-current` clean HEAD `6bb2399a4e185ffadbd2651953ab9c90320b4d5a`. Current workspace declaration plus four carried Python files equal every corresponding fixture byte. Fixture origin remains e860, distinct from current workspace and actual installation commit.

## What the source supports, and what it does not

Four carried inputs: `features/cove/workflows/recovery_steward.py` SHA256 `71bc320261ab6f6da2abb3b4f1bc3335fc9925ae28ac9e922a1f260e378529eb`; `features/cove/workflows/recovery_testing.py` `cf0876ecf4fd793f709e6070771e5c0f84dce34128581cf68049a1cce844879a`; `modules/cove.py` `778ec86607025eabf60efb768cf7f05b641a13532ea1686c19709211f5f52133`; `features/cove/__init__.py` `0498ef9c35aad1478ab89f334894b7bd3bb418b6901356530ad3e9384fb595a0`. Declaration `b79b5064f375098dabc119083cf2ff16778b683722b5df394f6cb8a06ca22914`. A readiness source `f49b1b935ef2467f66eccf3c4b2750773f664d1f0e41f8ff6b08e3f11035a1de`.

AST/source inspection finds stdlib, public bifrost and carried Cove imports, plus httpx. That is not the complete runtime closure: bifrost imports many SDK modules eagerly; decorators execute during module initialization; Cove/recovery modules create module/class/constants and invoke decorators before the target call. Readiness also invokes its decorator on import. All tenant initialization must occur after committed Start, regardless of apparently harmless statements. Preserve original module-load/context ordering until separately reviewed; do not move context ahead of import to simplify scope.

Root `requirements.lock` already selects httpx0.28.1, openai3.3.0, anthropic1.0.0, pydantic-ai-slim2.35.3/harness0.27.0, Redis7.4.0 and Azure libraries. The image installs the full lock using `--only-binary :all: --require-hashes`, and copies SDK/platform source. This covers the evident dependency needs of A, carried Cove imports, the actual provider adapter and summary source as a candidate; only supported startup/invocation can demonstrate the full installed closure. Neither a package-name inventory nor a source lock establishes the actual interpreter/site-hook bytes.

`api/Dockerfile` pins python3.14-slim base digest cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5, but apt installs remain repository-resolved and source is copied at build. Dockerfile provenance alone is not an immutable built-image identity. No existing image digest, preparation success or installed runtime was observed here.

Initialization is distinct: entrypoint repairs ownership when root, then gosu/exec; init_container runs migrations then warms requirements cache via RepoStorage. RepoStorage scopes `requirements.txt` to `_repo/requirements.txt`; async warming reads storage and sets Redis content/hash. The inspected initializer does not copy workspace root requirements.txt or synthesize default requirements. Missing/empty storage is a supported no-install result; read/cache errors can also collapse to absence in later worker reads, so absence cannot be certified by helper success/count0 alone. No actual storage/cache/default state was inspected.

Worker ProcessPoolManager.start always runs requirements_setup_helper before template; helper fetches mutable global requirements using Redis→authenticated API→object storage, and may re-cache. The worker treats API404 as authoritative no-install control flow; it is NOT evidence of object absence. get_requirements can return None after cache/storage failure, which the route maps to404. Auth/network/server failure falls through to storage. Installer reads another mutable snapshot for status. Template skips its own pip by default but still adds enabled user-site and preloads infrastructure before credential scrub. Current pool startup therefore is not a C2-safe prepared runtime even with an image containing every required package. Source loading likewise retains virtual-import/cache/storage fallback machinery; pinned Solution closure must be retained, not replaced by direct file imports or global repo fallback.

## Compare the two unapproved options

Prepared policy: dependencies execute/build only in existing image preparation, separate from coordinator/runtime credentials and tenant source. A admitted selected runtime starts from the exact immutable artifact without running helper/pip or mutable workspace package updates. Prepare can inspect staged bytes/metadata; tenant imports/decorators/provider probes remain after Start. Dependency-update coexistence is explicit generations: old runs retain their image, a new reviewed image is admitted for new runs; no in-place install/recycle against this profile. Ordinary Python users retain their existing mutable behavior.

Effectful post-Start policy: could preserve current install_requirements batch→per-package fallback, but only after durable Start and before tenant loading, within each run's restricted isolated writable dependency generation. Current pip call timeout is300s per batch AND per individual package; options/index/source tokens are reapplied to individual fallback. Batch failure, timeout or exception triggers fallback; partial success remains reported and does not itself prevent template startup today. Helper has no overall timeout; cancellation sends group TERM, waits5s then KILL. Moving this unchanged into a selected60s recipe cannot promise completion or preserve current fallback outcomes; imposing one run deadline can terminate before fallback/partial results. This is an execution-semantic decision, not a timeout adjustment.

Post-Start dependency hooks/network effects, partial installs, lost acknowledgement, kill/restart and installer descendants mean possible execution; no automatic replay or clean rollback inference. Existing package update/recycle installs before draining active children, so reuse would also preserve an unsafe shared coexistence surface. A new per-run installer mechanism, generic framework or fallback-policy rewrite is outside this proposal. Recommend against selecting this option for the bounded MVP now; if needed, STOP and obtain separately ratified deadline/fallback/cancellation/partial-failure semantics with independent source review.

## Concrete approval scope and owned candidate paths

Approve only the selected A/B profile's use of a separately prepared **actual digest-bound existing API image**, without fetching/installing workspace-global requirements in its C2 runtime. This is a proposed exception at an isolated adapter boundary, not deletion of required Python pip fallback. Reference Python startup/install/reference behavior remains unchanged and must be characterized under authoritative no-global-requirements state; stock workers/package jobs/defaults remain unchanged. Any selected input actually relying on global installed packages falsifies this choice.

Future implementation ownership proposed in platform repo: new `api/src/services/execution/core_mvp_runtime.py` and new `api/tests/unit/services/execution/test_core_mvp_runtime.py`; narrow optional selected-profile seams only in existing `api/src/services/execution/engine.py`, `api/src/services/execution/autonomous_agent_executor.py`, `api/src/services/execution/run_summarizer.py`; supported profile/receipt tests in new `api/tests/e2e/platform/test_core_mvp_runtime_dependencies.py`. These are exact candidate paths, not permission to implement them now. Existing selected extraction contracts/source/credential gates must be independently frozen before touching those seams. No process_pool/template/simple_worker/requirements_cache/package-manager/Dockerfile/lock/workspace-source changes are approved. Runtime orchestration/Compose/writer enforcement outside these paths needs its own explicit path-bounded approval.

Before implementation, independently approve the actual closed runtime invocation/entrypoint and authority projection. Do not use worker.main or the current pool as runtime entrypoint. Keep model loop, SDK, package behavior and summary implementation Python-owned; do not invent IPC callback/installer mesh. If engine/agent/summary seams cannot preserve the selected result without new authority or paths, escalate rather than broaden.

Closed artifact admission must bind: image digest and build/source recipe, platform commit/tree, complete lock and installed distribution/file hashes, actual Python executable/version and stdlib, SDK source origin/hash, sys.path/site/user-site/.pth/sitecustomize/import-shadowing behavior, startup/import/plugin closure and entrypoint. Freeze before interpreter startup; runtime imports use only admitted image plus exact immutable selected source generation. No mutable mounts/user-site/ambient preparation effects may supply dependencies. Actual supported readback is required; read-only source cannot prove this isolation.

Bind authoritative global requirements disposition for this isolated profile: exact storage/cache/API observations and absence/empty digest, no concurrent package updates, and actual installed package/file provenance. Existing default API lock is different from workspace-global requirements. Do not infer absence from empty Redis, inaccessible storage, API auth error or pip status counts. Nonempty global requirements or ambiguous custody STOP; do not delete them or mask fallback. No users/assets/keys/storage are provisioned by this proposal.

## Remaining SDK, issuer and writer fences

Dependency preparation approval does not release C2-W/C2-A. A scoped runtime credential must be issued outside runtime, preserve original caller/org/role/grants and supported public SDK/source behavior, and deny lifecycle create/claim/retry/finalize/cleanup plus prohibited SQL/HTTP/Redis-induced writes mechanically. AUTH-P1 caller propagation is not approval of a new purpose/issuer/policy. Runtime/installer/template receives no signing or lifecycle DML authority; prove environment/files/Settings reconstruction/cached clients/descriptors/profile/managed-identity fallback denial before startup. A permitted origin URL alone does not prevent token reuse elsewhere.

Preserve source/import generation and Solution global_repo_access=false; no mutable repo/cache repair writes confer hidden lifecycle authority. Preserve SDK integration/OAuth recovery, including true-refresh synthetic characterization; read-shaped integration lookup can acquire/update OAuth state and stays after Start. Required evidence/public event projection is independently session/fence-bound; raw log/result/source observations cannot finalize lifecycle ownership. Summary has its own model/lease/terminal writes today: retain selected behavior under reviewed control/runtime split, never call its current DML path with prohibited credentials.

## Falsification, STOP and smallest next step

Independent reviewer must reject if full selected import/provider/summary closure needs workspace requirements, approved immutable-source behavior requires fallback changes, existing image cannot be locked/read back, preparation imports tenant code, or isolation requires hidden coordinator/signing/writer authority. STOP for any pre-Start tenant/dependency effect, missing/ambiguous Start receipt, replay after possible execution, logging/secret sentinel failure, altered A/B workload, new installer framework or unapproved path expansion.

After explicit profile-policy ratification and independent source review, freeze adapter invocation/source/issuer/writer contracts; then separately authorize bounded implementation and supported proof. Prove unchanged A plus full B answer+summary, zero pre-Start effects, startup secret/logging sentinels, source custody, cancellation/disposal, stale-fence/direct/helper writer denial and mixed-writer rollback. No SDK/runtime invariant, nominal evidence, T/full-MVP completion, CI, merge or deployment is claimed here.

## Exact inspected platform source hashes (relative to named reference repo)

`api/Dockerfile` 911b84a6ef58f80ca7c0e456747bfdc42a6f89b11249d65af4c954e87381637c
`api/entrypoint.sh` d638f919a0b11e43cdac1c0c82678258f08811a659557bf78e90955b490167f7
`requirements.lock` be76160e9eb3b3ba9ab0d9af9e77f311db3578cfaf02b7042d9697bd7c2feea1
`api/scripts/init_container.py` 2bb87e240a50f0eba7c2e45ad108ebf7c72b54b575e97fa74eceb458ed2bab4c
`api/src/core/requirements_cache.py` 7b9cefb1a75f064df147e895d7c42a2c937e152f40e5d177f43c93771954079e
`api/src/core/module_cache_sync.py` b48230164915c92c7060a9ff743d0b7dd002a640915804d45e7e990fe3a813f1
`api/src/services/execution/simple_worker.py` e7a7ee3d25aa665cdce29b8be411a170c52cb9c03755a59ffcf6d5f3862ad0f6
`api/src/services/execution/requirements_setup_helper.py` 2c42df1c3e5535f5728bbb53845c6d467b2b656f0a440b34e8ec6085ff1b64e7
`api/src/services/execution/process_pool.py` 7d42d10965b21dd877df8a626d514b3bdbdb7f6510aea54e898c9fd0916a027c
`api/src/services/execution/template_process.py` 807190bdd26d044e8472758890186e6a7fb6387262bd514aa250f1296ddfab48
`api/src/services/execution/engine.py` 5ef3dd85c8a250c8c6b3de9adac19f8021cd755950beaffcd0c29826ec56fa68
`api/tests/e2e/platform/agent_reference_recipe.py` dc0c99869005f19a2a1c71ba7a868cd39c3beebd97bffa47f15a25d9cd872f98
`test-fixtures/agent-reference/provenance.json` 8e88c4972b5b578169cbb7df319ab21bbbd851523a7c9dcac27ad6aa533032fb

Source/AST/stdlib/read-only git only. No product/dependency/own-module import, tests, Docker/network/environment/credentials/CI/repo edits/commits or agents. Sole output is this <=140-line proposal; author cannot independently review it.

## Independent review

Original review SHA256:
`fd3fdcf705ab812303e9198095b48a7b02628c727f7a3b99377b9ad4c5abee28`.

# Independent C2 dependency policy proposal review

2026-10-02. **VERDICT: ACCEPT AS A CONCRETE USER DECISION PROPOSAL WITH REQUIRED ABSENCE-PROOF CORRECTION; DEPENDENCY SEMANTICS ARE UNRATIFIED. NO SOURCE BUILDER, DEVELOPER OR RUNTIME RELEASE.**

Reviewed `/tmp/bifrost-c2-dependency-policy-decision-proposal.md`, SHA256 `531d90493e7f5267c2f81e03a75873a9ac592d353b48b1ac7aa8d4b5aed21bde`; I did not author it. Engineering-flow `2026-09-30.1`, platform guidance and workspace AGENTS/Core/Testing source boundaries read. Source/text/stdlib hashes/read-only git only. No own/product/dependency imports, pytest/tests, Docker, network/runtime/credentials/environment/CI access, repository edits, commits or agents. Only this new report written; prior host report unchanged.

Exact reference `/home/thomas/src/bifrost-agent-capacity-reference` HEAD `701aaccd7956b1a3224fc561e4edc72368e88ace`; retained origin/main `01cadfe09710d293a40da14d6cf4056165289e31`; workspace `/home/thomas/src/bifrost-workspace-rust-mvp-current` clean HEAD `6bb2399a4e185ffadbd2651953ab9c90320b4d5a`. All13 proposal-listed platform hashes match. Main01 Dockerfile/lock/process_pool bytes independently match the reference. No current remote or deployed state is claimed.

## Selected A+B preservation and closure limits

Four carried Cove files independently equal current workspace bytes; declaration `agents/387064f6-af8d-4b88-97b4-adbf0207a2fa.agent.yaml` equals fixture declaration, hash `b79b5064f375098dabc119083cf2ff16778b683722b5df394f6cb8a06ca22914`. Its capacity-first prompt, conditional preview, both tool IDs, budgets and model remain intact. A readiness source independently hashes `f49b1b935ef2467f66eccf3c4b2750773f664d1f0e41f8ff6b08e3f11035a1de`; it calls public integrations.get and, for scoped requests, get_mapping. Its error/blocker/output projections must remain unchanged.

Source confirms carried imports are stdlib, public bifrost, admitted Cove modules and httpx. Decorators and module/class/constants execute on import. Platform `api/bifrost/__init__.py:82–149` eagerly imports many SDK surfaces; selected provider/model/summary imports add substantial transitive dependencies. Direct-import lists do not prove complete startup/invocation closure. Policy approval cannot move tenant import/decorators before committed Start or simplify capacity to a direct call. Preserve real A scoped readiness and full B Agent→capacity workflow→answer→summary plus events/usage/attempts, with preview advertised/uninvoked and bootstrap uninvoked. No reduced “passing” workload is accepted.

## Concrete correction: API404 is not object-absence attestation

`api/src/core/module_cache_sync.py:719–750` treats HTTP404 as authoritative for the worker's lookup branch. However `api/src/routers/sdk_modules.py:355–376` emits404 whenever async get_requirements returns None. `api/src/core/requirements_cache.py:48–71,219–248` can produce None after warming catches any storage/decode/cache exception and returns False. Therefore actual API404 alone can result from unavailable/broken storage, not just missing requirements. The proposal sentence “API404 is authoritative absence” must mean **worker control-flow classification only**, not custody proof.

This correction is required before presenting ratification as an independently reviewed evidence claim. Keep the proposal's stronger requirement for independent exact storage/cache/API observations and error distinction. Do not change endpoint or fallback behavior as part of this correction. Helper success/count0, empty Redis, cached empty content, init exit or404 cannot alone establish no-global-requirements state.

`api/src/services/repo_storage.py:21,53–76` maps requirements.txt to `_repo/requirements.txt`. Current init calls cache warming and performs no inspected copy of workspace root requirements/default synthesis; its stale “database/file_index” docstrings do not establish actual source. Initialization not copying requirements is **not** proof that storage is empty, that defaults never populated it elsewhere, or that no installed mutable packages remain. Those are unresolved actual custody facts.

## Image artifact: plausible candidate, not admitted environment

`api/Dockerfile` pins Python3.14-slim base digest, installs the full lock with binary-only/hash requirements, copies site-packages/platform/SDK and builds artifacts. It also executes repository-resolved apt and npm/source build steps. A base digest/lock/source recipe is not a resulting image digest or installed-byte manifest. Installed interpreter/stdlib/site hooks/import shadowing/package files/native libraries and provider closure remain unverified. The reference lane builds Dockerfile.dev, so its fixture CI image cannot silently supply production api/Dockerfile artifact custody.

An actual immutable prepared image can be the proposed dependency artifact, but existing Dockerfile does not mechanically seal it: pip/package tools remain present, default application startup/user-site behavior and writable paths remain possible. Configured image identity and absence declarations do not enforce immutability. Readonly mounts, admitted paths, environment/Settings/client/source generation and writer denial need their own reviewed runtime invocation and actual proof. The proposal correctly leaves these gates open; do not interpret “existing image” as existing C2-safe startup.

## Material semantic decision, not convenient equivalence

`simple_worker.py:79–157` deliberately implements mutable global requirements, batch pip then per-package fallback with source/index options,300s for EACH call, and partial results. `requirements_setup_helper.py:37–67` fetches another mutable snapshot for status. `process_pool.py:716,1726–1742` installs before template startup/recycle; `template_process.py:113–117,164–205` admits user-site/preloads before scrub. Shared mutable updates and fallback are real existing semantics; they cannot be declared preparation merely because the API image contains common dependencies.

The prepared policy **does exclude selected executions that need workspace-global installed packages or in-place package updates**. It therefore cannot claim general equivalence with public Python/workspace package behavior. This is not silent in the proposal: it expressly requires user ratification, rejects nonempty/ambiguous global requirements for this profile, retains ordinary Python package management/fallback, and stops if actual selected imports require it. Retain those explicit limits in the durable decision. Do not make nonempty state empty, ignore updates, replace source fallback casually, or choose a different workload to pass.

Moving existing pip behavior post-Start within a60s selected recipe changes possible fallback/partial/cancellation outcomes even without changing the installer function. A single deadline cannot preserve the source's potentially repeated300s calls. If the user requires mutable installation for selected A/B, that choice needs separate deadline/fallback/partial-failure/replay/coexistence decisions; this review does not approve a new installer framework.

Phrase “dependencies execute/build only in image preparation” must refer to **dependency installation/build hooks and preparation effects**. Library code necessarily executes during admitted runtime imports/invocations after Start. Tenant initialization/provider/OAuth probes remain runtime effects; public readiness labels “read_only” do not waive real OAuth refresh behavior or evidence/writer fencing.

## Minimum ratification scope and next gate

After incorporating the404 qualification, the minimum user decision is: **May only the exact selected A+B C2 MVP profile use an independently admitted digest-bound prepared API image and reject executions requiring mutable workspace-global packages/updates, while ordinary Python package management and batch/per-package fallback remain unchanged?** This is a bounded semantic exception, not approval to delete fallback or seal every workspace. If this restriction conflicts with the intended allowed workload, retain STOP and resolve that conflict explicitly.

Ratification would authorize the dependency-policy direction only. Exact adapter invocation, source/import generation, independent issuer/purpose/caller projection, writer denial, summary control/runtime split and proposed path ownership remain separately unreviewed/unreleased. Source-authoring gates can be frozen before actual runtime proof; immutable-environment acceptance cannot. No current worker/pool entrypoint is approved. No proposed engine/agent/summary seam is implementation approval from this dependency review.

Required falsifiers remain: nonempty/ambiguous authoritative global requirements; selected imports needing excluded packages; pre-Start tenant/dependency effects; mutable/site-hook/shadowing dependencies; hidden lifecycle/signing authority; altered A/B result/SDK/context behavior; missing summary/events; uncertain cancellation/disposal. Installed image/startup/zero-pre-Start/secret/logging/source/writer/closure evidence is absent. Host SOURCE STOP and #1017 STOP stay intact; host construction STOP is not global Rust falsification or C2 acceptance.

## Additional exact source hashes

All platform paths below are under `/home/thomas/src/bifrost-agent-capacity-reference`.

| Path | SHA256 |
|---|---|
| `api/src/routers/sdk_modules.py` | `e29562c6a1c1ae1be62c8d98c6d759c0e66ac6f323917e96e35c4d678a661793` |
| `api/src/services/repo_storage.py` | `707d7c899d5507b7e2c4311c42230e4ae2cfa275f44693b20b2b639c928b758f` |
| `api/Dockerfile` | `911b84a6ef58f80ca7c0e456747bfdc42a6f89b11249d65af4c954e87381637c` |
| `requirements.lock` | `be76160e9eb3b3ba9ab0d9af9e77f311db3578cfaf02b7042d9697bd7c2feea1` |
| `api/src/core/requirements_cache.py` | `7b9cefb1a75f064df147e895d7c42a2c937e152f40e5d177f43c93771954079e` |
| `api/src/core/module_cache_sync.py` | `b48230164915c92c7060a9ff743d0b7dd002a640915804d45e7e990fe3a813f1` |

Smallest next step: preserve this review and correct/freeze the precise policy proposal durably, then seek only the bounded user semantic ratification above. No tests/runtime/CI or implementation were performed or released.
