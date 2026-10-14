# Dependency simplification evidence — October 2026

Tracks [#1104](https://github.com/Midtown-Technology-Group/bifrost/issues/1104).
This is implementation evidence alongside the [initial hypothesis in #1105](https://github.com/Midtown-Technology-Group/bifrost/pull/1105); that documentation branch is unchanged.

## Baseline and reproducibility

Source: `02d1ca3d7d6c881699c5f637cab812d437bb9e8a`, clean MTG main on 2026-10-08. Upstream comparison: `gobifrost/bifrost` main `2e125fee861a4a3e8b8bd382711484b6d647033d` (2026-10-07). Ownership in the candidate inventory means file presence at that upstream ref, not an assertion that every current line is upstream-owned.

The API declares Python >=3.11; the published SDK declares >=3.10. Both API Dockerfiles pin a Python 3.14 slim digest, and the main lock was compiled with Python 3.14. The investigation host is `pve-t340`, Python 3.13.5; host Python was used for source/metadata analysis, not platform pytest. CI unit runtime reported Python 3.14.7 (run 37805471027); guest debug runtime readback remains unavailable.

- [Direct consumers](dependency-evidence/direct-consumers.md): all 72 root declarations plus SDK dependencies/extras, renderer and fuzzing inputs (105 declarations total, including duplicates).
- [Machine inventory](dependency-evidence/baseline-02d1ca3.json.gz): six locks, 193 main-lock packages, import/call locations, 166 dynamic-loading sites, script/container consumers and console entry points. `consumer_sites` indexes the `imports` array. Zero Python parse failures; 2,737 files with import evidence.
- [Exact locked metadata](dependency-evidence/locked-metadata.json.gz): PyPI `Requires-Dist`, Python markers, wheel filenames/sizes/hashes for all 193 main-lock distributions; zero retrieval failures. [Current releases](dependency-evidence/releases-2026-10-08.json) records separately retrieved latest versions and declared requirements.
- [Candidate records](dependency-evidence/candidates.json): 19 investigations with the complete component/evidence/ownership/behavior/alternative/version/opportunity/cost/risk/decision/verification/tracking fields.

Reproduce the static baseline from the recorded source, using the generator from this branch:

```bash
git worktree add --detach /tmp/bifrost-baseline-1104 02d1ca3d7d6c881699c5f637cab812d437bb9e8a
python3 scripts/dependency_inventory.py --root /tmp/bifrost-baseline-1104 --output /tmp/baseline.json.gz
```

The gzip output is deterministic. Resolver `via` descendants are attributed closure, **not exclusive removal cost**. Wheel bytes are compressed download sizes, not installed-image or RSS savings. Multiple locks have different Python/feature surfaces; differences such as renderer Pydantic 2.13.4 versus API 2.13.3 are not an in-process conflict by themselves.

Static evidence is incomplete for instance-method dispatch, aliases through reexports, package entry-point activation, generated source and configuration. Dynamic expressions remain explicitly unresolved. Workspace packages are installed at runtime through the package-management/worker path and are outside the platform lock; no default package is removed solely for lack of repository imports.

Notable non-import requirements: multipart is used by the web framework; greenlet by async SQLAlchemy; tzdata supplies zone files; pytest plugins use entry points/config; debugpy/Ruff/watchdog have command consumers; S3 stubs serve type checking; security floors constrain transitive packages. `setuptools>=78.1.1` is excluded as unsafe by pip-compile and has **no main-lock resolution**; the separate pip-tools lock is not proof of the runtime image's installed setuptools version.

## Prioritized decisions

| Priority | Component | Evidence and decision | Cost/risk gate |
|---|---|---|---|
| 1 | Editor regex | Replace private `re._parser` traversal with the already-installed timeout engine and request resource bounds. | Legal nested patterns become accepted. Oversized files are omitted and input exhaustion returns bounded results with `truncated=true`; per-file regex timeouts mark results incomplete; expired request deadlines and output exhaustion use controlled errors. Cursor closure and live proof required. |
| 2 | Package inventory | Consolidate three `pip list` paths into one bounded fresh-interpreter helper; async callers use `to_thread`, no cache. Native metadata substitution failed runtime parity. | Fresh startup discovers user sites and editable path configuration. Keep pip; verify runtime install/uninstall and worker Redis readback. |
| 3 | Python pgvector | No Python consumer found; existing list-based Vector adapter is shared upstream. NumPy's only lock parent is pgvector. | **DEFER**, tracked by [#1113](https://github.com/Midtown-Technology-Group/bifrost/issues/1113), until supported runtime-installed code compatibility is known. Keep database extension. Potential delta: one direct + one transitive package; NumPy's cp314 x86_64 Linux wheel is 16,637,700 bytes. |
| 4 | Dependency placement | Production and development share a lock containing real CLI, testing and debug consumers. | **DEFER** to [#1114](https://github.com/Midtown-Technology-Group/bifrost/issues/1114); split closures with clean image/wheel/archive/entry-point proof. Preserve libraries and advisory floors. |
| 5 | Cron descriptions | Current strings are asserted public behavior; CronTrigger's calendar numbering differs from croniter. | **KEEP** for transparent replacement. A UI representation change or cron-descriptor parity study is separate work. No description package added. |
| 6 | Container metrics | Current values include execution children and cgroup v1/v2; process/host counters have different scope. | **KEEP**. Collector migration needs a container-scope operational proof and deployment owner. No telemetry dependency added. |
| 7 | Agent compaction | Bifrost already calls Harness TieredCompaction/Clamp/Clear/SlidingWindow/WarnNearLimits. | **KEEP** native framework; preserve application wind-down, caller identity, cost tracking and subtree budgets. |
| 8 | Native retries | Framework transport is already used; the custom exhaustion path returns the final HTTP response to provider SDKs. | **KEEP** until a native API preserves error identity, guidance, six-attempt/60-second bounds and logs. |
| 9 | Tool result clamping | Custom model-only truncation retains full execution/audit results. | **DEFER** exact Harness ToolOutputLimits timing/type/retained-result parity. Avoid mixing upgrade and refactor. |
| 10 | MCP/FastMCP | Maintained protocol libraries own complex negotiation; current FastMCP is a prerelease even though stable 4.x now exists. | **KEEP**, upgrade only with paired compatibility/conformance proof. [#1106–#1109](https://github.com/Midtown-Technology-Group/bifrost/issues/1106) and [#1110](https://github.com/Midtown-Technology-Group/bifrost/pull/1110) already own Code Mode/version preflight. |
| 11 | HTTPX/aiohttp/httpx2 | aiohttp supplies CLI server/WebSockets and aiobotocore transport; provider SDKs require httpx2; HTTPX is widespread. | **KEEP** packages. Moving one JSON GET does not remove the dependency; narrow consolidation needs timeout/redirect/error/stream parity. |
| 12 | Settings/dotenv | Server validation and CLI allowlisted file reads have different precedence and security policy. Settings also depends on dotenv. | **KEEP**. Default BaseSettings substitution fails CLI override parity and provides no dotenv footprint removal. |
| 13 | Git/GitHub | GitPython local index/tree operations differ from HTTPX hosted Git Data APIs; PyGithub has real fixture use. | **KEEP** GitPython; investigate PyGithub placement under #1114. Do not inherit subprocess/security ownership for cosmetic savings. |
| 14 | libcst | Concrete transformations preserve comments/formatting in retained user source. | **KEEP**; ast.unparse is not equivalent. |
| 15 | JSON Schema/JMESPath | jsonschema validates arbitrary schema dialects; JMESPath implements a constrained event language. | **KEEP** mature evaluators; canonical DTO validation does not replace either contract. |
| 16 | Durable state/queues/cache | PostgreSQL lease/admission/token fencing, Redis ephemeral channels and RabbitMQ compatibility serve distinct roles. | **KEEP**. Harness snapshots/resume do not prove at-most-once admission or supported transport parity. Avoid rewriting Rust migration targets. |
| 17 | Auth/crypto/advisory floors | Difficult security protocols and intentional transitive constraints. | **KEEP**. No custom replacements; verify actual image build-tool provenance separately. |
| 18 | Fuzz launcher | Custom ClusterFuzzLite entrypoint replays inputs once, ignoring libFuzzer flags. Existing Atheris targets are unused. | **REPLACE**, tracked in [#1119](https://github.com/Midtown-Technology-Group/bifrost/issues/1119): maintained Python compiler, real mutation statistics, bad-build validation; deterministic pytest corpus fixtures remain. |
| 19 | Monaco runtime loader | Browser trace records a stalled CDN loader for 0.54.0 while the application locks 0.56.0. | **SIMPLIFY**, pending live proof, tracked by [#1136](https://github.com/Midtown-Technology-Group/bifrost/issues/1136): native bundled module/workers; preserve editor features and measure startup/bundle cost. |

## Replacement experiments

[Experiment fixtures](../../api/tests/unit/services/test_dependency_simplification_experiments.py) are run through the supported Docker lane:

```bash
./test.sh tests/unit/services/test_dependency_simplification_experiments.py -v
```

| Experiment | Proposed conclusion | Observable parity gate |
|---|---|---|
| Regex engine | GO with request bounds | Valid nested expression matches; adversarial nonmatch raises native timeout. Implementation tests add total deadline (including DB wait), input/output limits, zero-width result cap, cursor cleanup and immutable-overlay short circuit. |
| CronTrigger replacement | NO-GO | Its description is different; numeric weekday 0 is Monday, while croniter schedules Sunday. This does not test cron-descriptor, whose execution remains deferred. |
| Process instrumentation | NO-GO | Parent process CPU omits execution-child CPU represented in the cgroup fixture. Existing telemetry tests cover v1/v2, missing readings and working set. |
| BaseSettings for CLI dotenv | NO-GO | Process env wins by default, whereas explicit CLI load uses override=True; the credential allowlist remains enforced. Runtime metadata confirms settings still requires dotenv. |
| Native retry-loop deletion | NO-GO | Native validation exhausts by raising HTTPStatusError; Bifrost returns the final 429 for provider classification. Existing tests cover retry guidance, jitter, total budget and terminal errors. |
| Native package inventory | NO-GO; GO for shared pip helper | Initial inventory equality is insufficient: CI installed humanize successfully, but the running worker omitted the newly created user site. A subprocess fixture reproduces metadata-versus-fresh-pip visibility. Retain fresh discovery and consolidate duplicate wrappers. |

The separate import-isolation experiment `77a549514b22d4bd6fedfaea295e8d978cc1fd3a` is **GO for startup isolation**: two subprocess tests verify that actual cron/webhook harnesses do not load editor/ORM modules, and [comprehensive CI 38022836999](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/38022836999) passed. Initial observed RSS was approximately 100/94 MB versus approximately 146 MB before isolation; these are run observations, not a controlled benchmark. The implementation remains on a verification branch and is not part of #1137. Complete native cron-worker evidence remains NO-GO because output was truncated.

These are behavioral decisions, not claims that pending CI is green. Final run/head/result evidence is recorded below; failed experiments or missing live proof keep the implementation provisional.

## Current capabilities and upgrade implications

PyPI snapshots and authoritative release/migration sources were checked on 2026-10-08:

| Library | Locked | Newer release observed | Implication |
|---|---|---|---|
| Pydantic AI / Harness | 2.35.3 / 0.27.0 | 2.54.0 / 0.54.0 | Stable package releases; Harness remains pre-1.0. Already provides compaction; newer hook/persistence/default behavior needs migration tests. |
| MCP / FastMCP | 2.0.0 / 4.0.0b1 | 2.3.0 / 4.0.11 | Installed FastMCP is beta; stable 4.0 introduced protocol negotiation/MRTR/caching changes. Coordinate active capability work. |
| FastAPI / Starlette / Pydantic | 0.139.0 / 1.3.1 / 2.13.3 | 0.143.0 / 1.7.0 / 2.14.0 | PyPI is the timestamped release snapshot; web-rendered GitHub release pages lagged same-day releases. No scoped refactor justifies a framework batch upgrade. |
| SQLAlchemy / asyncpg | 2.0.49 / 0.31.0 | 2.1.4 / 0.32.0 | SQLAlchemy 2.1 changes runtime typing and relationship defaults; DB/lease authority requires separate broad validation. |
| APScheduler | 3.11.2 | 3.11.3 | Do not treat master/4.x architecture and migration documentation as installed stable behavior. It is not interchangeable with croniter. |
| HTTPX / aiohttp | 0.28.1 / 3.14.3 | 0.28.1 / 3.14.4 | WebSocket/server and SDK transport roles remain; native HTTPX does not remove them. |
| OTel SDK | 1.41.1 | 1.45.1 | Standard system/process instrumentation is not cgroup scope parity. |
| Python | API >=3.11; containers 3.14 | metadata API stable since 3.10 | `distributions()` predates this code; `asyncio.timeout()` is available at API minimum 3.11. SDK >=3.10 stays unchanged. |

Sources: [Pydantic AI releases](https://github.com/pydantic/pydantic-ai/releases), [Harness compaction](https://pydantic.dev/docs/ai/harness/compaction/), [step persistence](https://pydantic.dev/docs/ai/harness/step-persistence/), [tagged retry implementation](https://github.com/pydantic/pydantic-ai/blob/v2.35.3/pydantic_ai_slim/pydantic_ai/retries.py), [FastMCP 4.0](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.0.0), [APScheduler migration](https://apscheduler.readthedocs.io/en/master/migration.html), [SQLAlchemy 2.1 migration](https://docs.sqlalchemy.org/en/21/changelog/migration_21.html), [asyncpg releases](https://github.com/MagicStack/asyncpg/releases), [aiohttp changelog](https://docs.aiohttp.org/en/stable/changes.html), [OTel system metrics](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/system_metrics/system_metrics.html), [Python metadata API](https://docs.python.org/3/library/importlib.metadata.html), [regex timeout documentation](https://github.com/mrabarnett/mrab-regex/blob/hg/README.rst). No undocumented/private library API is introduced.

## Verification checkpoint — 2026-10-10

| Work | Exact candidate | Verified result | Remaining gate |
|---|---|---|---|
| [#1115](https://github.com/Midtown-Technology-Group/bifrost/pull/1115), pre-PR image preparation | `84a4ea065f4ac0d2fc0da87dd327bd294d5bdabb` | Comprehensive CI and native queue gates passed; maintainer explicitly approved the two shell files. **Merged** as `0a4db3d55388db9a2297e1a3c195a8628884ed3c`. | None for this implementation. |
| [#1116](https://github.com/Midtown-Technology-Group/bifrost/pull/1116), shared package discovery | `e048aa6baea7e7ba3be048a285141ca68fc60c9c` | [Comprehensive CI 38022624642](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/38022624642) passed; ARM64 worker compatibility 38022622539 and real-service delivery matrix 38022622537 passed. | Parent #1140, current-base queue evidence, live worktree proof. |
| [#1117](https://github.com/Midtown-Technology-Group/bifrost/pull/1117), bounded search | `712d12e7a0dd10c5f2afd40824997ebfccaec221` | Current CodeQL passed and all three alerts report fixed. PR API units: 13,674 passed, including all eight new/updated timeout cases. | Sonar and full workflows [38065113409](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/38065113409) / [38065116477](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/38065116477) pending at this checkpoint; live proof and review completion. |
| [#1118](https://github.com/Midtown-Technology-Group/bifrost/pull/1118), audit | Previous `7195361fb2a2ab4aaf439caa82ea6944193c6b6e` | [Comprehensive CI 38022560785](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/38022560785) passed; original seven experiment fixtures passed in 37808894104. | This documentation/candidate-record refresh requires fresh candidate verification. |
| [#1137](https://github.com/Midtown-Technology-Group/bifrost/pull/1137), maintained native fuzz compiler | `cf460fea922054ae6e4a97c3e461e18857e29dff` | [Comprehensive CI 38027179522](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/38027179522) and PR CI passed; Sonar new-code coverage 100%, zero new issues/hotspots; CodeQL passed. | Human review of shell/workflow/seeds explicitly required by exact-head Sonar evidence; complete per-worker native evidence remains open in #1119. |
| [#1140](https://github.com/Midtown-Technology-Group/bifrost/pull/1140), installed Monaco module/workers | `2e5f6cdd2465c64358ad9f0c379397861e7298c1` | [Comprehensive CI 38022602245](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/38022602245) passed after correcting both consumers to the public 0.56 `monaco.typescript` namespace. | Live worktree proof, review and current-base queue evidence. |

Queue admission is not merge acceptance. Signed snapshots preserve exact trees and archived local histories; GitHub signing records authenticated agent publication, not human review. The #1115 maintainer approval is limited to its named shell files and exact head. Sonar's explicit unsupported-path human-review requirement (`scripts/sonar/preflight.py:715`) is not satisfied by agent comments, signatures or workflow badges.

## Retained failures and experiment limits

- Audit head `af18a118b` failed Sonar with 0% new-code coverage for the two repository tools (167 executable lines), despite the seven replacement fixtures passing. Three new tests under the existing repository-tool CI discovery exercise tracked-file inventory, aliases/dynamic imports, optional declarations, parse failures, lock provenance, deterministic gzip/plain CLI output, and metadata markers/extras/missing or mismatched distributions. Local instrumented tool tests executed every statement: metadata checker 100%, combined branch coverage 99%; fresh Docker/Sonar acceptance remains required. No workflow, scope or exclusion change.
- Initial pre-PR run [37804299673](https://github.com/Midtown-Technology-Group/bifrost/actions/runs/37804299673) exposed MCP image creation inside the frozen provenance window. #1115 fixes preparation order while retaining image-change rejection. Earlier stale-main rejections and unsigned queue rejection are retained; neither was waived.
- Native package metadata substitution omitted newly installed `humanize` from a running worker's inventory. Fresh `pip list` remains required. The original initial-set fixture also failed canonical-name ordering; canonical ordering was corrected, and all seven fixtures later passed. This does not reverse the runtime user-site NO-GO.
- Search review exposed swallowed per-file regex timeouts. Current code propagates them, omits unfinished files with `truncated=true`, and raises the request-budget error when the deadline expires, including on the last file. Overlay/database, partial-match, cursor, adversarial and last-file regressions remain. CodeQL then exposed a merged literal/raw pattern variable and duplicate import; literal stdlib compilation now directly calls `re.escape`. A redundant positive nested-pattern test was removed; real timeout cases and the PostgreSQL successful-match/timeout regression preserve its behavior.
- Monaco's old CDN stalled the Custom Claim editor (#1136). Bundling exposed a real namespace compatibility defect in form/expression consumers, fixed through public `monaco.typescript` APIs. The editor chunk is 4,383.69 kB / 1,119.58 kB gzip versus the old local loader wrapper's 14.00 / 4.78 kB (whose editor arrived separately from CDN). Five on-demand worker assets total 9,458.73 kB raw. Static editor import adds startup download cost; this is not a footprint-reduction claim.
- Native fuzzing first found the incorrect object-only assertion on valid JSON scalar `4`; the harness now covers arbitrary JSON values without restricting the product parser. Graph adapter shape validation remains #1139. Earlier green native runs contained suppressed process timeouts; later runs generated enormous truncated logs. #1119 retains those incomplete results as NO-GO for complete mutation proof.
- Combined native search experiment `852f04391` completed four search workers with 25,701 total executions after its harness recognized the intended output-budget rejection; the original 1,562-byte crash seed remains. Import-isolation experiment `77a549514` completed four search workers with 31,312 runs and four webhook workers with 22,599,862; cron logs were truncated. These are specific experiment observations, not final-current-search-head coverage.
- #1137's prior 25% new-code coverage failure was repaired by executable native-entrypoint protocol tests (option identity, Setup/Fuzz ordering, mutated bytes delivered to real harnesses). The Atheris test double verifies that protocol; it is not native mutation proof. A Docker Hub authentication timeout occurred before tests and received one evidence-backed image-job recovery; the original failure is retained.

## Measured maintenance change

At the recorded heads, search and route remove 166 lines and add 150 (**net -16 owned runtime lines**), with ten private parser/traversal helpers removed. Package discovery removes 71 runtime lines and adds 33 (**net -38**); three discovery paths share one fresh-interpreter helper. Native fuzz implementation removes the 70-line replay adapter and yields **net -65 Python fuzzing lines**; the shell launcher, tests and seeds are measured separately. #1137 overall: 84 additions / 150 deletions in 14 files. Monaco trades the independently pinned CDN runtime for locked same-origin assets and explicit worker setup.

This task changes **zero declared dependencies or dependency versions**. Baseline root/lock counts remain 72/193 in the immutable inventory; later main security/build changes do not retroactively validate that baseline. The [constraint model](dependency-evidence/metadata-constraint-check.json) reports zero missing/version violations for the activated Linux x86_64 / Python 3.14.0 baseline closure; setuptools remains unresolved. Reproduce with `python3 scripts/check_dependency_metadata.py --baseline`. Without `--baseline`, the checker compares current root declarations with archived metadata and reports drift: current Pydantic AI 2.53.0 / Harness 0.36.0 roots differ from archived 2.35.3 / 0.27.0 metadata. The current lock agrees with current declarations; this is evidence age, not a demonstrated runtime conflict. Root markers, version constraints and all modeled platform fields are checked explicitly. This does not prove wheel ABI, image health or patch-version compatibility.

## Environment and cleanup limits

Investigation host: `pve-t340`. Dedicated guest VM101 `bifrost-platform-test-debian13-01`, 172.16.15.130; task worktree `/home/dev/test-worktrees/dependency-1104`, debug project `bifrost-debug-41c653ad`. That project was DOWN before this task. Attempting `./debug.sh up` after guest startup led to host OOM killing QEMU at 2026-10-08 11:49:59 local time. The last verified guest state is stopped; no blind restart, memory change, alternate production target or live behavioral acceptance is claimed.

Existing guest worktrees/volumes were preserved. Task-created guest debug cleanup is **unverified** while stopped; owner remains this #1104 work. The next cleanup step is guest recovery followed by inspection and teardown of only this Compose project. Keeper authenticated; legacy vault material failed guest SSH, while the host's installed route reached it. The NetBird hostname did not resolve on the host. No secrets are recorded.

Manual CI provides the exact-clean-candidate `./test.sh pre-pr` and Docker quality lane. Host Python is used for static inventory/metadata, not platform pytest or host services. Required live worktree proof remains distinct from CI integration results. No deployment, transport retirement, security exception, breaking DTO or broad upgrade is included. Engineering-flow/stewardship skills: 2026-10-06.1; installed MTG package read during this checkpoint: 2026-10-09.1.
