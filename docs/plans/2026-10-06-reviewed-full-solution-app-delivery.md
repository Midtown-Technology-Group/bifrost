# Reviewed full Solution App delivery

## Outcome

Deliver the Meraki Admin Governance and QuickSupport packages from protected
Workspace Main through the common package producer. Keep V2 Solution deployment
as compile-and-publish; inline App publication retains its separate adapter.

This plan tracks the adapter and its qualification. No production enrollment,
accounting closure or production runtime proof is claimed.

## Existing path to extend

- ProtectedGitReader already verifies exact Main/CI and every byte/mode in the
  complete authored Solution subtree.
- The ordinary Solution deploy job stages a source archive, compiles V2 Apps,
  updates entities under the Solution writer and switches each App pointer by CAS.
- Compiled App evidence now binds the retained source archive, owner, App pointer,
  build mode and complete actual output hashes. Accounting reads actual storage.
- The existing workflow-only Git source adapter requires deployment-v1 and
  excludes Apps. Do not relax its resource or ownership rules to accept V2 Apps.

## Adapter requirements

1. Add a distinct full Solution package kind to the existing producer/registry
   with exact Solution UUID, explicit organization scope and canonical subtree.
   Initial publication admits an operator-enrolled repo-v1 install with no
   immutable Solution pointer. Later publications require an exact active
   package deployment/pointer CAS, preserving the workflow-only adapter boundary.
   Reuse pinned repository/workflow OIDC and exact current Main CI; never grant
   the producer a platform administrator role. The ordinary manual deploy guard
   stays closed for immutable installs; the reviewed package path needs a
   supported successor operation rather than becoming a one-shot migration.
2. Build the archive server-side from the complete verified Git subtree. Accept
   no uploaded source or locally generated dist overlay. Use the existing
   Solution compiler and deploy job rather than another publisher or job system.
3. Before enqueue and again before effects, compare installed identity/scope,
   entity UUIDs, access/roles, resource policies and dependencies against the
   reviewed enrollment and parsed source. Refuse unpulled captures, additions or
   removals outside that enrollment, ownership changes, active SDK writers,
   secret/config/data replacements and force bypasses.
4. Persist an artifact-bound admission identity and staged archive before
   enqueue. A lost enqueue response must resolve to the same PlatformJob,
   including a terminal job; active-only deduplication is insufficient.
5. Revalidate Main/CI and installed controls in the worker before publication.
   Retain publication intent under the existing job lease before effects.
   Runner-loss or uncertain completion must enter readback on that original
   job, not execute the deploy again. Current solution.deploy defaults permit
   runner-loss retries and therefore need a reviewed intent-aware extension.
6. Inspection verifies exact entity registrations, actual retained source,
   complete dist bytes and independently reconstructed App pins. Settle the
   existing source ledger automatically. A source or accounting mismatch is an
   unresolved delivery, not a green producer result. Inspection has no effects.
7. The producer compares original job source identity and accounting result to
   the requested Main commit; an older successful job cannot certify new Main.

## Initial enrollment

- Meraki: Global Solution c05c8771-a656-4375-af84-f40fa0f16977,
  subtree solutions/meraki-admin-governance. Preserve its existing App UUID and
  Root access; do not infer resource grants from the source manifest.
- QuickSupport: Provider Solution 76cb30af-bad6-4d79-9f99-d59bced8fe1c,
  subtree solutions/quick-support. Fresh installed descriptor/source review must
  authorize its initial App definition and publication, eleven workflows, two
  admin-bypass evidence tables and the session-audits file location. The current
  empty install is not evidence that its complete authored package is App-only.

The ordinary repo-v1 workflow dispatch currently has no immutable runtime pin.
QuickSupport therefore also needs the supported full-package runtime projection;
App output pins alone do not satisfy its workflow acceptance criteria. The
existing CompiledDeploymentManifest already represents Apps, workflows and
tables. Keep publication of the corresponding App and Solution pointers coherent
and preserve the workflow-only Source adapter's restrictions.

## Source preparation command

`bifrost solution review-package RECIPE --source-commit SHA --repository-root DIR`
reads Git blobs at the exact commit, including all authored manifests, binary
assets and empty initializers. It never reads dirty source bytes, logs in,
imports authored Python or calls production. Its recipe requires an explicit
organization scope and hashes of the complete descriptor/entity manifest set.
Output binds source modes/hashes, deterministic full archive and declared
entity identities. CI, installed controls and runtime verification remain false.

The source preparation and server-side protected-Git capture seams are
implemented. The latter reuses the existing repository/CI reader and OIDC
verifier, binds an explicitly scoped enrolled target and complete reviewed
manifest set, and rechecks current Main/CI after reading all blobs. Its audience
cannot reuse workflow-only or inline-App tokens. This is source verification
only; the later publication adapter needs its own qualification. Common producer
enrollment remains unfinished.

Offline review of committed Workspace Main `6793385de20071acc85ab749fbe83c3b6dd22706`
retained all 147 Meraki package files and all 39 QuickSupport files. QuickSupport
includes eleven workflows, two tables and one file location; none were filtered
out as App build inputs. The source preparation has thirteen passing isolated
Docker checks. This does not certify that Main CI passed, installed controls
match, the Apps build successfully, or any runtime was published.

The recorded inventories are preparation evidence. Refresh live descriptors,
App identity/control/resource DTOs, captures, callers and outside references
before enrollment; no current live facts are certified by this file.

## Acceptance

Use one actual HTTP/database/object-storage happy path through scoped producer
admission and the existing worker, plus focused negative seams for scope,
controls, missing resources, stale Main/CI, capture deletion and runtime bytes.
Prove rapid successive merges, delayed admission, reverts, duplicate requests,
unknown enqueue/worker outcomes and pointer drift without effect replay.
Verify actual served V2 assets and canonical source accounting. Keep inline App
publication tests separate and preserve normal manual deployment behavior.

The prepared dev.24 first batch does not depend on this additional adapter.
Retiring loose Live still requires complete live consumer and accounting proof.

## Current validation boundary

The authorized CI Docker lane at committed `ce4fd20b3` passed the clean pre-PR
gate, all 24 protected-package source checks, all 13 offline preparation checks,
and 12,548 unit tests (three skipped). Real HTTP/database/object-storage App
checks passed, including a transaction that commits before its acknowledgement
is lost: the active pointer, runtime pin and every served output remain readable
without another activation. The earlier local asyncio socketpair setup errors
and fixture/mirror failures remain retained; none were waived or called passing.

The shared-deployer refactor at `dd37f45a6` retains both the original complete bundle and
its remapped projection alongside compiled App outputs. Its all-App CAS can run
inside the caller's transaction, enabling the package worker to move Solution
and App pointers together. A mixed App/workflow/table/file-location test checks
pre-publication retention and rollback of the App pointer. This new refactor
passed its own comprehensive CI run `37442686961`, including 12,549 unit tests
and the actual mixed-package preparation/transaction rollback test. These results
do not qualify subsequent changes.

Automatic enrollment remains unfinished. The package policy defaults to absent,
so the newly implemented admission path is disabled unless explicitly enrolled.

The shared Solution job now records a lease-fenced intent immediately before
its first database commit. A reclaimed attempt reads the original completion,
source archive, registrations, Python runtime/cache and compiled App pins;
it never invokes deployment or installation again. Uncertain commits retain
the staged input and install rather than deleting them. An identical request
from the original actor can resume that same job for readback; changed bytes,
options or scope cannot replace an unresolved intent. A missing original
completion or newer deployment fails closed for reconciliation. This does not
repair partially completed resource writes or qualify the future immutable
package worker. Its 19 focused recovery regressions passed in the exact
`5308f1176` unit lane (12,568 passed, three skipped); all four API shards also
passed. Browser/coverage lanes were still running at the recorded readback.

Complete package preparation now calls the existing deployer from the verified
Git archive, with an exact initial or successor deployment pointer and retained
control digest. It compares installed controls before and after preparation,
including role/MCP grants, endpoint/API-key exposure, entity scope/identity,
event bindings, table policies, file locations and connection declarations.
The comparison reads explicit columns rather than invoking capture methods that
can create connection declarations. Secret values and operational data are
excluded. Existing controls/resources cannot be silently replaced or removed;
new reviewed entities/declarations may be created. A failed comparison rolls the
complete metadata preparation back to its savepoint.

This entry returns only the original/remapped bundles and compiled App outputs;
it does not expose the mutable-source finalizer, commit, upload or activate.
The normal manual deploy guard remains closed for active immutable installations.
Initial/successor, stale-pointer/source/control and rollback preparation checks
passed current-head CI `37452670391` at `7e71a09821d0cdc4392f51335dbc7548631e1a40`.
This evidence does not qualify the subsequent publication changes.

The new adapter joins `solution.deploy`: source-scoped OIDC admission captures
protected Git and returns a stable shared job identity; status inspection never
enqueues or publishes. The worker rechecks Main/CI before effects, compiles the
complete package, stages revision-addressed source and App outputs, and joins
Solution/App CAS in one transaction. Its commit checks the current job lease in
that same transaction. Recovery reads only the original deployment. Actual
source bytes, full metadata/control snapshots, registrations and runtime pins
are checked before accounting. Existing declaration/scheduler hooks also inspect
completed packages for late declarations. Duplicate active scope installations
remain an accounting conflict until every intended target has qualified proof.

These publication changes have only static/import/OpenAPI checks so far. New
database/object-storage tests cover initial/successor mixed packages, App CAS
rollback, authored-byte tampering, incompatible signatures, a real commit whose
acknowledgement is lost, readback without publication replay, and late accounting.
Their exact-head Docker run is required. Common producer support/enrollment,
rapid-merge/revert qualification, actual served assets and production worker
execution remain acceptance gates. QuickSupport literal bound declarations still
need Source alignment; no workflow compiler restriction has been relaxed.
