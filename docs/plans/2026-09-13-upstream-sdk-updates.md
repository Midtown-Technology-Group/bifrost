# Upstream SDK update integration

Upstream `bbc882a57eb3f77e48e4aa584efd5064579054d4` (#742) landed while the
artifact-hardening follow-up was under validation. It is integrated as a
separate batch because it adds deployed SDK provenance, retained source,
in-place SDK rebuilds, and table invalidation semantics across API, CLI, and UI.

Normal merge `ae5bd50b4` preserves the upstream parent. The eight conflicts were
reconciled individually: retain Midtown's `react-router` imports and platform
job registrations, combine SDK status with existing solution entity counts and
logos, and retain the imports required by each implementation.

The new `application.sdk_update` definition uses Midtown's explicit interactive
platform-job operations policy. `20260913_merge_mtg_sdk` joins the existing
Midtown migration head and upstream SDK-provenance migration without rewriting
either parent chain.

The upstream web SDK contract advances to version 2 because batch table writes
now send `table_invalidated` rather than per-row events. Existing built apps
require an explicit SDK rebuild to consume those frames. This web SDK contract
is separate from the CLI/server contract (currently version 12).

Validation is pending: focused SDK/source/build and realtime tests, API type
generation, CLI contract and skill tripwires, and the exact clean-commit full
gate. The preceding artifact batch owns the host's active heavy test run; this
batch has only undergone static parsing and migration-graph inspection so far.
