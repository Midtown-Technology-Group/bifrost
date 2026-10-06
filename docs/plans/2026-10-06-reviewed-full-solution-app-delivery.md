# Reviewed full Solution App delivery

## Outcome

Deliver the Meraki Admin Governance and QuickSupport packages from protected
Workspace Main through the common package producer. Keep V2 Solution deployment
as compile-and-publish; inline App publication retains its separate adapter.

This plan describes the remaining adapter. It does not claim implementation,
production enrollment, source accounting closure or runtime proof.

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

This is the first implemented preparation seam. Scoped server admission,
worker publication/recovery and common producer enrollment remain unimplemented.

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
