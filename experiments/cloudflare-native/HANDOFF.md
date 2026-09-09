# Codex handoff: local runtime gate, then separately approved Cloudflare resources

## Start here

Work on the experimental branch in `experiments/cloudflare-native`. Read its
AGENTS.md and README.md plus the root platform guidance. Keep all changes isolated;
do not merge into main or change the existing platform/deployment pipeline.

The first 48 tests and strict core typecheck were run successfully. Cloudflare
runtime checks were not run because the authoring shell lacked network access and
Wrangler. There is intentionally no fabricated lockfile or claimed runtime pass.

## Gate 1: no account changes

1. Install the pinned tooling with `npm install`. Verify package availability and
   security advisories, resolve any tooling incompatibility deliberately, and
   commit the generated package-lock.json. Do not silently float dependencies.
2. Run `npm test`, `npm run typecheck:core`, `npm run typecheck`, and
   `npm run test:runtime`. Fix any binding, serialization, type-generation, or
   local-runtime incompatibility before adding features. Do not replace genuine
   runtime checks with mocks merely to make them pass.
3. Extend runtime coverage for the crash/ambiguity windows covered by unit tests:
   simultaneous same-key submissions; conflicting payloads; lost successful
   creation responses; launch-receipt write failure; temporary quota failures;
   tenant and same-tenant actor isolation; and retained admission receipts after
   Workflow history is absent. Verify actual Durable Object transaction behavior.
4. Confirm Workflow versioning/replay behavior before editing deployed definitions.
   Validate that the executor's operation ID remains unchanged across retries and
   that non-idempotent adapters cannot silently opt into retry-enabled execution.
5. Select one existing, small read-only HTTP integration workflow as the next
   migration candidate. Read its real code and authorization contract; do not infer
   them from the synthetic example. Decide how existing Python automation remains
   executable. Do not call a real tenant while building the adapter.

This gate does not require a Cloudflare login, resource creation, DNS changes,
account upgrade, or deployment. Do not perform any of those as a prerequisite.

## Gate 2: only after Thomas explicitly passes the resource baton

Confirm the exact work Cloudflare account ID and its current Workers subscription,
existing operational workloads, shared quotas, and independently billed services.
A domain's website plan is not proof of the developer-platform subscription.
Do not assume a free allowance means every product has a hard spending cap.

Present the proposed lab resources and account-wide quota exposure before writing.
The initial remote candidate needs a Worker, SQLite-backed Durable Object namespace,
and Workflow binding only. Use an explicit isolated environment and freshly created
bindings; never point lab bindings at existing resources. Do not add R2, Containers,
Workers AI, external Postgres, or a paid-plan upgrade implicitly.

Replace fixture auth before any remote exposure: verify issuer/audience/signature
for the chosen identity provider and derive organization/actor authorization on the
server. A caller-supplied Access/identity header is not proof. Add appropriate abuse
limits and an execution-history/retention policy before opening access.

Choose one deployment source of truth rather than mixing dashboard state, Wrangler,
and an IaC tool without ownership rules. For this tiny lab, checked-in Wrangler
bindings may be sufficient; evaluate Terraform/OpenTofu or another tool only when
account-level resources need it. Never store credentials in the repository.

Record exact created resources, rollback/teardown steps, known shared-quota failure
modes, and observed request/CPU/storage/step usage. Do not modify production DNS,
raise account limits, add billing, or enable automatic deployment as a side effect.

## Architecture review effort

Use higher reasoning for tenant isolation, admission ambiguity, retry safety,
retention, workflow-version changes, and the Python/executor boundary. Normal/medium
reasoning is sufficient for most mechanical routing/configuration changes once
those contracts and tests are settled. A higher setting is not a substitute for
runtime validation, and maximum reasoning need not be the default for every edit.
