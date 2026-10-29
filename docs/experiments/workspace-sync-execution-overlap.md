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

Local logs and JUnit receipts are preserved under `/root/.jcode/scratch/migration-20261029/validation/workspace-sync-overlap/`. No deployment, upstream push, or merge is part of this change.
