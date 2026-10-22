---
type: frontend-operations
title: Frontend Operations and Administration
description: How operators use browser surfaces for execution history, agent runs, long-running platform jobs, schedules, audit events, and diagnostics.
tags: [frontend, operations, diagnostics, audit, monitoring]
sources:
  - id: openwiki-source-baea32de32884b2a3f0c7b1d
    resource: repo://client/src/components/dashboard/ExecutionsOverTimeCard.tsx
  - id: openwiki-source-82c661dd5e63b05a448fa28d
    resource: repo://client/src/components/reports/UsageCharts.tsx
  - id: openwiki-source-c00ecb3130f24cd7b40c2deb
    resource: repo://client/src/pages/audit/AuditLogPage.tsx
  - id: openwiki-source-9946d589003a517f07cd0773
    resource: repo://client/src/pages/diagnostics/DiagnosticsPage.tsx
  - id: openwiki-source-c59f800b021a2b414c4e7835
    resource: repo://client/src/pages/Events.tsx
  - id: openwiki-source-385ec88bed83fc8c148be801
    resource: repo://client/src/pages/ExecutionHistory.tsx
  - id: openwiki-source-28a72ebc7498642efa60344d
    resource: repo://client/src/pages/Home.tsx
  - id: openwiki-source-6c66b4505c1450d9580b292b
    resource: repo://client/src/services/agentRuns.ts
  - id: openwiki-source-e2ce84549e8c73682487b82b
    resource: repo://client/src/services/platformJobs.ts
  - id: openwiki-source-f2a693d95c4c4edf2ed9afa9
    resource: repo://client/src/services/schedulerDiagnostics.ts
generated: { by: "bifrost-atlas", at: "2026-10-18T03:01:08.144Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T03:01:08.144Z
---

# Frontend Operations and Administration

The operations UI is a read/control layer over durable platform state. Pages use typed API services and notification channels to inspect work; cancel or repair actions remain server-authorized operations. The page surface is organized by the kind of object an operator needs to investigate: workflow executions, agent runs, platform jobs, scheduler tasks, integrations/devices, audit events, and system diagnostics. See [frontend architecture](frontend-architecture.md), [execution and workflows](../runtime/execution-and-workflows.md), and [observability](../operations/observability-and-operations.md).

## Following work

Execution history and details provide the workflow-oriented view of accepted and completed runs, while agent-run pages expose agent-specific state and verdict actions. Agent-run services support list/detail reads, pagination, live WebSocket invalidation, verdicts, reruns, dry runs, and summary backfill. These are distinct resource types even when they share the broader execution infrastructure.

Platform job views are for durable administrative operations such as deployment, export, or reindex work. The service reads a job snapshot or a filtered job list and can issue cancellation. Progress is primarily notification-driven; one status read closes the gap where a completion notification was missed, rather than polling continuously. A job's terminal status can include `requires_action`, which signals that the operation needs a further decision rather than having silently completed.

## Scheduler and system diagnostics

The diagnostics page is guarded in the UI for platform administrators and groups worker, scheduler, and app-service views. Scheduler services expose a system diagnostics snapshot plus per-task run history. These views help separate scheduler leadership/capacity symptoms from a particular task's history; they are observations from API responses and do not themselves prove that work will succeed.

## Audit trail

The audit page filters by action, outcome, search text, date range, and continuation token. Changing a filter resets its pagination cursor so the next page corresponds to the new query. Audit rows include actor and structured context; detail rendering selects safe display fields such as file path or table identity instead of treating arbitrary details as trusted markup.

## Summary, history, and monitoring UI families

| Surface | Responsibility and implementation anchor |
|---|---|
| Home resource catalogue | `Home` queries the catalog, stores search/type/org/view/collection state in URL parameters, and edits collections/preferences. Collections organize discoverability; the page explicitly states that resource grants still govern launch ([Home](../../client/src/pages/Home.tsx#L35-L80)). |
| Dashboard and charts | Dashboard stat cards and execution trend charts format API-derived aggregate data for overview navigation; failed totals link to filtered history ([execution trend card](../../client/src/components/dashboard/ExecutionsOverTimeCard.tsx#L30-L90)). Usage reports separately render cost trends from usage-service data ([usage charts](../../client/src/components/reports/UsageCharts.tsx#L30-L60)); chart error presentation is shared in `charts/`. |
| Execution history | The history components separate record/log rendering from the detail drawer; `ExecutionDrawer` hosts the focused execution detail view and stable-ID copy interaction ([execution drawer](../../client/src/pages/ExecutionHistory/components/ExecutionDrawer.tsx#L14-L62)). |
| Event-source administration | The Events route filters and searches event sources, selects organization/global scope for platform admins, and exposes create/edit/delete state through event components ([Events](../../client/src/pages/Events.tsx#L63-L105)). Provider delivery and API security remain backend-owned. |
| Diagnostics | `DiagnosticsPage` is explicitly guarded in the UI for platform admins and composes worker, scheduler, and app-service tabs ([DiagnosticsPage](../../client/src/pages/diagnostics/DiagnosticsPage.tsx#L12-L69)). Its readings are UI projections of API responses, not live health evidence by themselves. |

## Operator workflow and limits

Start from the resource's stable identifier, inspect its status/history and relevant logs, then use an explicit control action only when that action is supported by the API. For a stuck schedule or worker, correlate the job/execution record with scheduler and worker diagnostics rather than inferring from a single UI badge. UI route guards improve navigation but API authorization remains the enforcement boundary. The source and client tests do not establish live service health; for current runtime facts use the authorized environment and procedures described in [development and verification](../operations/development-and-verification.md).

The three primary route implementations clarify these browser responsibilities. `Home` reads and writes catalog preferences and collections through the typed API client, keeps search, organization, kind, sort, view and pagination state in the URL, and launches resources using existing grants ([query and mutations](../../client/src/pages/Home.tsx#L35-L63), [URL-backed view state](../../client/src/pages/Home.tsx#L64-L80), [resource launch boundary](../../client/src/pages/Home.tsx#L155-L170)). `ExecutionHistory` combines workflow records, logs and agent-run panels; its status tabs and organization/workflow filters are URL-backed, with separate hooks for execution reads and stream updates ([route composition](../../client/src/pages/ExecutionHistory.tsx#L26-L73), [filter definitions and URL state](../../client/src/pages/ExecutionHistory.tsx#L79-L89), [history hook and filter state](../../client/src/pages/ExecutionHistory.tsx#L120-L178)). `Events` owns the event-source browser controls—scope, text/status filters, activation, edit and delete—and delegates API work to event services/hooks ([route and state](../../client/src/pages/Events.tsx#L63-L86), [filtering](../../client/src/pages/Events.tsx#L98-L113), [activation mutation](../../client/src/pages/Events.tsx#L116-L134), [delete mutation](../../client/src/pages/Events.tsx#L147-L176)). These source paths document UI behavior only; they do not demonstrate runtime health or replace backend access checks.
