# Upstream Python compatibility boundary

Status: proposed compatibility test plan plus an executable conservative source
tripwire. Invariant: an upstream Python change may require a Python adapter change,
but must not change Go or the shared profile unless neutral semantics actually
change. No Python implementation is accepted as safe by this source snapshot.

## Executed tripwire and limitations

[upstream-boundary.json](../../../contracts/runtime/v1/execution-profile/upstream-boundary.json)
pins all `api/**/*.py` bytes, including SDK, execution, worker, auth, agents, tests
and newly added paths, without exclusions. A deterministic SHA256 commits sorted
UTF-8 repository paths and file bytes, each prefixed by an eight-byte big-endian
length; file count is checked too. Any modification/addition/removal fails with
`SourceReviewRequired`. This conservative check catches each listed drift class
without pretending keyword/AST pattern matching proves safety. Harmless Python
changes also require a reviewed baseline update. It does not inspect installed
packages, native libraries, non-Python scripts, deployment configuration or
runtime effects; existing unsafe paths remain in the baseline as known extraction
gates. A matching snapshot means unchanged source, not runtime conformance.

Baseline updates must identify the exact new source, reconcile adapter dependency
closure and run the relevant actual-process tests below before claiming accepted
adapter compatibility. For ordinary platform-only changes, review may record that
extraction acceptance remains held and update the source tripwire without claiming
process proof. Never suppress this check, add path exemptions or adjust Go/shared
schemas merely to absorb a Python-specific implementation detail.

## Required actual-process red-capable tests (not run in this PR)

| Drift | Concrete seam and negative signal | Accepted implementation response |
| --- | --- | --- |
| Pre-Start tenant initialization | `api/src/services/execution/{process_pool,template_process,module_loader,virtual_import,worker}.py`; sitecustomize, import hooks, decorators/default expressions and dependency startup canaries must produce zero effects before Start+valid actual provision, including malformed/cancelled admissions | inert trusted Python bootstrap/custody shim; same Go initializer vector stays unchanged |
| Direct lifecycle SQL/Redis writers | `execution/{async_executor,attempts,service,agent_run_service,autonomous_agent_executor,run_summarizer}.py`, worker consumers, repositories and SDK HTTP routes; run hostile raw SQL/Redis/HTTP and ordinary inherited helper calls through distinct non-owner/non-superuser runtime identities and actual pool; zero terminal/claim/retry/event writes | parent-owned observations/projections and mechanical writer exclusion under #1011 |
| Credential fallback | `api/bifrost/{client,credentials,_context}.py`, `execution/{worker,worker_sdk_http,template_process}.py`, `api/src/core/{auth,security}.py`; seed env, file, keyring, cached Settings, inherited descriptors and identity endpoint canaries; denied/missing scoped provision must neither select nor deliver broad credentials | mandatory scoped provision shim with no runtime fallback; ordinary CLI credentials unchanged |
| Independent finalization | `api/src/jobs/consumers/workflow_execution.py`, execution/agent helpers, `api/bifrost/executions.py`, terminal routes; emitted success/Stopped/lost Receipt cannot finalize, republish or replay under hostile calls; simultaneous Cancel/Result has one owner projection | translate to common observations and durable owner acceptance |
| Mutable dependencies | `execution/{requirements_setup_helper,simple_worker,process_pool,template_process}.py`, `api/src/core/requirements_cache.py`; mutated requirements endpoint/lock/image and pip/build hooks after Prepare must be rejected without fetching/installing at execution | immutable accepted image/dependency closure; custody failure stops Python extraction |
| Python-only common fields | closed `profile.schema.json`, `binding.schema.json` and structural/wire negatives reject interpreter-specific fields in lifecycle types, injected flags and unknown authority fields | adapter-local implementation detail; shared artifact classes remain semantic and language neutral |

Run the unchanged Go canary and shared result/SDK oracle after accepted Python
adapter changes. Deliberately introduce each unsafe change and require RED, then
correct only the Python shim and require GREEN with unchanged Go/profile vectors.
Retain full source/image/dependency/adapter hashes, actual runtime-role readback,
private-channel correlation, effect oracle and owned descendant cleanup. Re-run
all P0 codecs/interchange unchanged. AST/import inventories and digest checks
supplement, but cannot replace, actual process and ingress proof.
