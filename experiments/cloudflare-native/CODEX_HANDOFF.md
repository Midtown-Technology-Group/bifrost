# Codex handoff: validate the code before provisioning

## Starting point

Source: `gobifrost/bifrost@0598020e32ea6367ecda69f548a53c00bb77bb6c`.
This first slice adds only `experiments/cloudflare-native/` and does not modify
upstream Python, React, CLI/MCP, migrations or deployment workflows.
Use an isolated worktree. Do not merge the experiment's inherited upstream CI
configuration into MTG main: keep all fork-specific deployment guards intact.

The authoring shell could not resolve GitHub/npm. Upstream rules/source were read
through the connected GitHub API. The new subtree was built in a clearly labeled
offline verification worktree, not a claimed full clone. A GitHub branch, when
published, must use the actual pinned upstream parent/tree, not that local baseline.
The source bundle and patch contain only the new subtree.

## First code-validation pass: no Cloudflare account required

1. Install Node 22.16+, then `npm install` in this directory. Review dependencies,
   run `npm audit`, and commit the real package-lock.json. Pin updates deliberately;
   do not assume a passing core test validates Wrangler's transitive dependencies.
2. Run `npm test` and `npm run check`. The latter must use real `wrangler types`
   output; fix any adapter/type/config mismatch before continuing. Do not write
   hand-made Cloudflare declaration files to make the compiler green.
3. Run `npm run setup:local`, then `npm run dev`, and `npm run smoke` in a second
   terminal. The smoke script verifies actual local DO RPC, Workflows steps,
   status, authorization, duplicate submission and changed-input conflict.
4. Add targeted real-runtime tests for concurrent same-ID createBatch submissions,
   structured RPC errors, and persistence across a Wrangler restart without
   deleting `.wrangler`. Verify that a replay returns the same ID and does not
   start a second native execution. Test provider-accepted/acknowledgement-lost
   and marker-write failure boundaries with controlled fault injection.
5. Reconfirm native retained-ID semantics for the chosen plan/runtime. The core
   protects confirmed replay with a permanent dispatch marker; the unconfirmed
   recovery window is 15 minutes. Manual native instance deletion, namespace
   reset, or reducing retention below that window invalidates the assumption.
   Missing native history must not be represented as execution success.

The initial core suite's port fakes do not prove distributed storage, actual
Workflows replay behavior, or free-tier CPU viability. Measure real invocations
before drawing conclusions about the platform or its plan limits.

## Architecture decision before the second slice

Choose one small, explicit next workload, not every Cloudflare primitive at once.
A useful candidate is a read-only, allowlisted vendor API operation with scoped
credentials, explicit rate handling and a step-level idempotency design. Keep
credential values out of durable Workflow input/results and public errors.

Decide separately how existing user-authored Python workflows will execute.
A TypeScript orchestration control plane does not transparently preserve arbitrary
Python imports, SDK calls, PowerShell/WinRM/SSH or replayable checkpoints. Prefer
an explicit executor contract over translating every workflow immediately.
No executor stub or unused queue architecture is included in this first slice.

Production authorization requires the entire upstream model: authenticated caller
capture, scope resolution, entity/Solution resolution, role/access checks and row
policies. The ported four-rule resolver and synthetic single-user token are not a
replacement for those gates. Do not treat provider-org bypass as administrator
identity or caller input as authority.

## Cloudflare provisioning boundary: user-controlled

Do not provision resources during the code-validation pass. When Thomas explicitly
hands over deployment, establish the intended Cloudflare account, current plan,
shared account-wide quota users, allowed resource names and routes, and a rollback
plan before making writes. No account ID is embedded in this repo.

Use isolated lab names and synthetic data. Retain LAB_ENABLED=false in committed
configuration. Set a distinct deployment identity/token through the appropriate
secret mechanism rather than reusing local .dev.vars or production Bifrost secrets.
Confirm intentional access/routing before enabling the lab remotely.

There are no R2, D1, Queues, KV, Hyperdrive or Containers resources to create for
this slice. Configuring disabled public routes is not a cost cap: DO/Workflow
storage and background work still need quota, retention and cleanup planning.
Do not upgrade a plan or create optional metered services automatically.

## Review/delivery gate

The current artifact is a spike, not a PR-ready rewrite. After local runtime checks,
select the appropriate upstream/fork delivery lane and run the required exact-HEAD
`./test.sh pre-pr` gate on the approved environment before opening/queueing a PR.
Report all results and unrun checks honestly; do not waive unexplained failures.
No PR, deployment, merge or production-readiness claim was part of this first pass.
