# Agent boundary for the Cloudflare experiment

Read the containing repository's AGENTS.md first. Work in a dedicated git worktree.
This subtree is an explicitly requested TypeScript runtime experiment, not a change
to the existing FastAPI/React platform. Its own core tests do not boot the Python stack.

Keep the source baseline traceable to gobifrost/bifrost. Do not reset shared branches,
merge the upstream CI tree into MTG main, or change production deployment paths.
Do not deploy, provision, enable paid plans or install account-level bindings without
an explicit user handoff. Do not expose this synthetic lab to customer data.

Preserve UNSET versus explicit global scope and the independent admin/provider-org
bypass flags. Do not claim the resolver alone ports upstream's full authorization
model. The lab token is not production authentication.

Workflows is the sole execution-status authority. Durable Objects own immutable
admission and deduplication, not a competing lifecycle. Non-workflow platform
operations must still use the canonical upstream PlatformJob architecture.

Run npm test for core changes. Cloudflare adapter/config changes also require
npm run check and npm run smoke against real local Wrangler. Never substitute
invented ambient Cloudflare types or fake ports for adapter/runtime validation.
Do not hide failed checks with retries, skips, longer timeouts or broad fallbacks.

Do not open a PR until the containing repo's exact-commit pre-PR gate is satisfied.
Report the actual worktree, tested commit, scoped results and unrun gates. No local
verification output in this lab is evidence that the full Python/React stack passed.
