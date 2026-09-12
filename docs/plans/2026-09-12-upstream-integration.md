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

The fork owner approved changing the queue method to `MERGE` on 2026-09-12.
The ruleset was updated and read back to verify that its merge method was the
only policy change. Queue limits, conditions, enforcement and bypass actors
remain unchanged.

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

## Redesign reconciliation

The next merge records upstream `3c6590a45` as a parent. This upstream commit
combines the shared visual system, responsive page layouts, Home collections,
entity logos, dependency availability, and browser acceptance coverage. Review
its backend contracts and shared components separately from page presentation.
The fork's existing execution and source-governance behavior remains required.

Conflict decisions:

- Keep the consolidated `react-router` package for the host SPA. Preserve the
  app-facing `react-router-dom` name in native and user-dependency import maps,
  and in embedded app fixtures. Standalone app dependencies retain their own
  router contract.
- Keep immutable-source mutation guards, execution attempt history, browser
  WebMCP tools, and conditional event criteria. Integrate those controls into
  upstream's new layouts rather than removing them with their old containers.
- Keep the `log1:` cursor format and numeric-offset compatibility. Adopt the
  upstream workflow/global filters and stricter malformed-cursor rejection.
- Keep report script and event-handler stripping. Adopt the isolated report
  iframe and blocked-popup feedback with an empty sandbox policy. Both inline
  and popup tests inspect the sanitized frame content.
- Preserve explicit MCP authorization-server issuer metadata, manual issuer
  entry, and rejection of OAuth configuration without an issuer. The upstream
  discovery test must supply valid issuer metadata.
- Preserve worker runtime labels and configured capacity across partial
  heartbeats. A runtime change clears the previous label. Focused component
  tests cover both transitions.
- Preserve the JSON/YAML editor's blank-buffer and validator semantics while
  adopting the shared editor layout.
- Adopt upstream's explicit new-conversation draft transition in the route
  reveal key. Ordinary conversation navigation receives its own pathname key;
  Settings, account settings, and app runners retain their shared shells.
- Retain upstream's rewritten per-mapping OAuth browser test: it now creates
  deterministic fixtures and replaces the old opportunistic test removed by
  the fork in `297d2bea8`.
- Join the independent migration branches with
  `20260912_merge_mtg_redesign`; neither existing migration chain is rewritten.
- The CLI contract changes are additive metadata. Refresh the fingerprint
  without changing the compatibility version.
- Keep the fork's CI deployment boundary and browser artifact path. Adopt
  upstream's test-stack command lock and skill-mirror checker.

Regenerating types against the running modernization API produced no diff.
All 370 focused backend, live endpoint, contract and Tailwind compilation checks
passed without skips. Python quality checks and TypeScript passed. Browser
checks cover collections, logos, event criteria, MCP management and per-mapping
OAuth. A Home launch failure exposed an app import-contract mismatch; the
restored contract passed Home launch and preview-to-publish browser checks.
A component regression also covers the map used by apps with extra dependencies.
The first full client run passed 3,036 tests and exposed five fixture failures:
OAuth and MCP callbacks needed branding context for the shared auth transition,
and the message-ID test still expected the old timestamp-only fallback. The
callback fixtures now supply branding, and the ID test verifies distinct IDs
within one millisecond. All seven tests in those three files pass after repair.
The clean pre-PR gate remains required before publication.
