# Runtime feature flags over SystemConfig — design note

Status: proposed
Motivating example: `workspace_release_retirement_enabled`

## Problem

Platform behavior gating is currently ad hoc. New capability is usually hidden
behind a static `Settings` boolean, which can only be flipped by changing an
environment variable and redeploying. At the same time the platform already
stores org-scoped configuration in `SystemConfig`/`Config`, but nothing treats
that store as a feature-flag substrate and there is no convention or lifecycle
for a flag. The result is that we underuse flags where they would help most
(staged rollout, per-org canary, runtime kill switch) and risk flag sprawl where
we do use them.

The retirement of the immutable Workspace Live release is the concrete case.
`POST /api/workspace-promotions/live/retire` is gated by
`workspace_release_retirement_enabled` (`api/src/config.py:117`), a deploy-time
`Settings` boolean defaulting to off. Enabling it for one environment is an
infra change plus redeploy; there is no per-org enablement, no canary, and no
runtime off switch.

## Current state

Two mechanisms already exist:

- **Static `Settings` flags** — `api/src/config.py` (`env_prefix="BIFROST_"`):
  `workspace_rapid_promotion_preview_enabled` (`:92`),
  `workspace_rapid_promotion_draft_upload_enabled` (`:99`),
  `workspace_release_prepare_canary_enabled` (`:106`),
  `workspace_release_activation_enabled` (`:113`),
  `workspace_release_retirement_enabled` (`:117`), and the three-state
  `workspace_promotion_diagnostics_mode: off|shadow|enforce` (`:121`).
  Flip requires an env change and restart.
- **Runtime org-scoped config** — `Config`/`SystemConfig`
  (`api/src/models/orm/config.py:23`, `:82`) with optional `organization_id`,
  typed values (`ConfigType`), and write surfaces through `/api/config`
  (`api/src/routers/config.py`), the MCP `configs` tool, and the manifest
  `configs` dict. Tests already exercise a boolean flag-shaped config
  (`api/tests/e2e/api/test_config.py:67`).

`SystemConfig` has **non-unique** indexes only (`api/src/models/orm/config.py:98`
–`102`), and the existing OAuth/GitHub helpers `SELECT` before inserting, which
allows duplicate rows under concurrency. Caching is documented for `Config` via
an `OrgScopedRepository`; there is no `SystemConfigRepository`, and existing
`SystemConfig` services read and write the table directly.

There is no third-party flag framework (no PostHog/LaunchDarkly/Unleash) and the
client has no flag hook.

## Proposal

Add a thin `feature_flags` service over `SystemConfig` and a flag lifecycle
convention. Do not add a vendor.

**Registry.** Flags are declared in one code registry (for example
`api/src/services/feature_flags.py`, `FEATURE_FLAGS: dict[str, FlagSpec]`). A
`FlagSpec` carries the safe default, an owner, a removal condition, and a
rollout-safety class. Writes are rejected for keys absent from the registry, so
the registry is the single source of truth.

**Storage and uniqueness.** Flags live under `SystemConfig`
`category="feature_flags"`, `key=<flag name>`. A row with `organization_id=NULL`
is the global default; a row with `organization_id=<org>` is a per-org override.
The design adds scope-aware uniqueness — a unique index on
`(category, key, organization_id)` plus a partial unique index for the `NULL`
global scope — and writes use an atomic upsert instead of select-then-insert.
This closes the duplicate-row hazard the OAuth and GitHub helpers work around,
and flag reads never have to pick an unordered "first" row.

**Evaluation.** `feature_enabled(db, key, *, scope: Scope) -> bool` resolves
per-org override → global row → the registry's safe default. `scope` is
explicit: `Scope.global_()` or `Scope.organization(org_id)`. There is no
implicit `None`-means-global, so a tenant-scoped caller cannot accidentally read
the global value by omitting context; call sites bind the scope from request or
job context. The fallback is never caller-supplied: an unknown, unreadable, or
malformed value returns the registry default (fail-closed `False` unless a flag
is explicitly registered with a justified safe default).

**Cache and write path.** The service is backed by a new
`SystemConfigRepository` with an explicit cache and invalidation, rather than
relying on a `Config`-only cache that does not cover this table. Reads are cached
per `(category, key, scope)`; every successful write invalidates the affected
key and publishes an invalidation so peer replicas converge within a bounded
interval (TTL as the backstop). The bound is an acceptance criterion.

**Org overrides.** A per-org override may only narrow or preserve behavior. Each
`FlagSpec` declares a rollout-safety class; flags classified as
access-expanding (or otherwise unsafe to vary per tenant) reject organization
overrides and are global-only.

Keep static `Settings` for boot-time invariants and for safety rails where a
deploy gate is desirable.

## When to use which

| Need | Mechanism |
|---|---|
| Boot-time invariant or schema-coupled behavior | Static `Settings` |
| Destructive/activation rail that should require a deploy to flip | Static `Settings` |
| Staged rollout, per-org canary, runtime kill switch | Runtime `feature_flags` |

## Safety and tenancy

- Fail closed: an unknown, unreadable, or malformed flag returns the registry's
  safe default; callers cannot pass a `True` fallback.
- Scope is explicit at every call; `organization_id=None` is never an implicit
  global read.
- A per-org override may only narrow or preserve behavior. Access-expanding
  flags are registered global-only and reject org overrides.
- Writes are platform-admin only, validated, and emit the existing config audit
  event; unknown keys are rejected.
- No secrets belong in flags; use `Config`/integration secrets for those.

## Non-goals

- No third-party flag framework.
- No client-side flag hook until a UI actually needs to branch.
- This note does not change the enforcement of existing static safety rails.

## Acceptance criteria

- [ ] `feature_enabled` resolves override → global → registry default, with unit
      coverage for each precedence step, the explicit-scope contract, and the
      fail-closed case.
- [ ] `SystemConfig` gains scope-aware unique indexes and an atomic upsert;
      concurrent writers cannot create duplicate flag rows.
- [ ] A `SystemConfigRepository` provides the cache and write invalidation; a
      test proves peers converge within the documented maximum stale interval.
- [ ] Flag writes are admin-gated, registry-validated, and audited; an e2e test
      covers a global default plus a per-org override changing behavior, and a
      global-only flag rejecting an org override.
- [ ] The registry defines owner + removal condition and the rollout-safety
      class, and where it lives.
- [ ] One real flag adopts the service (low-risk candidate chosen during
      implementation), demonstrating rollout without a redeploy.

## Open questions

- Do org admins read their own flags, or is this platform-admin only?
- Do we want a small UI surface, or API/CLI/MCP only at first?
- Should `workspace_release_retirement_enabled` / `workspace_release_activation_enabled`
  remain deploy-gated safety rails, or gain a runtime toggle once the service
  exists? (Current recommendation: keep them deploy-gated.)
