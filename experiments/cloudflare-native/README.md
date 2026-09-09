# Bifrost: Cloudflare-native execution lab

**Status: first code slice; core tests pass; Cloudflare adapter/runtime validation is still required. No deployment has been performed.**

This isolated experiment is based on **gobifrost/bifrost**, commit
`0598020e32ea6367ecda69f548a53c00bb77bb6c`, not the MTG fork's main branch.
Only `experiments/cloudflare-native/` is added. Existing Python, React, migrations,
CLI/MCP contracts, platform-job infrastructure and deployment workflows are unchanged.
The containing repository's license continues to apply; this is not a separately licensed package.

## The question this slice answers

Can a small TypeScript control plane submit and observe a native Cloudflare
Workflow while retaining explicit scope authorization, requester isolation and
safe submission retries? It does **not** answer whether the whole Python workflow
SDK or Bifrost platform can already be replaced.

```text
Authenticated lab HTTP Worker
  -> ExecutionAdmission: SQLite-backed Durable Object
       immutable request + dispatch confirmation/deduplication record
  -> EchoWorkflow: Cloudflare Workflow with two checkpointed steps
       load immutable input -> return synthetic message
  <- status reads the native Workflow; no second lifecycle state machine
```

The adapter is `src/index.ts`. The independently tested domain, admission policy
and HTTP boundary are in the other `src/` files. There are no runtime npm
dependencies; Wrangler and TypeScript are development dependencies.

Only the built-in `lab.echo.v1` workflow is executable. No arbitrary code,
user-supplied URLs, shell commands, vendor calls or customer credentials are supported.
Queues, D1, KV, R2, Hyperdrive and Containers are intentionally absent from this slice.
Do not add a primitive until there is a concrete workload for it.

## Contracts and failure behavior

`POST /__lab/executions` requires the lab bearer token, `Content-Type:
application/json`, and a 16–128 character `Idempotency-Key` containing only
letters, digits, dot, underscore, colon or hyphen. Example body:

```json
{"workflow":"lab.echo.v1","parameters":{"message":"synthetic example"}}
```

An accepted submission returns HTTP 202, `executionId`, `reused`, `statusUrl`, and
a matching `Location` header. `GET /__lab/executions/{executionId}` returns the
native runtime status to the same requester. This is a **new experimental API**,
not a drop-in Bifrost API or a new production platform-job endpoint.

The admission ID hashes the requester, caller organization, effective organization
and idempotency key. Input is immutable once reserved. Repeating a key with
changed input returns 409. An omitted `scope` means the caller's organization;
explicit `null` means global and requires scope bypass. The port preserves
upstream's independent admin/provider-org bypass flags, but the local HTTP token
represents only an ordinary synthetic user.

A storage transaction reserves input before calling Workflows. Singleton
`createBatch` supplies native retained-ID deduplication. The admission record is
marked dispatched only after the native call acknowledges creation or reuse.
No 202 is returned until that marker is durable.

If dispatch or marker persistence fails, HTTP 503 says
`dispatch_unconfirmed_retry_same_key`: **the Workflow may already have started**.
The client must repeat the same request/key, not invent a new key. There is no
outbox or background recovery for an unaccepted request in this minimal slice.
Read requests never launch work.

Unconfirmed admission can be retried for 15 minutes from its original reservation.
Older ambiguity returns 409 `submission_recovery_expired` without relaunching.
This window assumes the provider's default retained-ID lifetime (documented as
3 days on Free, 30 days on Paid) and no manual deletion/reset of the native
instance, DO namespace or local persistence. Confirmed admission records are
retained indefinitely: replaying them never invokes create again, even after
native history expires. This deliberately trades bounded lab storage growth for
safe replay; a production retention/archival policy is **not implemented**.

A native status lookup failure returns 503 `runtime_status_unavailable`. The code
does not guess whether an error means expiration, deletion or provider outage.
Native error strings and arbitrary output fields are not passed through.
This is **not** an exactly-once guarantee for future external side effects.

## Local setup and checks

Use a dedicated worktree as required by the upstream agent guide. Node 22.16+
is required. From this directory:

```sh
npm install
npm test
npm run check
npm run setup:local
npm run dev
```

Run `npm run smoke` in another terminal while the local runtime remains up.
Setup generates an ignored `.dev.vars` with a random token and synthetic IDs;
it does not print the token or overwrite an existing configuration. Do not
commit this file. On Windows, keep the directory under your user profile and
apply normal local file-access controls; POSIX mode bits are not a Windows ACL.
The smoke test uses only `127.0.0.1:8787` and refuses redirects.

**Dependency lock:** npm registry access was unavailable during authoring.
Direct development versions are pinned, but no fabricated `package-lock.json`
is included. Install, review/audit and commit the real lockfile before CI/PR use;
subsequent installs should use `npm ci`.

`npm test` compiles only the pure core and uses Node's built-in test runner.
It does not type-check `src/index.ts` or emulate Cloudflare. `npm run check`
generates official Wrangler runtime/binding types and type-checks all source.
`npm run smoke` exercises actual local Wrangler/Workflows/DO bindings, not mocks.
See [VALIDATION.md](VALIDATION.md) for exactly which checks have been run.

## Safety boundaries and next step

Wrangler configuration defaults to `LAB_ENABLED=false`, no routes, no
`workers.dev` endpoint and no preview URLs. There is no deploy command, CI
integration, account ID, Cloudflare API token or vendor credential in this lab.
**These are exposure guardrails, not a spending cap or a prohibition enforced
by Wrangler.** Running a deployment command can still create billable resources.
Provisioning and deployment belong to the explicit Codex handoff, not this step.

The token is local lab authentication, not Entra/Bifrost authentication. The
upstream entity cascade, Solution scoping, RBAC, external-user rules and table
policies have not been ported. Do not put customer data into this lab, publish
it publicly, or connect privileged execution before designing those boundaries.
The synthetic message is stored in the DO and Workflow checkpoints; input-size
limits do not turn it into a suitable secrets channel.

`PlatformJob` remains the canonical owner of durable **non-workflow** platform
operations. This workflow-execution experiment must not become a competing
feature-specific production job system. Arbitrary Python workflow compatibility
requires an explicit executor/checkpoint design; changing the control-plane
language does not automatically change or resume user-authored Python workflows.

Continue with [CODEX_HANDOFF.md](CODEX_HANDOFF.md).

## Sources used

- [Pinned upstream agent rules](https://github.com/gobifrost/bifrost/blob/0598020e32ea6367ecda69f548a53c00bb77bb6c/AGENTS.md)
- [Canonical scope implementation](https://github.com/gobifrost/bifrost/blob/0598020e32ea6367ecda69f548a53c00bb77bb6c/api/shared/scope_resolver.py)
- [Full authorization model](https://github.com/gobifrost/bifrost/blob/0598020e32ea6367ecda69f548a53c00bb77bb6c/api/src/repositories/README.md)
- [Platform-job boundary](https://github.com/gobifrost/bifrost/blob/0598020e32ea6367ecda69f548a53c00bb77bb6c/docs/architecture/platform-jobs.md)
- [Cloudflare Workflows API: createBatch, retention and status](https://developers.cloudflare.com/workflows/build/workers-api/)
- [Workflow replay/idempotency rules](https://developers.cloudflare.com/workflows/build/rules-of-workflows/)
- [SQLite-backed Durable Object storage](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/)
- [Workflow local development](https://developers.cloudflare.com/workflows/build/local-development/)
- [Wrangler 4.130.0 release](https://github.com/cloudflare/workers-sdk/releases/tag/wrangler%404.130.0)
