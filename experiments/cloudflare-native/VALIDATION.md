# Validation record — 2026-09-09

## Scope and environment

Upstream reference: `gobifrost/bifrost@0598020e32ea6367ecda69f548a53c00bb77bb6c`.
New source only: `experiments/cloudflare-native/`.
Authoring worktree: `/mnt/data/bifrost-cf-worktree` (Linux offline verification
workspace, not a full upstream clone). Node v22.16.0, npm 10.9.2, TypeScript 5.8.3.
Upstream source/rules were inspected through the connected GitHub API; direct
shell GitHub/npm DNS failed. No Cloudflare credentials or account were used.

## Passed

From `experiments/cloudflare-native/`:

- `npm test`: **110 tests passed, 0 failed, 0 skipped**. This invokes
  `tsc -p tsconfig.core.json` and `node --test test/*.test.mjs`.
- `node --check scripts/setup-local.mjs` and `node --check scripts/smoke.mjs`:
  syntax valid.
- Local setup script exercised in a temporary directory: creates a random
  64-character hex token, uses POSIX mode 0600, does not print that token,
  refuses to overwrite existing configuration and leaves its contents unchanged.
- Static Wrangler JSON assertions: lab disabled, routes empty, workers.dev and
  previews disabled, no account ID, SQLite DO migration declared, no deploy script.
- `git diff --cached --check -- experiments/cloudflare-native`: no whitespace errors.

Core coverage includes the upstream scope-rule table, canonical input validation,
tenant/requester-qualified IDs, immutable payload conflicts, admission retries,
ambiguous failures, recovery deadlines, permanent dispatch markers, requester-only
reads, native-status projection, fault serialization, HTTP authentication and
streamed byte limits. Test ports are explicitly fake: passing coordinator
concurrency tests is not proof of Cloudflare distributed execution semantics.

## Not run / not claimed

- `npm install`, `npm audit`, and a real dependency lock: registry unavailable.
- `npm run check` (Wrangler-generated types + all-source TypeScript check):
  Wrangler not installed; **src/index.ts has not been type-checked here**.
- `npm run dev`, `npm run smoke`, real DO/Workflow RPC and persistence/replay tests:
  no local Cloudflare runtime installed. The smoke script was syntax-checked only.
- Worker dry-run bundling, live deployment, real free-tier CPU/quota measurements,
  tenant/RBAC parity against the upstream runtime, vendor integration tests.
- Full Bifrost Python/React or `./test.sh pre-pr` suites: no full checkout or
  Dockerized stack available. This is not a PR-ready/production-ready declaration.

See CODEX_HANDOFF.md for the blocking runtime/dependency work and delivery gate.
