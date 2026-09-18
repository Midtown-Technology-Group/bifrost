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
  typed values (`ConfigType`), a cached repository read path, and write surfaces
  through `/api/config` (`api/src/routers/config.py`), the MCP `configs` tool,
  and the manifest `configs` dict. Tests already exercise a boolean flag-shaped
  config (`api/tests/e2e/api/test_config.py:67`).

There is no third-party flag framework (no PostHog/LaunchDarkly/Unleash) and the
client has no flag hook.

## Proposal

Add a thin `feature_flags` service over `SystemConfig` and a flag lifecycle
convention. Do not add a vendor.

- Store flags under `SystemConfig` `category="feature_flags"`, `key=<flag name>`.
  A row with `organization_id=NULL` is the global default; a row with
  `organization_id=<org>` is a per-org override.
- `feature_enabled(db, key, *, organization_id=None, default=False) -> bool`
  resolves override → global → code default. Anything missing, unreadable, or
  malformed fails closed to the code default.
- Values are validated on write (a JSON object `{"enabled": <bool>}` or the
  boolean itself), reuse the existing cached `SystemConfig` read path, and
  invalidate on write.
- Every flag names an owner and a removal condition in one place, and is deleted
  when fully rolled out.

Keep static `Settings` for boot-time invariants and for safety rails where a
deploy gate is desirable.

## When to use which

| Need | Mechanism |
|---|---|
| Boot-time invariant or schema-coupled behavior | Static `Settings` |
| Destructive/activation rail that should require a deploy to flip | Static `Settings` |
| Staged rollout, per-org canary, runtime kill switch | Runtime `feature_flags` |

## Safety and tenancy

- Fail closed: an unknown, unreadable, or malformed flag is disabled.
- A per-org flag may only narrow or preserve behavior, never widen access,
  exposure, or blast radius.
- Writes are platform-admin only and emit the existing config audit event.
- No secrets belong in flags; use `Config`/integration secrets for those.

## Non-goals

- No third-party flag framework.
- No client-side flag hook until a UI actually needs to branch.
- This note does not change the enforcement of existing static safety rails.

## Acceptance criteria

- [ ] `feature_flags` service resolves override → global → default, is cached,
      and has unit coverage for each precedence step and the fail-closed case.
- [ ] Flag writes are admin-gated, validated, and audited; an e2e test covers a
      global default plus a per-org override changing behavior.
- [ ] A written convention defines owner + removal condition and where flags are
      registered.
- [ ] One real flag adopts the service (low-risk candidate chosen during
      implementation), demonstrating rollout without a redeploy.

## Open questions

- Do org admins read their own flags, or is this platform-admin only?
- Cache invalidation across API replicas: rely on the existing TTL, or publish an
  explicit invalidation event?
- Do we want a small UI surface, or API/CLI/MCP only at first?
- Should `workspace_release_retirement_enabled` / `workspace_release_activation_enabled`
  remain deploy-gated safety rails, or gain a runtime toggle once the service
  exists? (Current recommendation: keep them deploy-gated.)
