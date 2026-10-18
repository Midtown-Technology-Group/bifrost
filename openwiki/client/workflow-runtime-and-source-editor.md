---
type: client-guide
title: Workflow Builder, Execution Views, and Source Editor
description: Maps browser editing, transient testing, workspace Git operations, and execution investigation while keeping their API and state boundaries distinct.
tags: [client, workflows, source-editor, executions, git]
sources:
  - id: openwiki-source-e91649c18f3633512e39bdfc
    resource: repo://client/src/App.tsx
  - id: openwiki-source-d67b56fe580184db21e3fad3
    resource: repo://client/src/components/editor/EditorOverlay.tsx
  - id: openwiki-source-c1e50bd7cfc5f8d3b8d6b050
    resource: repo://client/src/components/editor/runGitOperation.ts
  - id: openwiki-source-c22bb0d770a72532f663d281
    resource: repo://client/src/components/editor/RunPanel.test.tsx
  - id: openwiki-source-62c975fddd1a76251ed948df
    resource: repo://client/src/components/editor/RunPanel.tsx
  - id: openwiki-source-ba59d8915a52ab8fdbc04955
    resource: repo://client/src/components/editor/SearchResultItem.tsx
  - id: openwiki-source-47ad9bedf2a0ad28d961dc90
    resource: repo://client/src/components/editor/SourceControlPanel.test.tsx
  - id: openwiki-source-72d9582ee1ec79fd1208d5e6
    resource: repo://client/src/components/editor/SourceControlPanel.tsx
  - id: openwiki-source-24df3224701bb7ec8e3c8e9c
    resource: repo://client/src/components/events/EventsTable.tsx
  - id: openwiki-source-1781878e4a8d28e6886f9c5f
    resource: repo://client/src/components/execution/ExecutionInspector.tsx
  - id: openwiki-source-a73709a45e578f10ab93f07f
    resource: repo://client/src/components/file-tree/WorkspaceFileTree.tsx
  - id: openwiki-source-0ad0d2ae7ec3ecb0350dd0af
    resource: repo://client/src/components/files/FilesExplorer.tsx
  - id: openwiki-source-f325db99b3963aa505bc90a3
    resource: repo://client/src/components/forms/FieldsPanel.tsx
  - id: openwiki-source-6037d32b8d9c89eed86af9d5
    resource: repo://client/src/components/schedules/CronTester.tsx
  - id: openwiki-source-47b05cce746ea1f46b5e4669
    resource: repo://client/src/components/search/SearchBox.tsx
  - id: openwiki-source-77d7cefa714edb2eadb3f3f6
    resource: repo://client/src/components/workflows/WorkflowListSurface.tsx
  - id: openwiki-source-15114430c3058d14a0d0382e
    resource: repo://client/src/hooks/useAutoSave.test.tsx
  - id: openwiki-source-31d9e0e7f375d006bcfcda52
    resource: repo://client/src/hooks/useAutoSave.ts
  - id: openwiki-source-46c9bf782dfbefebefbd3029
    resource: repo://client/src/hooks/useSaveQueue.test.ts
  - id: openwiki-source-f0ae1b88d17983be842d261c
    resource: repo://client/src/hooks/useSaveQueue.ts
  - id: openwiki-source-295369edea011dd708c0deed
    resource: repo://client/src/pages/AppCodeEditorPage.tsx
  - id: openwiki-source-d14234b5dad7e8e2bc517d7e
    resource: repo://client/src/pages/events/EventSourceCard.tsx
  - id: openwiki-source-0d8ac2779ae1868b21fcdaf5
    resource: repo://client/src/pages/ExecutionHistory.test.tsx
  - id: openwiki-source-96611683de553097745b8bda
    resource: repo://client/src/pages/ExecutionHistory/components/ExecutionDrawer.tsx
  - id: openwiki-source-14d1a49ccb9ee805cd67c5ff
    resource: repo://client/src/pages/table-detail/TableDetailHeader.tsx
generated: { by: "bifrost-atlas", at: "2026-10-18T03:01:08.144Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T03:01:08.144Z
---

# Workflow Builder, Execution Views, and Source Editor

Bifrost has separate browser surfaces for editing platform workflow files, editing V2 application code, testing workflow files, publishing Git changes, and inspecting executions. These surfaces call shared API and job services; their controls do not establish server-side persistence, authorization, or runtime guarantees. See [frontend architecture](frontend-architecture.md), [execution and workflow runtime](../runtime/execution-and-workflows.md), [Git and ecosystem integration](../integrations/git-and-ecosystem.md), and [application SDKs](../workflows/apps-and-sdk.md).

## Workflow file editor and save path

The full-screen editor overlay is mounted with the application frame, but it renders only while the editor store is open and the signed-in user is a platform administrator. The overlay hosts `EditorLayout`; the editor's tabs, diagnostics, file tree, run panel, and source-control panel are client-side workbench surfaces ([overlay gate](../../client/src/components/editor/EditorOverlay.tsx#L10-L31), [application frame mount](../../client/src/App.tsx#L289-L302)). This UI gate is not a replacement for API authorization.

File changes flow from `useAutoSave` into `useSaveQueue`, which debounces edits for one second, writes with the tab's current ETag, and processes queued writes sequentially. A conflict response is kept separate from a generic save failure and handed to the editor conflict flow; if a newer edit arrives during an in-flight write, the queue carries the returned ETag forward before sending that edit ([save queue](../../client/src/hooks/useSaveQueue.ts#L35-L41), [ETag write and conflict handling](../../client/src/hooks/useSaveQueue.ts#L64-L100), [serial queue and newer edits](../../client/src/hooks/useSaveQueue.ts#L122-L187)). Tests cover late completions after a tab closes and preserving an edit queued during a save ([save queue tests](../../client/src/hooks/useSaveQueue.test.ts#L19-L75), [in-flight edit test](../../client/src/hooks/useSaveQueue.test.ts#L104-L149)).

Python workflow indexing is a second stage after the ordinary save: the autosave write detects whether indexing is needed, waits for pending saves, then requests an indexing write with `index=true`. The editor applies returned content and ETag, exposes diagnostics, and routes workflow-ID collisions into a decision dialog; this describes client orchestration rather than the server's indexing contract ([autosave and indexing sequence](../../client/src/hooks/useAutoSave.ts#L71-L101), [deferred indexing and conflict state](../../client/src/hooks/useAutoSave.ts#L135-L185), [indexing test across reordered tabs](../../client/src/hooks/useAutoSave.test.tsx#L273-L319)).

## Editor test runs

The workflow editor's `RunPanel` can test an open Python script by sending its base64 content with no registered workflow ID, or submit parameters for a discovered workflow. Both request forms set `transient: true`; synchronous results are shown immediately, while responses without immediate logs initialize an execution stream. This is the editor's test path, distinct from a normal durable workflow invocation ([script request and result handling](../../client/src/components/editor/RunPanel.tsx#L354-L432), [registered workflow request and result handling](../../client/src/components/editor/RunPanel.tsx#L475-L559)). The component tests exercise preserved input and explicit retry after a rejected request, metadata-read recovery, and service-file supervision UI ([RunPanel tests](../../client/src/components/editor/RunPanel.test.tsx#L168-L225), [metadata and service cases](../../client/src/components/editor/RunPanel.test.tsx#L237-L288)).

For asynchronous runs, `useExecutionStream` joins the `execution:{id}` WebSocket channel, appends logs and status updates to the execution-stream store, and invokes its completion callback so the page can refresh full execution data ([stream subscription and completion](../../client/src/hooks/useExecutionStream.ts#L49-L121)). WebSocket state shown by the editor is a live client projection; the durable execution record and its API remain the authority described in [execution and workflow runtime](../runtime/execution-and-workflows.md).

## Workspace Git operations

The editor's source-control panel is a workspace Git interface for status, fetch, commit, sync, diff, conflict resolution, abort, and discard. `runGitOp` creates a job ID, subscribes before queuing the operation, then observes shared `PlatformJob` updates and resolves on terminal success or action-required status; errors retain job result data. It uses the shared job/WebSocket observation path rather than a Git-specific polling loop ([job observer](../../client/src/components/editor/runGitOperation.ts#L1-L94), [source-control actions](../../client/src/components/editor/SourceControlPanel.tsx#L326-L424), [sync and conflict controls](../../client/src/components/editor/SourceControlPanel.tsx#L527-L687)). Focused tests verify a single commit with progress, no silent retry on rejection, explicit delete confirmation before syncing removals, and explicit publication recovery ([source-control tests](../../client/src/components/editor/SourceControlPanel.test.tsx#L76-L135)).

These Git actions operate on workspace source control and synchronization. They are distinct from saving a file through `fileService.writeFile`, and neither a successful UI job nor a fetched comparison alone proves that application code has been published to an external runtime.

## V2 application code editor

The V2 app code editor has its own `/apps/new` and `/apps/:applicationId/edit/*` platform-admin routes. It uses `AppCodeEditorPage` and `AppCodeEditorLayout`, rather than the workflow-file editor overlay ([app editor routes](../../client/src/App.tsx#L582-L597)). For Solution-managed applications the page passes `readOnly` to the editor and hides the Publish action; ordinary draft publishing is a separate action from the file editor's workspace Git sync ([managed app publish and read-only behavior](../../client/src/pages/AppCodeEditorPage.tsx#L397-L429)). The app code editor's SDK and server-side source lifecycle are covered in [application SDKs](../workflows/apps-and-sdk.md).

## Execution history and detail

The history list and `/history/:executionId` detail route require an organization user, while the editor overlay has its own platform-admin visibility check ([history and detail routes](../../client/src/App.tsx#L453-L461), [execution detail route](../../client/src/App.tsx#L834-L842), [editor overlay gate](../../client/src/components/editor/EditorOverlay.tsx#L16-L25)). `ExecutionHistory` maps search, organization, date, status, view, and log-level selections into URL state and supports pagination and row previews. Tests restore filter state from a deep link, check regular-user workflow-filter behavior, and verify canonical status-filter updates ([history URL filter tests](../../client/src/pages/ExecutionHistory.test.tsx#L277-L320), [filter restoration tests](../../client/src/pages/ExecutionHistory.test.tsx#L521-L550)). The execution detail page is a separate investigation surface with logs, metadata, and available actions; for durable status, cancellation, and result semantics, follow the runtime API documentation rather than inferring them from the page.

## Workflow, file, event, and execution component map

| Family | Responsibility and implementation anchor |
|---|---|
| Workflow catalogue | `workflows/` provides the reusable workflow list surface and action callbacks for open/edit/execute, endpoint and scope controls; the page supplies data and operations ([WorkflowListSurface](../../client/src/components/workflows/WorkflowListSurface.tsx#L46-L78)). |
| File editor tree | `file-tree/` owns the workspace tree and scope-aware navigation into open editor tabs ([WorkspaceFileTree](../../client/src/components/file-tree/WorkspaceFileTree.tsx#L76-L110)). |
| File browser | `files/` presents browsing, sharing, preview, access, and policy controls; a Solution install can pin the file scope, while ordinary browsing allows the scope selector ([FilesExplorer](../../client/src/components/files/FilesExplorer.tsx#L52-L105)). |
| Form designer | `forms/` owns field configuration and ordering, data-provider input config, upload controls, preview, and share/publish interactions. `FieldsPanel` delegates add/edit/delete and reorder behavior to the field manager ([FieldsPanel](../../client/src/components/forms/FieldsPanel.tsx#L117-L145)); see [forms, tables, and files](../workflows/forms-tables-and-files.md) for the resource contract boundary. |
| Schedule expression helper | `schedules/CronTester` submits the entered expression to the API validation endpoint and renders the returned validation result ([CronTester](../../client/src/components/schedules/CronTester.tsx#L29-L80)). This is an expression helper rather than the scheduler itself. |
| Event source timeline | `events/EventsTable` queries one source's events using status/date filters, adds provider event-type presentation, filters locally, and subscribes to event-stream status ([EventsTable](../../client/src/components/events/EventsTable.tsx#L118-L155)). |
| Execution inspection | `execution/ExecutionInspector` composes result, input, and log panels behind controlled or local tabs; `ExecutionHistory` adds list, cancel/cleanup, log, and drawer interactions ([ExecutionInspector](../../client/src/components/execution/ExecutionInspector.tsx#L5-L55), [history composition](../../client/src/pages/ExecutionHistory.tsx#L1-L37)). |
| Table detail | `table-detail/` owns the table workspace header and document collection/delete/pagination interactions; `TableDetailHeader` adapts its back navigation for tables and Solution detail contexts ([TableDetailHeader](../../client/src/pages/table-detail/TableDetailHeader.tsx#L12-L60)). |

## Change and test guidance

When changing this workbench, trace the route and component to its hook or service, then read the focused test for the behavior being changed. Keep file-save ETag/conflict behavior, editor test execution, workspace Git synchronization, V2 app publication, and execution inspection as separate paths. A passing UI test proves the tested client behavior only; verify corresponding API contracts and runtime behavior at their owners when those guarantees change.

## Focused browser detail components

| Surface | Responsibility and implementation anchor |
|---|---|
| Execution detail drawer | `ExecutionHistory/components/ExecutionDrawer.tsx` hosts the execution-detail sheet and exposes a copy-ID action; it is a navigation/detail affordance over the execution resource ([drawer contract and copy behavior](../../client/src/pages/ExecutionHistory/components/ExecutionDrawer.tsx#L14-L45), [sheet shell](../../client/src/pages/ExecutionHistory/components/ExecutionDrawer.tsx#L48-L85)). |
| Event-source detail | `pages/events/EventSourceCard.tsx` links into a source detail route and presents source type, organization/global scope, graph metadata, and admin action callbacks ([card inputs and route](../../client/src/pages/events/EventSourceCard.tsx#L49-L78), [source summary](../../client/src/pages/events/EventSourceCard.tsx#L88-L124)). The event detail table separately requests events with status/date filters, adds event-type presentation, searches locally, and attaches an event-stream subscription ([EventsTable query and stream](../../client/src/components/events/EventsTable.tsx#L118-L150)). |
| Editor search results | `editor/SearchResultItem.tsx` renders the server-provided file path, line number and matching line. It highlights literal query matches locally, but deliberately does not re-run server regexes in the browser ([result contract and regex boundary](../../client/src/components/editor/SearchResultItem.tsx#L1-L24), [rendered result](../../client/src/components/editor/SearchResultItem.tsx#L40-L58)). |

The shared `SearchBox` used by these page families owns only controlled text input, debounce, and clearing; feature hooks and services own the search request and result scope ([SearchBox contract](../../client/src/components/search/SearchBox.tsx#L6-L22), [debounce and clear behavior](../../client/src/components/search/SearchBox.tsx#L35-L49)).
