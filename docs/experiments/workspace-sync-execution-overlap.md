# Workspace Sync execution-overlap regression

## Scope

`api/tests/e2e/platform/test_workspace_sync_execution_overlap.py` adds one real Docker scenario, with no runtime changes. It uses two workflow executions, one baseline Fetch and one native Sync. A table barrier holds execution A after its eager import. A test-only workflow admission barrier proves execution B has a durable, unclaimed dispatch before publication. Git jobs remain eligible.

The intended assertions are A retaining v1 entry/eager imports while its late import sees v2, and queued B loading all v2. This documents current loading behavior, not an atomic-release guarantee.

## Blocking observation, October 29, 2026

Base: `1fab1d0db69ffcc88968cc51b7b3ee097443de96`.

Two native attempts reached A's running barrier and B's pending dispatch, but failed before publishing v2. Fetch succeeded on the worker container. Sync ran on the scheduler container, which lacked `/tmp/git/.git/bifrost-storage-baseline`, and failed closed with `git_operation_failed`: the working copy must be fetched again. The second attempt verified corrected cancellation cleanup and reported only this primary failure, with no teardown error.

Second attempt job IDs:

- Fetch: `bbc55821-403c-4656-b38c-a44db5d0bdcc`, succeeded.
- Sync: `8852c651-e06b-47ce-b16c-61e61f2cf944`, failed.

`GitRepoManager.sync_down` writes the baseline locally. `sync_up` removes it before upload and rewrites it afterward, so it is not shared through workspace storage. `desktop_sync` takes the workspace lock and checks the local baseline without hydrating a different worker's checkout. Git jobs have no corresponding host affinity. The relevant Git job and repository-manager sources match staging pin `5f0c4900202429f9c83818a34387b97bb481a131`.

Do not merge this red regression as a green gate. Resolve cross-worker Git working-copy ownership/hydration explicitly first. Retrying Fetch until workers happen to match, forcing one worker, skipping the test, or weakening the baseline check would hide the finding. Blind rehydration also needs review because local Git state can include unresolved work.

## Validation and limits

Command: `BIFROST_SKIP_BUILD=1 BIFROST_TEST_IMAGE_TAG=bifrost-test-44976df9 ./test.sh tests/e2e/platform/test_workspace_sync_execution_overlap.py -v -s`.

The final test ran for 18.61 seconds, with no automatic retry. Ten adjacent Git-storage-freshness and module-isolation tests passed in the earlier focused run. Those passes do not establish this scenario's version assertions, which remain blocked at Sync.

This host's Docker AppArmor profile denied Unix socket creation. Validation used the unchanged canonical Dockerfile and an isolated Compose project with a temporary, uncommitted AppArmor exception. No host policy or staging configuration changed. Temporary harness selectors were restored. This is real API/queue/storage integration evidence, not proof under production hardening.

## Final committed-candidate requirement matrix

The complete selected suite was rerun against test-source commit `cec62f2b6` at 21:45 UTC with both adjacent files in the same Docker invocation. Result: **one failed, ten passed in 32.21 seconds**. No test-source changes were made after that run. This was a whole-candidate check, not a retry to obtain a favorable worker assignment.

| Requirement or risk | Concrete check | Observed result |
| --- | --- | --- |
| Native Git baseline is usable | HTTP Fetch, poll real `workspace.git` job, assert success and no changed files | Passed, job `54b6da21-22e7-4bff-aed2-a961af4a10ab` |
| A starts on v1 before publication | Execute through HTTP, wait for SDK-written table barrier after eager import | Passed before Sync assertion |
| B is genuinely queued before publication | HTTP enqueue, assert `WorkDispatch` row and API `Pending` status | Passed before Sync assertion |
| Publish exact v2 commit through native platform | HTTP Sync and real job completion, then compare commit SHA | Blocked: job `5137ee73-bf33-4241-99dd-327304755039` failed baseline preflight before commit comparison |
| A remains running and B pending through successful publication | Post-Sync API state assertions | Not reached because Sync failed |
| A retains v1 entry/eager code and late-imports v2 | Release barrier, await actual result, compare three returned version fields | Not reached because Sync failed |
| B starts entirely on v2 | Restore admission, await actual result, compare three returned version fields | Not reached because Sync failed |
| Failed scenario does not mask its cause or leave held executions | Finally restores handlers, cancels active executions through API, polls terminal state, deletes fixture resources and restores config/storage | Completed without secondary failure; only the Sync assertion failed |
| Platform edits are not overwritten | `test_platform_edits_after_fetch_are_never_overwritten` | Passed |
| Discard updates search index and storage together | `test_discard_restores_the_search_index_with_storage` | Passed |
| Credentials stay off disk/storage | `test_token_never_reaches_disk_or_storage` | Passed |
| Disconnect removes credentials | `test_disconnect_removes_stored_credentials` | Passed |
| Merge conflicts are not published by discard | `test_discard_during_merge_never_publishes_conflicts` | Passed |
| Abort followed by discard publishes revert | `test_discard_after_aborting_merge_publishes_the_revert` | Passed |
| Switching solutions evicts prior modules | `test_switching_solution_evicts_other_solutions_module` | Passed |
| Same-solution module reuse remains intact | `test_same_solution_keeps_its_module` | Passed |
| Async execution delegates once | `test_execute_async_delegates_once_to_shared_execution_core` | Passed |
| Failed execution refreshes modules and clears context | `test_run_execution_refreshes_modules_and_clears_context_on_failure` | Passed |

Adjacent storage and module tests exercise their own service/unit boundaries, not the new overlap acceptance path. Their success cannot replace its blocked version assertions. Packaging validation used the canonical built Docker image and mounted final source; Docker Ruff passed. No package or runtime interface was changed. No global test suite or production-hardening qualification is claimed.

Exact command appends `tests/e2e/platform/test_git_sync_storage_freshness.py tests/unit/test_solution_module_isolation.py` to the overlap test selection above. Initial stack restart attempts executed zero tests until the existing local AppArmor exception was also selected for template initialization. The exception remained local and both harness selectors were restored afterward.

Local logs and JUnit receipts are preserved under `/root/.jcode/scratch/migration-20261029/validation/workspace-sync-overlap/`, including `whole-candidate.log` and `whole-candidate.xml`. No deployment, upstream push, or merge is part of this change.
