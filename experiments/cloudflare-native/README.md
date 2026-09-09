# Bifrost: Cloudflare-native control-plane experiment

**Local-only, synthetic workload. Not a deployable replacement for Bifrost.**

The first slice is intentionally small: accept an authenticated execution request,
reserve an idempotent identity, start a versioned Cloudflare Workflow, and observe
its result. Nothing here calls a vendor, customer endpoint, existing Bifrost API,
or paid external service. No resources have been provisioned.

## Layout and ownership

```text
POST /lab/executions
  -> Worker: fixture authentication, strict input validation, bounded body
  -> ExecutionAdmission Durable Object: atomic admission record per execution
  -> InventoryWorkflow: durable steps, bounded retries, authoritative lifecycle
  -> synthetic Executor: one simulated operation per demo target

GET /lab/executions/:id
  -> Worker authentication
  -> Durable Object owner check
  -> Workflow status/result
```

The Durable Object does **not** run another execution state machine. It owns only
request identity, immutable input, and a launch receipt. Workflows owns execution.
Queues would duplicate dispatch for this slice, so they are not bound. D1,
Postgres/Hyperdrive, R2, KV, Containers, and infrastructure-as-code are deferred
until a representative use case requires them. The tiny HTTP surface uses standard
Request/Response APIs; adding Hono is an independent future routing decision.

## Relationship to the current platform

Source baseline: `Midtown-Technology-Group/bifrost`, commit
`fb3e1b154cf52bfe83739e90f5e0b86966a31029`.

Read before building this experiment:

- `AGENTS.md`: platform engineering boundaries and canonical background-job guidance.
- `docs/architecture/platform-jobs.md`: platform jobs are distinct from workflow execution.
- `api/src/models/contracts/executions.py`: execution inputs use `workflow_id` and
  `input_data`; organization overrides and impersonation require privileged handling.
- `api/src/models/enums.py`: established execution status names.

This experiment borrows those input names and unambiguous status labels, but
`/lab/*` is an explicitly separate versioned API, **not** an implementation of
Bifrost's existing SDK/OpenAPI contract. `Paused` and `Unknown` are lab-only status
labels; `runtime_status` is included to avoid hiding differences between platforms.
No production models, client types, migrations, runtime, CI, or deployment files
are changed. In particular, this does not create a parallel production PlatformJob
system.

**The major unproven capability is dynamic Python automation.** A deployed
TypeScript Workflow is not a drop-in runtime for workspace Python functions,
imports, SDK calls, PowerShell, or arbitrary native dependencies. This slice proves
control-plane contracts, not language or integration parity. An executor protocol
and a representative existing workflow migration are necessary before judging the
rewrite viable. Do not rewrite working Python executors into Go merely for the
sake of a language change.

## Run the independent checks

Node 22.16+ is required. The Node tests need no npm packages or account credentials:

```sh
cd experiments/cloudflare-native
npm test
```

The standalone core can be checked with an installed TypeScript 5.8.3 compiler:

```sh
npm run typecheck:core
```

With npm network access, install the pinned development tooling and generate the
Cloudflare runtime types. Commit the resulting package-lock.json after validation:

```sh
npm install
npm run typecheck
npm run test:runtime
```

The runtime smoke script creates a temporary local Wrangler configuration and
local state, exercises real local bindings, then restarts Wrangler to check
persistence. It deletes its temporary state afterward. It does not log in,
provision resources, or deploy. Fixtures use two disposable identities.

For interactive local development, copy `.dev.vars.example` to `.dev.vars` and run
`npm run dev`. It binds to `127.0.0.1:8787`. The example tokens are deliberately
public demo strings, not credentials suitable for deployment. The lab is disabled
by default, the local config has no routes or account ID, workers.dev and preview
URLs are disabled, and `npm run deploy` exits with an error. These are guardrails,
not a security boundary against someone deliberately invoking Wrangler deploy.

Example request from PowerShell:

```powershell
$headers = @{
  Authorization = 'Bearer local-demo-token-not-for-deployment-0001'
  'Idempotency-Key' = 'inventory-test-001'
}
$body = @{
  workflow_id = 'lab.inventory.v1'
  input_data = @{ targets = @('demo-a', 'demo-b') }
} | ConvertTo-Json -Depth 4
$run = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8787/lab/executions' `
  -Headers $headers -ContentType 'application/json' -Body $body
Invoke-RestMethod -Uri ("http://127.0.0.1:8787" + $run.status_url) -Headers $headers
```

## Correctness and safety contracts

- Identity comes from the configured local principal, never an organization header
  or submitted `org_id`/`run_as`. Reads are requester-private, including within an
  organization. This is fixture authentication, **not** Entra or Cloudflare Access.
- A required Idempotency-Key is scoped to organization and actor. The same key
  and normalized request reuse one execution; changed input returns 409. Array
  order is semantically significant, so changing target order is a conflict.
- Admission is persisted before launch, but 202 is sent only after creation or
  an existing instance has been confirmed. An arbitrary create error is not
  treated as a duplicate. Failed/unknown confirmation returns 503 with Retry-After.
  Retry the original key; do not invent a new key for an ambiguous response.
- There is no atomic transaction spanning a Durable Object and Workflows. A
  lost create response is reconciled by status lookup; a crash before launch
  requires a client retry. Unconfirmed reservations expire after ten minutes
  and return a lookup path, rather than being silently recreated much later.
  Confirm the original status before submitting a fresh key. Once a launch
  receipt exists, replay never creates again, even if Workflow history has expired.
- Admission receipts are not automatically deleted. The lab's stored state grows
  with unique keys; a bounded retention/tombstone design is required for production.
  Missing/expired Workflow history currently surfaces as unavailable, not a
  fabricated terminal result. No long-term execution history is implemented.
- Step names, target order, and operation IDs are stable for v1. Each target has
  a ten-second step timeout and up to two retries. Serial fanout is capped at eight
  synthetic demo targets, and HTTP bodies at 4096 bytes.
- **No exactly-once external-side-effect guarantee.** A step can be delivered again
  after a lost checkpoint. The Executor contract carries a stable operation ID;
  a future real executor must enforce idempotency at the destination or explicitly
  refuse automatic retries for an unsafe operation. One unit test demonstrates
  duplicate delivery with destination-side deduplication.
- No cancellation endpoint, production rate limiter, user/role store, real
  integration, audit sink, secret store, browser WebSocket channel, scheduler, or
  infrastructure provisioning is implemented. Admission identity is not a global
  concurrency limit. Do not expose this fixture-authenticated lab to the Internet.

## Validation performed for this initial slice

- **Passed:** 48 Node contract/API/admission/orchestration tests.
- **Passed:** strict TypeScript checking of the runtime-independent core using 5.8.3.
- **Not run:** dependency installation/lockfile resolution, Wrangler-generated
  runtime typechecking, or the local Workers/DO/Workflows smoke test. The authoring
  shell could not reach GitHub/npm and had no Wrangler installed.
- **Not run or requested:** live Cloudflare deployment or the existing Python
  platform test suite. Existing platform source is unchanged.

The in-memory stores and step cache used in unit tests are explicitly test doubles,
not evidence of Cloudflare crash recovery. Complete the runtime gate in HANDOFF.md.

## Platform references checked September 9, 2026

- [Workflows Workers API](https://developers.cloudflare.com/workflows/build/workers-api/)
- [Triggering and observing Workflows](https://developers.cloudflare.com/workflows/build/trigger-workflows/)
- [Workflow local development](https://developers.cloudflare.com/workflows/build/local-development/)
- [Durable Object local environments](https://developers.cloudflare.com/durable-objects/reference/environments/)
- [Durable Object runtime testing](https://developers.cloudflare.com/durable-objects/examples/testing-with-durable-objects/)

See HANDOFF.md for the next implementation and resource gates.
