---
type: frontend-architecture
title: Frontend Architecture and API Client
description: The React SPA entrypoint, route/provider composition, OpenAPI-typed network layer, authentication and CSRF handling, and development proxy boundary.
tags: [frontend, react, api-client, routing, authentication]
sources:
  - id: openwiki-source-e91649c18f3633512e39bdfc
    resource: repo://client/src/App.tsx
  - id: openwiki-source-ccc3df2144f0969024df2ec0
    resource: repo://client/src/components/access/AccessLevelSelect.tsx
  - id: openwiki-source-fcef92f24d9e45b4b3794793
    resource: repo://client/src/components/auth/AuthSetupSteps.tsx
  - id: openwiki-source-73a4a12f033ebd49e8017f44
    resource: repo://client/src/components/auth/AuthTransition.tsx
  - id: openwiki-source-4ff81794a70514d59804720c
    resource: repo://client/src/components/branding/Logo.tsx
  - id: openwiki-source-b7a76a96b5b3cba38c439d14
    resource: repo://client/src/components/charts/MetricsCardError.tsx
  - id: openwiki-source-a963f6b4d866c1364b78133f
    resource: repo://client/src/components/common/MarkdownContent.tsx
  - id: openwiki-source-36514d6e60ae752d064476ef
    resource: repo://client/src/components/icons/GithubIcon.tsx
  - id: openwiki-source-1ce004b17b417fa0788b1249
    resource: repo://client/src/components/layout/AppLayout.tsx
  - id: openwiki-source-2477d4dbbfb92d5f26eb7bf3
    resource: repo://client/src/components/pagination/ListPagination.tsx
  - id: openwiki-source-a7668ebfd577476ec92bfcda
    resource: repo://client/src/components/quick-access/QuickAccess.tsx
  - id: openwiki-source-47b05cce746ea1f46b5e4669
    resource: repo://client/src/components/search/SearchBox.tsx
  - id: openwiki-source-846f7391ca97625494c459fb
    resource: repo://client/src/components/services/ServiceListSurface.tsx
  - id: openwiki-source-6f2b6d626296d9361fc674d5
    resource: repo://client/src/components/services/useServiceStream.ts
  - id: openwiki-source-103d02817127e68c7f39182b
    resource: repo://client/src/components/shared/JsonYamlEditor.tsx
  - id: openwiki-source-8fa2b5fbdded6f4e7b6a1588
    resource: repo://client/src/components/shared/SettingsToggleRow.tsx
  - id: openwiki-source-4eecfd82574ad5cdda4a4665
    resource: repo://client/src/components/ui/button.tsx
  - id: openwiki-source-98df76a0fc0d93e5ee83eb92
    resource: repo://client/src/components/window-management/WindowDock.tsx
  - id: openwiki-source-16106cfefc23b870399fd4de
    resource: repo://client/src/components/window-management/WindowOverlay.tsx
  - id: openwiki-source-fcc9c72d754d065f9bdaa2cb
    resource: repo://client/src/contexts/OrgScopeContext.tsx
  - id: openwiki-source-d95b43bf21f2fca036e63284
    resource: repo://client/src/lib/api-client.ts
  - id: openwiki-source-9eb01333fabdb30b30dcc3b4
    resource: repo://client/src/lib/app-code-platform/useWorkflowMutation.ts
  - id: openwiki-source-cf0907c2df9cf9452d072ce1
    resource: repo://client/src/lib/monaco-setup.test.ts
  - id: openwiki-source-694fc04092d63b42e232ce72
    resource: repo://client/src/lib/monaco-setup.ts
  - id: openwiki-source-829a95bd4605e82a4193c39d
    resource: repo://client/src/lib/monaco/conflictDecorator.ts
  - id: openwiki-source-2076ae67f9a06e98fc3bab58
    resource: repo://client/src/lib/queryClient.ts
  - id: openwiki-source-d833fdf3f8a51669ca439c84
    resource: repo://client/src/main.tsx
  - id: openwiki-source-1a6a87b4b75e4ba9fd7b5b8d
    resource: repo://client/src/pages/Home/components/CatalogFilters.tsx
  - id: openwiki-source-96de3c87682cee2ca0b5e2ad
    resource: repo://client/src/pages/Home/components/CollectionEditor.tsx
  - id: openwiki-source-10b637684b49f45d3cc299f4
    resource: repo://client/src/pages/Home/components/CollectionIconPicker.tsx
  - id: openwiki-source-804ef6246772eae3ed1d4181
    resource: repo://client/src/pages/Home/components/CollectionNavigation.tsx
  - id: openwiki-source-5955a4c217a8391e4096a4a3
    resource: repo://client/src/pages/Home/components/HomeBrowse.tsx
  - id: openwiki-source-f37c5dbe8a75e855e6798720
    resource: repo://client/src/pages/Home/components/ResourceCard.tsx
  - id: openwiki-source-c8028bc2008c14303a41f6c7
    resource: repo://client/src/pages/Home/components/ResourceList.tsx
  - id: openwiki-source-810ab177642148bf08dfb03b
    resource: repo://client/src/pages/roles/RoleActionsMenu.tsx
  - id: openwiki-source-1031ee0234e15576a14e1fd6
    resource: repo://client/vite.config.ts
generated: { by: "bifrost-atlas", at: "2026-10-19T14:54:21.479Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-19T14:54:21.479Z
---

# Frontend Architecture and API Client

The browser client is a React SPA built with Vite. `main.tsx` configures platform-wide browser services and mounts `App`; `App` owns route composition, providers, protected page access, and the top-level application error/update gates. Pages and feature components use shared service modules and an OpenAPI-derived fetch layer rather than each defining their own transport conventions. See [application authoring](application-authoring.md) and [operations console](operations-console.md).

## Startup and route tree

The entrypoint installs React strict mode, theme and TanStack Query providers, organization-scope query invalidation, Monaco setup, the JSX module shim, and Sentry before rendering. Monaco is bundled from the locked `monaco-editor` dependency with language workers provided by the application bundle; `MonacoEnvironment.getWorker` selects JSON, CSS, HTML, TypeScript/JavaScript, or the general editor worker rather than loading editor code from a CDN (`client/src/lib/monaco-setup.ts`). The router wraps the page tree in authentication, organization scope, keyboard, layout, and error handling providers. Most pages load lazily so the initial bundle does not have to include every platform surface; protected routes distinguish platform-admin access from organization-user access.

## Typed network layer

`client/src/lib/v1.d.ts` supplies the OpenAPI `paths` type used by `openapi-fetch`. `api-client.ts` builds an imperative `apiClient` and a React Query `$api` wrapper from the same typed client. This means route paths and payload types are constrained by the generated API schema, while domain services can provide higher-level operations. `authFetch` remains available for endpoints that are not represented in the OpenAPI schema.

The client middleware ensures a valid token before non-auth requests, attaches user context and CSRF headers for mutating methods, prefers the HttpOnly cookie, and uses a bearer token as a fallback for embed or cookie-less paths. A `401` does not automatically mean the whole session expired: the client checks `/auth/me` before refreshing or redirecting. `429` responses become a rate-limit error that carries the server's `Retry-After` delay.

Transient retries are deliberately narrow. The client retries only `502`, `503`, and `504` for replay-safe methods; `POST` and `PATCH` are not replayed because the server may already have applied their side effects. Workflow endpoint paths are excluded even for otherwise idempotent verbs because they execute work. React Query also disables automatic query and mutation retries by default, leaving any opt-in retry behavior explicit at the call site.

## Same-origin proxy and asset boundaries

During development, Vite proxies API, auth, OpenAPI, MCP, websocket, embed, and object-storage paths to the relevant backend service. `API_URL`, `WS_URL`, and the S3 endpoint environment setting select Docker or local targets. It rewrites `/api/auth` to backend `/auth`; the `/auth/callback` path stays in the client router. The proxy also obtains a content security policy for public/HMAC embedded forms before serving the frame. The browser typically sees one origin, which simplifies cookie and CSRF handling.

## Shared component and state families

| Family | Responsibility and implementation anchor |
|---|---|
| Access controls | `access/` contains reusable access-level inputs. `AccessLevelSelect` emits a selected access value; it is UI configuration and does not enforce permissions ([implementation](../../client/src/components/access/AccessLevelSelect.tsx#L6-L27), [selection behavior](../../client/src/components/access/AccessLevelSelect.tsx#L72-L100)). |
| Authentication, organization, theme, and keyboard contexts | `contexts/` owns app-wide client state. `OrgScopeProvider` persists scope and loads branding/terminology when scope changes ([OrgScopeContext](../../client/src/contexts/OrgScopeContext.tsx#L19-L35), [scope persistence and refresh](../../client/src/contexts/OrgScopeContext.tsx#L44-L95)); auth and server authorization remain separate. |
| Shared content and layout | `common/` contains cross-domain content/action helpers such as Markdown rendering; `layout/` owns route shells, headers, navigation, loading/error states, and notification presentation. Start with [MarkdownContent](../../client/src/components/common/MarkdownContent.tsx#L6-L16) and [AppLayout](../../client/src/components/layout/AppLayout.tsx#L1-L31). |
| Primitive UI | `ui/` supplies shared controls and Radix-based wrappers, such as the variant/size-aware `Button`. Feature pages compose these primitives; they are not platform-domain components ([button variants](../../client/src/components/ui/button.tsx#L7-L39), [render wrapper](../../client/src/components/ui/button.tsx#L42-L60)). |
| App-code platform hooks | `lib/app-code-platform/` provides the browser SDK compatibility layer for embedded apps, including role gating, navigation/location, user/scope and workflow query/mutation hooks. The workflow mutation hook owns its execution subscription and cleanup lifecycle ([RequireRole](../../client/src/lib/app-code-platform/RequireRole.tsx#L1-L25), [useWorkflowMutation contract](../../client/src/lib/app-code-platform/useWorkflowMutation.ts#L1-L20), [subscription cleanup](../../client/src/lib/app-code-platform/useWorkflowMutation.ts#L77-L119)). |
| Monaco conflict tooling | `lib/monaco/` decorates merge conflict regions and exposes editor conflict actions. It is editor support for local source review, not a Git merge engine ([decorator and conflict regions](../../client/src/lib/monaco/conflictDecorator.ts#L1-L30)). |

For product-domain components, follow the family guides linked above: [agent/chat/AI](agents-chat-and-ai-configuration.md), [application authoring](application-authoring.md), [workflow and source editor](workflow-runtime-and-source-editor.md), [platform administration](platform-administration.md), and [operations console](operations-console.md).

## Change and test guidance

When adding a page, register its route with the right `ProtectedRoute` level, use the shared typed client/service seam, and invalidate affected query keys after mutations. When an API contract changes, regenerate the OpenAPI TypeScript declarations against the matching API. Focused tests include `api-client.test.ts` for auth/retry transport behavior, route tests for protected routing, and service/page tests beside their feature. These checks describe client behavior; they do not establish that an external proxy or deployed API is configured correctly.

| Additional shared family | Responsibility and change location |
|---|---|
| Auth-flow presentation | `auth/AuthSetupSteps.tsx` presents passkey setup or password registration and delegates registration to callbacks; `AuthTransition.tsx` covers the post-credential session-resolution interval. Credential acceptance and session creation remain server/auth flow responsibilities ([setup callbacks and choice](../../client/src/components/auth/AuthSetupSteps.tsx#L8-L33), [transition purpose and status](../../client/src/components/auth/AuthTransition.tsx#L7-L17), [rendered transition](../../client/src/components/auth/AuthTransition.tsx#L34-L49)). |
| Branding and vendor marks | `branding/Logo.tsx` selects organization-scoped custom logo URLs, shows loading skeletons, and falls back to the default logo/product name ([implementation](../../client/src/components/branding/Logo.tsx#L20-L41), [loading and fallback](../../client/src/components/branding/Logo.tsx#L43-L86)). `icons/` holds small inline vendor marks used by feature surfaces, such as the GitHub SVG component ([GitHub icon](../../client/src/components/icons/GithubIcon.tsx#L1-L22)). |
| List navigation and search | `pagination/ListPagination.tsx` derives visible range and next/previous offsets from offset, limit, total and fetch state ([implementation](../../client/src/components/pagination/ListPagination.tsx#L3-L37)). `search/SearchBox.tsx` provides a controlled, debounced text input with immediate clear-and-focus behavior ([props/debounce](../../client/src/components/search/SearchBox.tsx#L6-L22), [debounce and clear](../../client/src/components/search/SearchBox.tsx#L35-L49)). |
| Quick access | `quick-access/QuickAccess.tsx` implements the Cmd+K navigation palette for forms, workflows and scripts; it delays data hooks until opened and limits workflow metadata loading to platform admins ([component scope](../../client/src/components/quick-access/QuickAccess.tsx#L35-L44), [deferred and role-scoped queries](../../client/src/components/quick-access/QuickAccess.tsx#L59-L64)). |
| Service browser | `services/` owns the service list/detail surfaces and hooks for actions, queries and live log subscription. `ServiceListSurface` receives service records and callbacks from its parent; `useServiceStream` subscribes to the service WebSocket channel, accumulates lines and unsubscribes on cleanup ([surface inputs](../../client/src/components/services/ServiceListSurface.tsx#L31-L42), [stream contract](../../client/src/components/services/useServiceStream.ts#L15-L27), [subscription lifecycle](../../client/src/components/services/useServiceStream.ts#L40-L87)). |
| Shared settings and structured editors | `shared/` contains cross-feature settings and editor shells. `SettingsToggleRow` exposes a labeled, disabled/busy-aware switch through callbacks ([implementation](../../client/src/components/shared/SettingsToggleRow.tsx#L5-L21), [status/accessibility](../../client/src/components/shared/SettingsToggleRow.tsx#L23-L54)). `JsonYamlEditor` keeps separate JSON/YAML buffers and only emits parsed values; invalid text does not replace the parent's last good document, while the accepted schema prop is reserved and not currently registered with Monaco ([contract](../../client/src/components/shared/JsonYamlEditor.tsx#L1-L17), [editor props](../../client/src/components/shared/JsonYamlEditor.tsx#L28-L60)). |
| Window presentation | `window-management/` supplies shared overlay and minimized-window dock shells. `WindowOverlay` handles maximized-window presentation and `WindowDock` renders dock items only when present, with reduced-motion-aware transitions ([overlay](../../client/src/components/window-management/WindowOverlay.tsx#L10-L28), [dock behavior](../../client/src/components/window-management/WindowDock.tsx#L6-L35)). |

These families are client-side composition and presentation seams. Where a component consumes callbacks or API data, the owning page/service remains responsible for mutations, access checks, and server-derived state.

## Remaining client family entry points

| Family | Responsibility and change location |
|---|---|
| Chart error presentation | `charts/MetricsCardError.tsx` gives metric cards a shared destructive-alert treatment and normalizes an optional unknown error with a fallback message; it reports a failed data read and does not represent chart data or service health ([component contract](../../client/src/components/charts/MetricsCardError.tsx#L5-L19)). |
| Home catalogue composition | `pages/Home/components/HomeBrowse.tsx` composes type filters, collection heading/edit action, sorting, grid/list choice, empty state, resource rendering and pagination from parent-supplied state and callbacks ([inputs](../../client/src/pages/Home/components/HomeBrowse.tsx#L16-L46), [browse controls and rendering](../../client/src/pages/Home/components/HomeBrowse.tsx#L47-L169)). `ResourceCard` and `ResourceList` provide card/list representations with open and pin callbacks, while `CatalogFilters` presents the app/form/agent type selector ([card actions](../../client/src/pages/Home/components/ResourceCard.tsx#L14-L27), [list callbacks](../../client/src/pages/Home/components/ResourceList.tsx#L7-L17), [filter contract](../../client/src/pages/Home/components/CatalogFilters.tsx#L10-L18)). |
| Home collections | `CollectionNavigation.tsx` selects, creates, edits, deletes, and reorders collection tabs through callbacks and an overflow menu ([navigation callbacks](../../client/src/pages/Home/components/CollectionNavigation.tsx#L26-L43), [ordering and visible/overflow selection](../../client/src/pages/Home/components/CollectionNavigation.tsx#L44-L76), [navigation rendering](../../client/src/pages/Home/components/CollectionNavigation.tsx#L96-L162)). `CollectionEditor.tsx` collects metadata, audience/scope, and ordered resource membership, then emits a `HomeCollectionWrite` to its parent. Its own copy states that grouping resources preserves each item's existing access permissions; the collection is organizational ([editor callback boundary and state](../../client/src/pages/Home/components/CollectionEditor.tsx#L28-L60), [scope-filtered resources](../../client/src/pages/Home/components/CollectionEditor.tsx#L62-L85), [save payload](../../client/src/pages/Home/components/CollectionEditor.tsx#L99-L117)). `CollectionIconPicker.tsx` owns searchable icon selection ([picker](../../client/src/pages/Home/components/CollectionIconPicker.tsx#L53-L63), [selection callback](../../client/src/pages/Home/components/CollectionIconPicker.tsx#L73-L97)). The `Home` page owns API reads/mutations and URL-backed browse state, as described in [operations console](operations-console.md); these child components do not grant access. |
| Role row actions | `pages/roles/RoleActionsMenu.tsx` is a small shared edit/delete dropdown that labels itself with the role name and delegates both actions to callbacks; page/API code owns mutations and authorization ([implementation](../../client/src/pages/roles/RoleActionsMenu.tsx#L5-L13), [menu actions](../../client/src/pages/roles/RoleActionsMenu.tsx#L14-L30)). |

These remaining component families sit at the presentation and callback layer. Trace data loading, mutations and policy decisions to their page/service/API owners.
