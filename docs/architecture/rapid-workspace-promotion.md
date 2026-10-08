# Retained Workspace release evidence

The loose Workspace promotion authoring surface has been removed from this
source revision. Deployment of that removal must follow verified production
Live retirement. The migration receipt, accepted-work inventory and signed
history remain required evidence; repository changes cannot prove retirement.

## Development and production delivery

Author workflow families as Solutions. Run and iterate in the isolated local
Solution environment, then deliver the reviewed protected Git source through
the configured Solution deployment path. Preserve registration identities,
scope, resource bindings, current-pointer comparisons and runtime verification.
A changed signature uses reviewed workflow revision; a body-only change uses
Source revision. Apps retain their separate Source, SDK build and immutable
published distribution path.

Generic Root file writes, Git sync and explicit workflow registration remain
superuser operations. The file indexer only enriches existing registrations;
uploading a new function does not register it. Retired registration identities
remain fenced against revival. Solution development resolves local workflows
first and defaults to `global_repo_access: false`; shared Root execution requires
an explicit opt-in. Keep migrated production targets sealed.

There is no `bifrost promote` command, local `--promotion-evidence` option or
public preview, draft, canary, prepare, activate, retry-history or retirement
writer in this revision. Those operations cannot be used to create a new global
Live release. Use the existing reviewed Solution delivery contracts.

## Evidence that remains readable

The platform-admin routes under `/api/workspace-promotions` retain:

- Artifact, release and Live status reads.
- Native retirement inventory, including inactive and normalized path variants.
- Protected Source declarations and authenticated GitHub declaration admission.
- Source and Solution obligation reads and evidenced Source dispositions.

The Source journal and Solution accounting scheduler remain active. Removing
the old writer does not settle historical obligations. Their dispositions must
still carry complete path, scope, current deployment and runtime evidence.
See [Global Solution source accountability](../dev/global-solution-source-accountability.md).

Historical `workspace-release-v1` pins continue to resolve their exact archived
release bytes. Retain immutable artifacts, release rows, registration evidence,
signed history and the runtime decoder. Never substitute mutable Root bytes or
a new Solution pointer for an accepted historical pin.

## Cleanup deployment gate

Before deploying this removal, independently verify:

1. The guarded Live retirement committed once and its exact pointer/readback,
   audit receipt and final signed history agree.
2. No production consumer still requires the loose release; current Solution
   source, registration and recorded runtime pins match the reviewed targets.
3. Accepted preview, prepare, draft-canary and history work is terminal, with
   unknown outcomes reconciled by readback rather than replay or cancellation.
4. Historical runtime pins and retained source evidence remain readable.

The internal preview/prepare handlers and draft-expiry scheduler remain through
durable work and artifact cleanup. They have no public producer in this source
revision. Remove their remaining implementations only after their respective
terminal-work and draft-storage inventories prove they are no longer needed.
The history projection handler remains until final signed history is verified;
the Source/Solution accounting scheduler is part of the replacement path.

The original preview/prepare/activate protocol is available in Git history for
interpreting retained receipts. It is no longer an operator runbook.
