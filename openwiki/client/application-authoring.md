---
type: frontend-workflows
title: Frontend Application and Workflow Authoring
description: How the browser authoring surfaces create and publish applications, edit workflow configuration, review form publication, and capture loose entities into Solutions.
tags: [frontend, authoring, applications, workflows, forms, solutions]
sources:
  - id: openwiki-source-8091ea15a9cfd8321a8e828f
    resource: repo://client/src/components/app-builder/AppInfoDialog.tsx
  - id: openwiki-source-f327efb7bec394d7781c3e65
    resource: repo://client/src/components/app-builder/AppUpdateIndicator.tsx
  - id: openwiki-source-43abd9b48170310f963efb8b
    resource: repo://client/src/components/app-code-editor/AppCodeEditor.tsx
  - id: openwiki-source-18451de1f378757634c8868e
    resource: repo://client/src/components/app-code-editor/DependencyPanel.tsx
  - id: openwiki-source-653f8501a6c212d81c75346a
    resource: repo://client/src/components/applications/ApplicationListSurface.tsx
  - id: openwiki-source-c2c33ceffd9aa4c7055fab7a
    resource: repo://client/src/components/catalog/ResourceCatalogCard.tsx
  - id: openwiki-source-e2bff8d7838c9f59430d3c32
    resource: repo://client/src/components/dependencies/DependencyGraphSurface.tsx
  - id: openwiki-source-44b5c1139dca77018d3dc5ea
    resource: repo://client/src/components/entity-management/EntityAssignmentPanel.tsx
  - id: openwiki-source-d4c88b6b9161bb3766879a82
    resource: repo://client/src/components/entity-management/EntityListToolbar.tsx
  - id: openwiki-source-da82ea9db4dae6bb31070d53
    resource: repo://client/src/components/forms/FormShareDialog.tsx
  - id: openwiki-source-aeb19b1f4140d7aecb3dd7db
    resource: repo://client/src/components/jsx-app/StandaloneV2App.tsx
  - id: openwiki-source-34fd49b2430739c4c51c32c1
    resource: repo://client/src/components/solutions/SolutionCaptureDialog.tsx
  - id: openwiki-source-cd1aa8a62c5555c8350f6cc9
    resource: repo://client/src/components/solutions/SolutionSetupChecklist.tsx
  - id: openwiki-source-e2c7d7fbe06d4ae0c1589d1a
    resource: repo://client/src/components/solutions/WorkspaceImportReview.tsx
  - id: openwiki-source-2be59f9cd3cabbefa616d765
    resource: repo://client/src/components/workflows/WorkflowEditDialog.tsx
  - id: openwiki-source-295369edea011dd708c0deed
    resource: repo://client/src/pages/AppCodeEditorPage.tsx
  - id: openwiki-source-9c3a216843b3bee9b66a7c34
    resource: repo://client/src/services/solutions.ts
generated: { by: "bifrost-atlas", at: "2026-10-18T03:01:08.144Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T03:01:08.144Z
---

# Frontend Application and Workflow Authoring

The authoring UI is a set of domain surfaces backed by typed API calls and server-side ownership rules. Applications, workflows, forms, and Solutions have distinct lifecycles; a browser control is only the user-facing entrypoint. The API remains authoritative for publication, resource ownership, and permission checks. See [frontend architecture](frontend-architecture.md), [applications and SDK](../workflows/apps-and-sdk.md), and [Solutions and promotion](../workflows/solutions-and-workspace-promotion.md).

## Applications

`AppCodeEditorPage` distinguishes a new application from an existing one. Creation collects name, slug, description, and organization and creates a standalone v2 app before navigating into its editor. Existing applications can have draft changes and publish a version with an optional message. The editor presents publish progress and preserves inline errors so the user can retry. Solution-managed applications are shown as read-only in the platform UI because deployment owns their writes; the API rejects direct edits and publication as well. This split is important when debugging an apparently disabled editor: check the app's ownership and publication state before changing the editor component.

## Workflows

Workflow settings are edited through `WorkflowEditDialog` and service hooks rather than a direct ad hoc request. The dialog groups metadata, organization/access controls, timeout and retry settings, economics, tool and data-provider configuration, and endpoint options such as allowed methods, public access, execution mode, and key management. It also loads and assigns roles. Solution-managed workflows are read-only in the browser and rejected by the API, preserving the Solution deployment path as the writer.

The main workflow editor and execution details live in separate page and runtime code. Treat changes to the workflow's saved metadata separately from changes to workflow source and runtime execution. See [execution and workflows](../runtime/execution-and-workflows.md).

## Forms and public sharing

Form editing covers fields, workflow selection, data-provider inputs, confirmation behavior, and preview. Public publishing is a separate controlled operation in the sharing dialog. The UI obtains a publication review and uses its fingerprint in the publication request along with allowed origins and spam-protection settings. It blocks conflicting publication actions while a request is active, refreshes the publication state on success, and leaves an error state on failure. Origin edits for an already published form are saved with the same reviewed fingerprint. This review handshake keeps changes tied to the version the user actually reviewed; server-side validation remains authoritative.

## Capturing entities into Solutions

The Solution capture flow first fetches eligible candidates, then maintains a selection across entity kinds (workflows, apps, forms, agents, tables, custom claims, and configs). Selection plus the optional imports toggle forms a stable preview request. Only after the preview is available can the user capture; success refreshes the Solution view, while errors remain visible in the dialog. This is an adoption flow: captured entities become Solution-managed, which changes their future write path. The capture UI therefore should not be mistaken for an ordinary edit or export.

## App builder component map

| Family | Responsibility and change location |
|---|---|
| App builder status and settings | `app-builder/` contains version/update state, app info, embed settings, and new-version notices; start with [AppUpdateIndicator](../../client/src/components/app-builder/AppUpdateIndicator.tsx#L28-L50). |
| Source editor | `app-code-editor/` owns the Monaco editor wrapper, theme lifecycle, code-change callback, diagnostics markers, and dependency package panel. See [AppCodeEditor](../../client/src/components/app-code-editor/AppCodeEditor.tsx#L51-L84) and [DependencyPanel](../../client/src/components/app-code-editor/DependencyPanel.tsx#L27-L55). |
| Application catalogue | `applications/` owns reusable list/card presentation and visible selection/SDK status, while page-level hooks provide entity data and actions ([ApplicationListSurface](../../client/src/components/applications/ApplicationListSurface.tsx#L231-L269)). |
| Dependency map | `dependencies/` renders the graph loading/error/data surface; graph acquisition and shaping are owned by its hook ([DependencyGraphSurface](../../client/src/components/dependencies/DependencyGraphSurface.tsx#L19-L55)). |
| Standalone V2 app host | `jsx-app/` mounts compiled app assets into the browser and tracks loading/recovery state. Treat it as a runtime boundary with its own protocol lifecycle, not as the editor ([StandaloneV2App](../../client/src/components/jsx-app/StandaloneV2App.tsx#L242-L285)). |

## Focused verification

Useful browser tests sit beside the individual components and pages: app publishing in `AppCodeEditorPage.test.tsx`, form publication/share behavior under `components/forms/`, and Solution capture behavior under `components/solutions/`. Workflow edit coverage is under `components/workflows/`. These tests exercise UI state and API calls; they do not establish server permission enforcement or runtime deployment. For a backend change, follow the corresponding API service and focused API tests as well.

## Supporting authoring and governance components

The app-builder family contains metadata and collaboration controls around the standalone app editor. `AppInfoDialog` is the shared create/edit form: it validates name and slug, and composes organization, access-level and role controls with application create/update/delete hooks ([implementation](../../client/src/components/app-builder/AppInfoDialog.tsx#L91-L108), [data hooks and controls](../../client/src/components/app-builder/AppInfoDialog.tsx#L64-L74)). `AppUpdateIndicator` presents brief attribution for a remote update and honors reduced-motion preferences; it is a notification surface, not a synchronization mechanism ([implementation](../../client/src/components/app-builder/AppUpdateIndicator.tsx#L28-L58)).

| Family | Responsibility and change location |
|---|---|
| Resource catalog card | `catalog/ResourceCatalogCard.tsx` is a reusable card shell that receives content and an `onOpen` callback; it supplies accessible link/button behavior and layout, while callers own resource data and authorization ([implementation](../../client/src/components/catalog/ResourceCatalogCard.tsx#L3-L31), [activation behavior](../../client/src/components/catalog/ResourceCatalogCard.tsx#L46-L80)). |
| Entity management | `entity-management/EntityListToolbar.tsx` owns list search, visible selection and sort controls. `EntityAssignmentPanel.tsx` builds organization/access/role changes for selected unmanaged entities and sends them through parent callbacks; keep server-side access enforcement in the API ([toolbar](../../client/src/components/entity-management/EntityListToolbar.tsx#L17-L37), [assignment filtering and callbacks](../../client/src/components/entity-management/EntityAssignmentPanel.tsx#L31-L49), [managed-entity exclusion](../../client/src/components/entity-management/EntityAssignmentPanel.tsx#L82-L89)). |
| Solution installation and import review | `SolutionSetupChecklist.tsx` renders declared configuration and delegates writes to parent callbacks; secret/password values use a non-revealing placeholder. `WorkspaceImportReview.tsx` groups a definition with its associated source files so the user makes one keep/replace decision for the related bundle. The parent page owns data fetching and mutations ([checklist boundary](../../client/src/components/solutions/SolutionSetupChecklist.tsx#L1-L8), [secret handling](../../client/src/components/solutions/SolutionSetupChecklist.tsx#L37-L55), [grouped import review](../../client/src/components/solutions/WorkspaceImportReview.tsx#L20-L32), [definition/source grouping](../../client/src/components/solutions/WorkspaceImportReview.tsx#L58-L96)). |

These components provide browser interaction and review. They do not transfer ownership or establish authorization themselves; consult the API and Solution deployment contracts for those guarantees.
