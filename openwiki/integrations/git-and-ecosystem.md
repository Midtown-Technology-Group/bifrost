---
type: integration-architecture
title: Git Integrations and Bifrost Ecosystem Contracts
description: The platform's workspace Git synchronization and separate Solution Git update paths, plus repository boundaries locally documented for the wider Bifrost ecosystem.
tags: [git, github, workspace, solutions, ecosystem]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T02:14:28.318Z
sources:
  - id: openwiki-source-8037e2358a2c4f9b2c722a11
    resource: repo://AGENTS.md
  - id: openwiki-source-c23c14b8bdb6b07be50d5237
    resource: repo://api/src/jobs/platform/git_operation.py
  - id: openwiki-source-c035c6436e1ecbb2f1f1a5b1
    resource: repo://api/src/jobs/platform/solution_git_sync.py
  - id: openwiki-source-9949d9a6d1599d55fd237847
    resource: repo://api/src/routers/github.py
  - id: openwiki-source-7535b9e44706b0de6f927659
    resource: repo://api/src/services/git_repo_manager.py
  - id: openwiki-source-187b3bc5aba5d184bf0b03d9
    resource: repo://api/src/services/github_sync.py
  - id: openwiki-source-ae7bfdacbc3430a281161a63
    resource: repo://docs/architecture/workspace-source-coherence.md
  - id: openwiki-source-82b568a56fde27324ed7f028
    resource: repo://docs/dev/agent-platform-rules.md
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
generated: { by: "bifrost-atlas", at: "2026-10-18T02:14:28.318Z" }
---

# Git Integrations and Bifrost Ecosystem Contracts

This repository contains two Git-backed product paths. The workspace Source Control integration manages the shared `_repo/` working tree; Solution Git sync updates one connected Solution install. Both run through durable platform work, but they have different source ownership and update semantics. See [Solutions and workspace promotion](../workflows/solutions-and-workspace-promotion.md) and [data and contracts](../architecture/data-and-contracts.md).

## Workspace Source Control

The `/api/github` router validates GitHub configuration and offers source-control operations such as connect, fetch, status, commit, sync, conflict resolution, diff, and discard. Mutating or long-running operations are enqueued as `workspace.git` PlatformJobs under a shared workspace mutation lock. The job records caller and organization attribution and publishes progress; status and errors are returned through the PlatformJob contract.

The server accepts GitHub HTTPS repository URLs (including owner/repository shorthand), constructs an internal authenticated remote from stored configuration, and passes it to `GitHubSyncService`. The working tree is serialized to and from object storage under `_repo/` by `GitRepoManager`, with a lock around materialization and persistence so concurrent operations do not overwrite one another. The durable source is object storage; Redis module cache and file index are derived runtime/search representations. A successful Git operation does not mean a deployment is healthy.

Connection is a preview-and-decision flow rather than blind clone. The job validates a preview token against the requesting user and organization, rejects an already-connected repository, and records configuration after the connection result succeeds or needs a user decision. Sync can return a requires-action result such as delete confirmation, while stale preview plans and invalid decisions become explicit job failures. See the source-control API and sync-plan tests before changing this workflow.

## Solution Git updates

`solution.git_sync` loads one existing Solution install and rejects installs that are not Git-connected or have no configured remote. It calls the Solution service's single-writer sync, reports progress, clears the available-version marker after success, and returns a typed completion result. A competing writer is deferred for retry; incomplete finalization or an ordinary sync failure is reported as retryable PlatformJob failure. This path is intentionally scoped to a single install and does not replace workspace `_repo/` synchronization.

## Repository boundaries

Local developer guidance identifies `bifrost` as the platform repository, `bifrost-workspace` as the automation-authoring repository, `bifrost-infra` as the Azure deployment owner for the MTG fork, and `bifrost-ops` as the source for MTG onboarding and operating procedures. The platform repo retains a guard so upstream `gobifrost/bifrost` DigitalOcean deployment CI does not run for the MTG fork. These are repository-local ownership contracts and links; this checkout does not verify current state or behavior in the other repositories.

The platform README describes workspace Git-based source management as a product capability. The authoritative release and environment lane for platform code remains in this repository's delivery guidance. Do not infer a workspace publish, infrastructure deployment, or external repository health from a local Git sync result.

## Focused verification

Relevant tests include `test_git_operation_platform_job.py`, `test_github_connect.py`, `test_github_sync_plan.py`, Git sync service tests, and Solution Git sync job tests. They cover plan validation, lock/durable job wiring, conflict/error paths, and the Solution update handler. No remote repository was contacted as part of this documentation generation.
