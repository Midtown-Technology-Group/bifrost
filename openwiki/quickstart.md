---
type: overview
title: Bifrost Platform Architecture Quickstart
description: A task-oriented map of Bifrost's API, client, runtime, persistence, integrations, and delivery boundaries.
tags: [quickstart, architecture, navigation, platform]
sources:
  - id: openwiki-source-de1257550254e1c4095fea5a
    resource: repo://api/src/main.py
  - id: openwiki-source-ab020f2fc3594eaf2a194005
    resource: repo://docs/dev/delivery-lanes.md
  - id: openwiki-source-0612ff143250c43ebbfbb3ba
    resource: repo://PRODUCT.md
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
generated: { by: "bifrost-atlas", at: "2026-10-18T02:34:56.337Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T02:51:00.673Z
---

# Bifrost Platform Architecture Quickstart

Bifrost is an automation platform for operators and the people using their apps and forms. The browser client lives in `client/`; the FastAPI service, worker processes, and shared Python code live in `api/`. PostgreSQL persists application data, Redis provides caching and coordination, and S3-compatible storage holds objects. Local Docker Compose defaults to RabbitMQ for work delivery, while the README describes PostgreSQL for MTG production; repository configuration is not proof of a running deployment. Start with [platform architecture](architecture/platform.md) for the service map and [development and verification](operations/development-and-verification.md) before changing or validating behavior.

## Choose a route by the change

- **Changing API contracts, ORM persistence, schemas, or tenant scope?** Start with [data model and contracts](architecture/data-and-contracts.md); for request routing and CLI/MCP parity use [CLI and MCP](architecture/cli-and-mcp.md). Follow the owning router, service/repository, and focused tests.
- **Changing authorization, credentials, or audit behavior?** Read [identity and authorization](architecture/identity-and-authorization.md) and [security and audit](operations/security-and-audit.md).
- **Changing workflow execution, agents, chat, knowledge, events, or jobs?** Use [execution and workflows](runtime/execution-and-workflows.md), [agents and chat](runtime/agents-and-chat.md), [knowledge and memory](runtime/knowledge-and-memory.md), [events and schedules](runtime/events-and-schedules.md), and [jobs and schedulers](runtime/jobs-and-schedulers.md) by owner. Provider registration/renewal is covered separately in [provider-managed webhooks](runtime/provider-webhooks.md).
- **Changing LLM configuration, decision inference, embeddings, or app compilation?** Start with [model inference and code apps](runtime/model-inference-and-code-apps.md), then follow the consuming agent, knowledge, or app flow. For deployable App source, its SDK contract, and build provenance, use [Applications and SDK](workflows/apps-and-sdk.md).
- **Changing app authoring, forms, tables, or files?** Begin at [application authoring](client/application-authoring.md), [workflow runtime and source editor](client/workflow-runtime-and-source-editor.md), or [forms, tables, and files](workflows/forms-tables-and-files.md), then trace the relevant client and API contract.
- **Changing platform-admin user flows?** Use [platform administration](client/platform-administration.md) and the [operations console](client/operations-console.md); for agent/chat settings also see [agents, chat, and AI configuration](client/agents-chat-and-ai-configuration.md).
- **Changing source ownership, a Solution package, or promotion?** Read [Git and ecosystem](integrations/git-and-ecosystem.md) for repository boundaries and [Solutions and workspace promotion](workflows/solutions-and-workspace-promotion.md) for package, changeset, and immutable-release workflows.
- **Changing devices, workers, OAuth, MCP, or external integrations?** Use [devices and workers](integrations/devices-and-workers.md) and [OAuth and external MCP](integrations/oauth-and-external-mcp.md); check the related identity and execution pages when a change crosses those boundaries.
- **Changing deployment, Kubernetes, CI, observability, or release behavior?** Start with [deployment and infrastructure](operations/deployment-and-infrastructure.md), [Kubernetes and local cluster](operations/kubernetes-and-local-cluster.md), [CI and release evidence](operations/ci-and-release-evidence.md), and [release process](operations/release-process.md). For live questions, source alone cannot establish current health or deployment state.

## Find the actual entry point

The API entry point assembles middleware and imports domain routers, but a route is only the boundary. Follow its service/repository for durable behavior, its worker or scheduler for asynchronous work, and its contract/ORM model for data shape. The client has its own routing and state boundary; generated API types connect it to the server contract. The [operations console](client/operations-console.md) and [frontend architecture](client/frontend-architecture.md) describe the browser-side structure.

For source changes, use the repository's delivery lane that matches the risk: documentation and isolated internal changes can use a narrow check; API and product behavior need scoped tests and generated type updates when contracts change; auth, execution, tenant isolation, queues, migrations, deployment, secrets, audit, and infrastructure require the platform/live-operations lane. The lane guide defines when live or VM-backed evidence is needed, and pure documentation does not require a live stack.

## Evidence boundaries

This knowledge map describes the checked-out source baseline. It does not establish that a service is running, a feature flag is enabled in a deployment, a workflow succeeded, or another ecosystem repository currently matches the local contract. Treat dated design documents as decisions or history, then confirm current behavior in maintained code. For operational claims, use the owning environment's read-only status or runtime evidence and state its host, worktree, and scope.
