# Cloudflare-native lab

This directory is an explicitly requested, isolated platform rewrite experiment.
It does not change the existing Python platform, generated client DTOs, CI, or infrastructure.
Use local Node/Wrangler checks here; the root Docker test instructions still govern the existing platform.

Never provision or deploy Cloudflare resources, change account plans, wire CI secrets,
or contact real tenants as part of this scaffold. Resource work is a separate Codex handoff.
Never add live credentials or production hostnames. Do not expose fixture auth publicly.
Run `npm test` and `npm run typecheck:core`. With dependencies installed, also run
`npm run typecheck` and `npm run test:runtime`; distinguish unit checks from runtime checks.
Do not claim exactly-once side effects. New executors must enforce operation IDs at the destination.
Preserve versioned workflow definitions; do not change the meaning/order of persisted v1 steps.
Read README.md and HANDOFF.md before extending the experiment.
