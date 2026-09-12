# Upstream integration through 0428e0fb8

The source integration starts at fork `fb3e1b154` and targets upstream
`0428e0fb8d4ecfc1ca0feabffd0780aea94e5d3d`. The existing cross-repository
PR #706 includes 1,406 files. Its common ancestor with the fork is
`0598020e32ea6367ecda69f548a53c00bb77bb6c`, preserved by integration PR #702.

## Review batches

1. Backend changes through `8af322ac05e6234039d76f376a6bed38deab01e4`.
   Each upstream commit has its own two-parent integration commit.
2. Workspace modernization, upstream `3c6590a45` and PR #732.
3. Table batch-write consolidation, upstream `0428e0fb8` and PR #735.

Each batch must contain current fork main and pass its candidate checks before
merging. Final acceptance requires both the starting fork head and the pinned
upstream target to be ancestors of fork main. No published history is rewritten.
Source integration does not authorize an infrastructure rollout.

## Backend reconciliation

| Upstream | Change | Fork reconciliation |
| --- | --- | --- |
| `f5a0ab4a6` | Atomic bulk table upserts | Preserve conditional-update DTOs and SDK exports. Add a live regression proving bulk replacement invalidates a previously reviewed revision. |
| `668b2676a` | Shared module refresh | Move the fork's generation-aware refresh into the shared module. Retain immutable release roots, batched hash checks, execution-scoped import hooks, worker telemetry and existing entry points. Import lazy consumer loading. |
| `08a8f58bc` | Event transaction ordering | Commit delivery rows before enqueueing. Preserve criteria-derived terminal event outcomes. |
| `0247d6f2e` | Document-ID keyset pagination | Import alongside existing JSON filtering, policies and conditional writes. |
| `3543c7ebe` | Large-response memory handling | Remove request-time allocator trimming, import live regression and sampler changes, preserve fork middleware, and commit file-deletion effects before returning. |
| `e9b66020d` | Long-running completion tracking | Add compact renewable leases and database metadata recovery. Keep durable attempt fencing, callback persistence, cancellation handling, admission accounting, template lifecycle and immutable duration bounds. Attach parent-owned sync metadata before every callback, including fork shutdown. |
| `8af322ac0` | Indexed document-ID batch queries | Import bounded physical-ID filtering without changing JSON field filtering or table ownership checks. |

Regenerate OpenAPI output from the integrated running API. Upstream generated
files must not replace fork-only contracts. Preserve the gobifrost-only deployment
guard, Azure image ownership, and the fork exemption from upstream website gates.

The completion merge also corrects the test runner's JUnit destination to the
container's mounted `/tmp/bifrost`, retaining reports after runner removal.

## Merge policy

At inspection, active ruleset 22240877 requires a merge queue configured with
`merge_method: SQUASH`. This conflicts with ancestry preservation. Resolve that
policy conflict before landing any integration batch; do not silently squash.

## Validation status

The first two integrations passed 56 focused unit tests. Subsequent combined
checks exposed test fixtures and expectations that needed to retain the fork's
retry-safe query flag, live template validation and attempt-fencing contract.
Those were corrected. Completion-order mocks now patch the consumer's actual
import and no longer enter repository code unintentionally.

The corrected combined run passed 243 unit, live API and query-plan tests with
no skips or failures. It covers pool lifecycle, sync routing, immutable duration
bounds, bulk upserts, stale conditional writes, physical-ID pagination and batch
filters, table policies, large-response memory behavior and execution recovery.
API Pyright and Ruff passed. DTO parity, contract fingerprint and generated
appendix freshness passed in the earlier combined run. The MTG CI boundary check
and shell syntax check passed. Both the fork base and selected backend head are
ancestors of the integration branch.

The delivery PR must record client checks and the full pre-PR result for its exact
clean candidate. Final acceptance still requires the modernization and batch-write
consolidation integrations and ancestry verification on fork main.
