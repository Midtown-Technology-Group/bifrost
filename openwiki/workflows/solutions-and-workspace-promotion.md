---
type: workflow
title: Solutions, Workspace Promotion, and Git Sync
description: How portable Solution bundles are previewed, installed, reconciled, and synchronized, and how mutable Workspace authoring moves through reviewed changesets or immutable promotion.
tags: [solutions, workspace, promotion, git-sync, manifests]
sources:
  - id: openwiki-source-4caee390dc5fd98930592bdd
    resource: repo://api/src/routers/solutions.py
  - id: openwiki-source-b62002f0045af3615fa35f0e
    resource: repo://api/src/routers/workspace_promotions.py
  - id: openwiki-source-7d8425e8a1749c96e407cb70
    resource: repo://api/src/routers/workspace_repo_changesets.py
  - id: openwiki-source-bd7341a1b27f641827153147
    resource: repo://api/src/services/solutions/deploy.py
  - id: openwiki-source-87f9ee1add68ff2e73f6f751
    resource: repo://api/src/services/solutions/export.py
  - id: openwiki-source-b3ff21776fd9771e7f50ea47
    resource: repo://api/src/services/solutions/git_sync.py
  - id: openwiki-source-7cd8161b5f8c9f0d19c9a50a
    resource: repo://api/src/services/workspace_repo_changesets.py
  - id: openwiki-source-2fc99602da69d2f7b60e291e
    resource: repo://docs/architecture/rapid-workspace-promotion.md
  - id: openwiki-source-ae7bfdacbc3430a281161a63
    resource: repo://docs/architecture/workspace-source-coherence.md
generated: { by: "bifrost-atlas", at: "2026-10-18T02:51:00.673Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T02:51:00.673Z
---

# Solutions, Workspace Promotion, and Git Sync

Solutions and Workspace releases are related packaging and delivery paths, but they own different source namespaces. A Solution is a portable, manifest-backed package whose install has an isolated identity and reconcile boundary. Workspace changesets operate on mutable `_repo/` source and Git closure; rapid promotion turns protected source into an immutable release candidate and moves the Live pointer only after explicit validation and activation. The shared promotion design does not make the two source stores interchangeable. See [data and contracts](../architecture/data-and-contracts.md), [Git and ecosystem](../integrations/git-and-ecosystem.md), and [Apps and SDK](apps-and-sdk.md).

## Portable Solution package

A Solution workspace is represented by a `bifrost.solution.yaml` descriptor, Python source, and `.bifrost/*.yaml` entity manifests. Export writes the same general workspace shape accepted by preview/install, keeping the descriptor declarative: organization/global install scope is chosen by the installer rather than embedded in the package. Export can include encrypted runtime values only in password-protected full backups; a shareable export omits the encrypted secrets blob. This makes a package portable while keeping target identity and sensitive instance values outside ordinary source manifests.

The workspace-import preview endpoint stages the uploaded archive under a requester-bound token, parses it, computes a collision plan, and stores the package digest, planned manifest, ID map, and file hashes. Import submission requires one explicit decision for every conflict and checks config values against declared schema keys before it queues a durable PlatformJob. Repository preview is a one-time snapshot operation: it clones a requested ref, requires a descriptor under the selected subpath, rejects symlinks, resolves the concrete commit, then stages a preview. It does not create a connected Solution or continuing synchronization relationship.

At install time, source manifest IDs remain portable while persisted entity IDs are deterministically remapped with UUIDv5 using the install ID as namespace. Redeploying the same install preserves its generated IDs and references; another install gets independent rows. The reconciler upserts the bundle and deletes only entities owned by that exact `solution_id` that are absent from the new bundle. It cannot sweep `_repo/` rows or another installation. Python installs as source under `_solutions/{id}/`, and entities inherit the install's organization scope.

## Connected Solution synchronization

A Git-connected Solution has one writer: its configured repository ref, with the default branch used when no ref is set. The auto-pull path clones into a temporary checkout, uses the CLI's canonical collectors to construct the same bundle shape as a normal deploy, and applies that bundle to the Solution namespace; it does not write to Workspace `_repo/`. Manual deploy paths are refused for connected installs to preserve the one-writer rule. A shared per-install Redis write lock serializes sync with other Solution writers. If a newer trigger arrives while a sync owns the lock, the implementation leaves a pending-rerun marker so the current holder can pick up the newer source rather than silently letting an older checkout win.

The lock and descriptor checks protect against overlapping or incorrect full-replace writes, but do not establish that a remote repository is currently healthy or synchronized. Source here documents the mechanism only; it is not evidence of a particular Git host's current state.

## Mutable Workspace changesets and Git closure

The Workspace repo changeset API exposes state, begin, stage-file, diff, validate, activate, abort, Git-closure retry, and Git-convergence operations. The service normalizes scopes to non-empty workspace-relative prefixes and rejects paths outside a changeset's scope. Validation checks each staged Python file for syntax and extracts decorated workflow/tool/data-provider registrations; it also checks exact-byte `verify` mutations against their recorded hash. Activation requires a valid validation immediately beforehand and serializes writers with a PostgreSQL transaction advisory lock. A registration-only activation cannot request a source Git commit.

Changeset source mutation, activation, and Git closure have distinct outcomes. Runtime propagation proof can fail after durable activation, in which case the changeset remains activated and carries a `runtime_propagation` failure detail; callers must not repeat the source mutation to repair a cache mismatch. Git push/closure failures have explicit retryable states and recovery endpoints. The separate Git-convergence preview/apply path reconstructs an immutable plan against inspected repository history, then uses the configured platform commit writer; it is not a direct filesystem edit or proof of a remote push unless the returned result records it.

For exact source/cache repair, `workspace-repo-changesets/state` reports runtime mismatches separately from durable file hashes. A `verify` mutation can bind an exact-byte repair into validation and activation without manufacturing a source change or Git history commit. `_repo/` remains durable source of truth; Redis module cache and file index are derived runtime/search structures. See [workspace source coherence](../../docs/architecture/workspace-source-coherence.md).

## Immutable Workspace promotion

Rapid promotion is behind default-off feature flags in the inspected source. Its reviewed path begins with an exact protected Git source snapshot, not client-supplied production bytes. Preview creates an immutable candidate bound to source revision, effective snapshot and impact evidence; prepare is enqueued as a durable PlatformJob and returns the identities and challenge needed for activation. Activation checks the prepared challenge and base/active-release compare-and-swap state. The R0 path requires a successful canary of the exact reviewed artifact. Higher risk classes use an explicit matching risk acknowledgement and record that activation occurred without a canary or effect execution. The activation request does not accept mutable source bytes.

This release path is distinct from ordinary checked writes: impact-check writes are a development safety primitive and cannot register, activate, execute, test, commit, or push. Once a path is governed by an immutable Live release, legacy mutable write routes fail closed and direct operators to promotion. Solutions share the release state machine while remaining a separate target kind and namespace; they do not become Workspace `_repo/` source by virtue of that shared machinery.

## Ownership and change guidance

Use the Solution descriptor/manifests and Solution deploy/export services when changing portable package content or install reconciliation. Use Workspace changesets when the desired change is a scoped mutation to mutable Workspace source with validation, runtime coherence, and optional verified Git closure. Use rapid promotion when reviewed protected source must become an immutable Live release. Keep source authority, deployment target, and Git writer explicit: a repo-import preview is a snapshot, a connected Solution auto-pull is its sole install writer, and protected Workspace Git is the reviewed input to promotion. Focus verification on package round-trips and scope-isolated reconcile tests, changeset validation/activation failure states, and writer serialization/retry cases appropriate to the changed path; this page does not claim those suites were run.
