# Workspace source release accountability

## Problem

A protected Workspace merge and a production activation are separate events.
The platform cannot infer a merge from the existing `production-live` GitHub
writer. Without a durable declaration, reviewed source can sit on `main` while
production keeps running older bytes.

## Contract

### Native Solution authored Source

The reviewed Git delivery path keeps the complete `solutions/<slug>/` authored
tree separately from its executable closure. A fourteen-file authored package
may legitimately have a seven-file runtime closure; a green runtime cannot
credit the other seven files. Protected commit/tree/subtree identities, regular
Git modes, every file hash and the authored content ID are retained in a
content-addressed, create-only authored archive alongside the deployment.
Runtime `source.zip`, queued pins and historical manifests remain unchanged.

The native adapter checks descriptor fields, README text, all owned workflow
identities and controls, all owned table metadata/policies, and exact immutable
archive/runtime bytes. Only unused empty Python package initializers may remain
outside the positive executable dependency closure. Apps, assets and unsupported
components require their own delivery contract; this adapter cannot credit them
as workflow source.

README delivery uses the existing installation writer, a pointer/README CAS and
a SQL savepoint. It commits only when every other authored component reads back.
A descriptor, control, table, source or dependency mismatch rolls that metadata
write back. `authored_source_state=verified` is separate from runtime activation;
`attention_required` preserves partial runtime success without claiming complete
authored delivery. Neither state asserts a real worker execution.

Production target membership comes from all protected registry recipes,
including mappings that mix Root and package files. A missing/inactive Provider
installation cannot disappear from the target set. Recovery checks the complete
relevant family in exact scopes, including unexpected active siblings; unrelated
mutable Solutions are not a prerequisite. Storage proof is collected and cached
before the shared Solutions table fence, with a 64 MiB aggregate proof budget.
Budget exhaustion leaves affected targets visible and debt unresolved.
Acquisition waits at most one second, and the fenced database phase has a
five-second cooperative cancellation deadline; a timeout
rolls back without completing debt. The fence rechecks family membership,
durable receipts, closure identity and all metadata, binding the previously
verified bytes to every current source hash without remote storage calls.
Installations are read without row locks. Membership/status and pointer writes
wait only for this bounded database phase; workflow admissions
and historical execution pins continue without a Live fence. Reviewed shared
Root table verification retains its existing shared row locks. Native accounting
commits its checkpoint and releases the membership fence before the separate
Root accounting pass starts.

Delivery success/replay, late declaration/replay and the existing overdue sweep
use the same verifier and the existing child obligation. Native completion uses
`bifrost.native-solution-deploy-completion/v1`, with per-install actual deployment,
receipt and fresh readback evidence. It leaves `deploy_job_id` null rather than
fabricating a legacy job. The forward migration
`20261003_native_src_account` preserves the legacy completion branch and refuses
downgrade while native completions depend on it. Missing evidence leaves debt
open and rotates the bounded sweep; storage/transport failures propagate.

A verified descendant can automatically supersede an older native declaration.
The original obligation must belong to its exact GitHub OIDC declaration and
the same authored package. Delivery reads and retains that declaration's complete
historical Git inventory, not just the changed files. Recovery verifies the old
archive and every current intended installation before recording
`bifrost.native-solution-deploy-supersession/v1`. This records the old commit,
tree, subtree, content ID and file manifest alongside current delivery/readback
evidence; it does not claim that the old version was released. No workflow or
vendor effects are replayed.

Ancestry follows validated first-parent links through at most 1,000 protected
Git commit objects. Historical authored reads cover at most 100 unresolved
declarations for the package, retaining at most 32 MiB of bytes and manifests.
The historical tranche is ordered by immutable declaration creation time and
UUID, so all intended installations retain the same inputs until aggregate
completion removes them. Recovery check timestamps rotate the verifier's work,
but cannot rotate installation-local history selection before that convergence.
Selection first restricts declarations to the bounded verified first-parent
chain, so out-of-window or non-ancestor debt cannot fill its archive-read slots.
Unavailable or mismatching historical input leaves its obligation open while
current source still undergoes mandatory CI and full installed verification.
Missing ancestors, targets, archives or original producer evidence never settle
debt. A supported delivery receipt replay can refresh this proof after a delayed
declaration without activating the same source again.

### Solution delivery of Root source

Protected Git Solution delivery retains repository-to-runtime path mappings,
runtime hashes, recipe and installation-registry blob hashes, selected install
scopes, Git commit/tree, CI identity and a successful operation receipt. These
come from the protected Git reader; a producer cannot submit mappings or source
bytes as accounting evidence.

The accounting adapter runs after durable declaration (including exact replay),
after verified delivery (including a successful receipt replay), and before the
existing overdue sweep. It takes the global Live fence, all active installation
rows in UUID order, and then source rows. Delivery releases its one-install
writer before this aggregate reconciliation. Accounting does not activate a
deployment or retire Live.

Immediate and scheduled execution admission take a shared form of the same
global fence before selecting a runtime pin and retain it through durable
execution insertion. Admissions can run concurrently. The exclusive accounting
scan waits for those inserts, so a deployment selected just before a pointer
change cannot appear after completion as an unseen old consumer.

Each bounded recovery batch selects never/least-recently checked declarations
first, retaining `accounting_checked_at` even for unsupported paths. Repeated
sweeps advance past blockers without disposing them. Declaration and exact
replay additionally target their own Source identity rather than a front page.

Completion requires **every** declared path. The adapter checks active install
pointers, exact scopes, compiled closures, current workflow registrations and
immutable runtime bytes. It follows exact dependency pins and includes accepted
executions and unfinished workflow attempts, including superseded deployments.
A stale sibling, accepted pin, unknown mapping, mutable install, uncertain loose
import/file read or unpinned execution leaves the declaration unresolved. A
worker success is not completion evidence. Recipe changes remain production
controls; registry changes require each configured installation to have matching
scope, recipe and protected commit/tree readback.

Completion uses `bifrost.solution-owned-source-completion/v1`. A different newer
commit cannot release an old declaration merely because bytes match. The
protected Git reader can retain bounded first-parent ancestor attestations;
only a verified descendant with complete per-path replacement readback can
supersede old OIDC declarations, using
`bifrost.solution-owned-source-supersession/v1`. Missing ancestors, removed or
unmapped paths and ambiguous historical admin declarations remain unresolved
for explicit review. Reverts create new declarations and new deployment pins.

Reconciliation is idempotent and never reopens completed entries. A crash after
durable activation can be recovered by exact delivery replay, declaration
replay or the scheduler without manual accounting changes. Read failures retain
the debt; transport failures propagate rather than falsely reporting closure.

Apply `20261001_solution_src_account` before deploying the accounting adapter.
The constraint preserves Live completion and accepts only nonempty Solution
completion evidence for the exact declared commit/tree without a Live row.
Keep the constraint during application rollback; migration downgrade refuses
while completed Solution accounting rows rely on it.

### Inline App publication accounting

Published inline Apps keep their existing App identity, organization, controls,
source directory and publication job. Protected Git capture attests the complete
authored subtree, including `app.yaml` without applying its metadata. The exact
compiled manifest, compiler inputs, recorded migrations and every published
output belong to `bifrost.inline-app-runtime-pin/v1`; this is compiled runtime
byte proof, not a browser or customer business-success claim.

The existing Root accounting sweep reads immutable registry/recipe Git blobs
before acquiring aggregate locks. Under the final App row/control fence it
requires the latest original System publication job to be successful, verifies
all published bytes and the complete bookkeeping inventory, and reconstructs
the same saved runtime pin. A later manual, partial or uncertain publication
invalidates the older anchor. Unproven Apps leave their own source and registry
obligations open while unrelated Solution paths may complete.

App source/control paths use the existing `workspace_source_releases` ledger.
Mixed completion retains App publication-job/runtime-pin identities alongside
actual Solution deployment/receipt identities, using
`bifrost.package-owned-source-completion/v1`. No fake Solution, deployment,
receipt, new accounting table or new background job is created. A shared
registry requires every configured App and Solution in the same protected
commit/tree, scope and recipe target. Older OIDC debt can be superseded only by
verified first-parent ancestry and complete replacement readback. Missing
paths, stale consumers and uncertain loose readers remain unresolved.

Apply `20261005_package_src_account` before this adapter. It retains both Live
and Solution completion branches; downgrade refuses to discard App accounting
evidence. The scoped original-job inspection response adds fresh read-only
`accounting_readback` inside its existing result contract. It never changes the
durable original result, source ledger, published bytes or job state.

The Workspace batch publishes all reviewed Apps first, then checks source,
runtime pins and automatic accounting through that inspection route. A pending
shared registry is observed after every App has had its delivery opportunity.
Accounting waits and failed readback never enqueue, resume, build or publish
again; a missed deadline retains the original jobs and a failed batch result.

### Declaration producer

The trusted `bifrost-workspace` workflow triggered by every push to protected
`main` must call `POST /api/workspace-promotions/source-releases/github` for
that commit. Human administrators may use
`POST /api/workspace-promotions/source-releases` with ordinary platform-admin
authentication. The automatic producer uses a GitHub
Actions OIDC token in `Authorization: Bearer <token>`. The platform verifies
GitHub's issuer and signing key, then requires all of these identity claims:

- the configured repository name, immutable repository ID, and immutable owner ID;
- `ref=refs/heads/main`, `ref_type=branch`, and an event name of `push` or
  `workflow_run`;
- the configured producer `workflow_ref` on `refs/heads/main`;
- for `push`, the fixed audience `bifrost-workspace-source-release` and a token
  `sha` identical to `source_commit_sha` in the request;
- for `workflow_run`, the signed audience
  `bifrost-workspace-source-release:workflow_run:<run-id>:<head-sha>`, with its
  `head-sha` identical to `source_commit_sha` in the request.

`workflow_run` covers merges performed by GitHub's serialized queue, whose
`GITHUB_TOKEN` does not recursively emit a `push` workflow. GitHub defines the
workflow-run `GITHUB_SHA` as the latest default-branch commit, which may be newer
than the triggering CI run. The declaration workflow therefore binds
`github.event.workflow_run.id` and `github.event.workflow_run.head_sha` into the
OIDC audience. It must filter the exact CI workflow, completed success, and
`main`; check out that triggering head; prove it is an ancestor of its
OIDC `sha`; and derive the base from the triggering head's first parent. A fixed
audience on `workflow_run`, or a workflow-run audience on `push`, fails closed.

The request includes:

- the protected commit and tree SHA;
- each changed executable Workspace path and its SHA-256 target;
- a null target for a deletion;
- `pending` for production-affecting writes;
- `attention_required` with a reason for a deletion or unsupported change;
- `non_production` with a reason when no production path changed.

Changes under `solutions/<slug>/` use a separate child obligation. They are not
loose Workspace files and never close against the `production-live` branch. For
each changed Solution, the declaration carries the exact slug and repository
subpath, base/source commit and tree evidence, changed paths, the full protected
Git file manifest, and a canonical `source_content_id`. A solution-only commit
may remain `non_production` in the loose-file lane while its child is
`solution_deploy_required`. Malformed descriptors, deletions, cross-Solution
renames, and unsupported Git objects are declared `attention_required` instead
of disappearing from accountability.

The endpoint is idempotent for identical evidence and returns `409 Conflict`
if the same commit is redeclared with different evidence. The producer must
fail its GitHub Actions job when declaration fails. This removes operator
memory from record creation while retaining an exact audit trail.

Declarations now retain a canonical request digest independently of producer
provenance and mutable operational status. Replaying an identical declaration
after a deadline sweep, delivery or disposition returns the same record and
preserves its current diagnostic. Changing the original reason, path hashes,
tree, disposition or child obligations returns 409. Admin declarations retain
the digest too, without inventing a GitHub producer identity.

The nullable `declaration_digest` migration does not backfill old diagnostics as
original declarations. Historical producer digests remain valid replay
anchors. Legacy records with neither digest reject replay. Equality of current
and declared status cannot establish original identity: a disposition edit can
change the reason without changing status, or later return to the original
status. Original reasons cannot be reconstructed from current operational text,
and a declaration must never be accepted by guessing an empty or current reason.
Historical recovery needs retained original evidence, not a diagnostic backfill.
This schema change follows the
fixed-target migration lane before a compatible runtime image is deployed.

`GET /api/workspace-promotions/source-releases` returns
`tracking_state=not_configured` until the producer configuration begins or the
first declaration arrives. Operators and monitors must treat that state as
missing coverage, not an empty backlog. Existing installations may still
activate a release only while every producer setting remains absent. Once an
operator supplies any producer setting, partial configuration and a missing
first declaration both fail closed. This prevents a failed first producer run
from leaving activation open indefinitely.

## Completion

The platform owns completion. A successful immutable release projection marks
an eligible source record `released` only when every recorded target hash
matches both the immutable Live runtime and the verified, signed
`production-live` readback. A runtime-only activation cannot close the record.
One protected commit may be promoted through several exact-path artifacts. The
source record stays pending until one cumulative projection proves every
declared target on both surfaces. A non-production current head may still anchor
an exact reviewed catch-up or registration-only release for source merged
earlier; its own non-production disposition remains unchanged while the durable
Workspace release ledger records that activation.

Pending records default to a 30-minute deadline. The scheduler changes overdue
records to `attention_required`, does the same for a Live release whose signed
history has not converged within 15 minutes, writes a structured error log, and
notifies platform administrators.

An operator may explicitly mark a record `deferred` or `non_production`, but
must provide a reason. A later exact release may still replace `deferred` with
verified `released` evidence. Records containing deletions remain open until a
release path can prove runtime absence as well as signed-history absence.

An old source commit may instead be `superseded` after a later source record is
`released` or a reviewed Solution deployment is active. This manual decision
requires a production readback from the last day, a readback digest, and a
decision for every path in the old record. Each path records the current
protected-Git hash, current runtime owner, runtime
source hash and runtime reference, or records that the source was removed. The
platform checks the later released source record or active immutable Solution
pointer, checks source hashes against the corresponding release or Solution
closure and the current Live pointer for Workspace-owned paths, and requires
every old path to be reviewed. It stores an immutable
digest of the review. The operator must independently
compare those stated hashes and runtime references with live source,
registration, dependency, and signed-history readback. A changed Git hash alone
does not establish supersession. Use `deferred` while that proof is missing;
`non_production` is only for changes that truly had no production effect.

## Retired releases

Retiring the global Live release (see
[Rapid Workspace Promotion](rapid-workspace-promotion.md#retirement-and-demotion-of-the-immutable-loose-release))
leaves no Live release for the platform. Retirement does not silently close
outstanding accountability: a source-release record that is still `pending`
keeps its deadline, and the existing accountability sweep disposes it to
`attention_required` with reason
`reviewed Workspace source has not reached verified production`. Operators must
resolve or explicitly classify those records through the normal source-release
disposition endpoint. The Live-release history sweep also stops reporting the
retired row because it only selects `activation_state='live'` rows.

Changes under `solutions/<slug>/` are never part of a loose source-release
`paths` map. The declaration rejects any path equal to `solutions` or beginning
with `solutions/` and directs the caller to declare Solution source with
`solution_deploy_obligations`. Those subtrees therefore remain the child
obligation path before and after retirement.

## Solution deployment completion

Solution obligations preserve the explicit operator-approved deployment step;
the platform does not auto-deploy reviewed source. A deploy binds the exact raw
uploaded ZIP SHA-256 candidate and an order-independent candidate file-manifest
identity. The obligation closes only after all of the following succeed:

- the deploy database transaction commits;
- source-artifact and runtime-file storage finalization succeeds;
- the stored artifact reads back and matches the protected-Git file manifest;
- every deployed Python runtime path and hash reads back exactly;
- every Solution-owned entity ID reads back exactly after install-specific UUID
  remapping; and
- workflow registrations read back with the expected ID, path, function, and
  name.

A deploy failure, storage-finalization failure, artifact mismatch, runtime-file
mismatch, or registration mismatch cannot mark the obligation complete.
Mismatches become durable attention records. Pending Solution obligations use
the same 30-minute accountability sweep and administrator notification as loose
Workspace releases. Read-only status is available from
`GET /api/workspace-promotions/solution-deploy-obligations` and its ID-specific
endpoint.

## Repository producer

`MTG-Thomas/bifrost-workspace` owns the protected-main producer, exact Git-tree
classification, canonical manifest construction, and declaration-failure CI
signal. The platform API must be deployed before a producer version that emits
Solution child obligations is merged.

The OIDC endpoint is disabled until all five settings are configured:

- `BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_REPOSITORY`
- `BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_REPOSITORY_ID`
- `BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_REPOSITORY_OWNER_ID`
- `BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_WORKFLOW_REF`
- `BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_ORGANIZATION_ID`
