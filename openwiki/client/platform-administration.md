---
type: client-guide
title: Platform Administration and Entity Governance
description: Maps the browser's protected administration routes for users, roles, integrations, MCP, event sources, and settings, alongside authenticated user self-service.
tags: [client, administration, identity, integrations, events]
sources:
  - id: openwiki-source-e91649c18f3633512e39bdfc
    resource: repo://client/src/App.tsx
  - id: openwiki-source-7f7e4a749434d8d88cb4dc95
    resource: repo://client/src/components/config/ConfigDialog.tsx
  - id: openwiki-source-94d31b279f1d02db7043d4bd
    resource: repo://client/src/components/integrations/IntegrationOverview.tsx
  - id: openwiki-source-ed2519778a2cec43ccdc0b44
    resource: repo://client/src/components/mcp/MCPServerForm.tsx
  - id: openwiki-source-ee7d1ea1e13f2de3890e2019
    resource: repo://client/src/components/oauth/OAuthConnectionCard.tsx
  - id: openwiki-source-a0f1f61f3b16338b2d3bf799
    resource: repo://client/src/components/policy-rules/PolicyRulesManager.tsx
  - id: openwiki-source-58be647b4d4af3a4ba89f7cc
    resource: repo://client/src/components/ProtectedRoute.test.tsx
  - id: openwiki-source-0b10024142eebe28920031df
    resource: repo://client/src/components/ProtectedRoute.tsx
  - id: openwiki-source-5696b3561510a9f8dbe1bce5
    resource: repo://client/src/components/roles/ConsumerTab.tsx
  - id: openwiki-source-9b0de849dcccabf5e8dcc9eb
    resource: repo://client/src/components/solutions/SolutionSetupWizard.tsx
  - id: openwiki-source-0be120307dcefd09022e8818
    resource: repo://client/src/components/user/UserMCPConnections.tsx
  - id: openwiki-source-e328b2d4d4273de4e09b6271
    resource: repo://client/src/components/users/UserDetailsDialog.tsx
  - id: openwiki-source-d9320ab5467654c5556c9734
    resource: repo://client/src/hooks/useRoles.ts
  - id: openwiki-source-7114b6523dfe448fd2525ffe
    resource: repo://client/src/hooks/useUsers.ts
  - id: openwiki-source-a9f129c1a2d0a13725b34006
    resource: repo://client/src/pages/Events.test.tsx
  - id: openwiki-source-c59f800b021a2b414c4e7835
    resource: repo://client/src/pages/Events.tsx
  - id: openwiki-source-67789bc2a33c952e16362d97
    resource: repo://client/src/pages/Integrations.test.tsx
  - id: openwiki-source-d5902a6b15b25ef63bb03d83
    resource: repo://client/src/pages/Integrations.tsx
  - id: openwiki-source-c8144ba6b736d8a54a3d7781
    resource: repo://client/src/pages/Integrations/IntegrationConnectionStatus.tsx
  - id: openwiki-source-95b86cd58842004e650438e3
    resource: repo://client/src/pages/mcp/components/ConnectionToolCatalog.tsx
  - id: openwiki-source-0cbd61d10c791c59b8c8efd1
    resource: repo://client/src/pages/mcp/components/ServerConnectionList.tsx
  - id: openwiki-source-42e0e72503afb6324e7441d8
    resource: repo://client/src/pages/Roles.tsx
  - id: openwiki-source-d96ee750218665b198b521de
    resource: repo://client/src/pages/settings/AIModelSettings.tsx
  - id: openwiki-source-7690a400397ae02780ddf2f5
    resource: repo://client/src/pages/solutions/SolutionCounts.tsx
  - id: openwiki-source-59df0e4f0f7cb04989d7c2c1
    resource: repo://client/src/pages/user-settings/BasicInfo.tsx
  - id: openwiki-source-8350a1ca34530b958cf95fd9
    resource: repo://client/src/pages/user-settings/SecurityPasskeyDialogs.tsx
  - id: openwiki-source-7a321435767cf1c1a5cfa3c9
    resource: repo://client/src/pages/Users.tsx
  - id: openwiki-source-3ff54128109766da9e3d59bb
    resource: repo://client/src/pages/UserSettings.tsx
generated: { by: "bifrost-atlas", at: "2026-10-18T03:01:08.144Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T03:01:08.144Z
---

# Platform Administration and Entity Governance

The client organizes platform administration around route-guarded list/detail surfaces and API-backed hooks. The route tree is useful for understanding who the UI is designed for; backend authorization remains authoritative. For enforcement and tenant rules, use [identity and authorization](../architecture/identity-and-authorization.md), and for credential or external MCP lifecycle use [OAuth and external MCP](../integrations/oauth-and-external-mcp.md).

## Route access and self-service boundary

The public OAuth callback is a distinct route. Inside the authenticated layout, user, role, integration, MCP-server, event-source, and platform-settings pages are wrapped in `ProtectedRoute` with the platform-administrator requirement. User settings instead require an authenticated session and expose profile, security, personal connections, preferences, and developer tabs ([route tree](../../client/src/App.tsx#L325-L329), [admin routes](../../client/src/App.tsx#L485-L515), [integration, MCP, events, and settings routes](../../client/src/App.tsx#L670-L755), [user-settings routes](../../client/src/App.tsx#L796-L812), [self-service tabs](../../client/src/pages/UserSettings.tsx#L15-L35)).

`ProtectedRoute` displays a loading state while auth loads, defers signed-out recovery to the auth provider, and renders a no-access state when a user lacks the requested route role. These client checks shape navigation and feedback; they do not grant or replace API authorization ([route guard](../../client/src/components/ProtectedRoute.tsx#L17-L54), [focused guard tests](../../client/src/components/ProtectedRoute.test.tsx#L26-L75)).

## Users and role governance

The Users page combines paginated user queries, organization scope, invitations, account actions, selection, and bulk operations. Its controls include server-side sorting, inactive-user visibility, role replacement, and organization movement. Those operations use typed hooks and should be traced to the corresponding server endpoints when changing permissions or identity state ([user page dependencies](../../client/src/pages/Users.tsx#L34-L95), [user query and mutation hooks](../../client/src/hooks/useUsers.ts#L14-L36), [user interaction tests](../../client/src/pages/Users.test.tsx#L486-L517)).

Roles are managed through separate list and role-detail routes. The role UI associates users with role resources such as forms, agents, apps, workflows, and knowledge; this makes a role change a cross-entity governance change rather than a cosmetic label edit ([role resource categories](../../client/src/pages/Roles.tsx#L56-L77), [role detail route](../../client/src/App.tsx#L491-L515), [role assignment hooks](../../client/src/hooks/useRoles.ts#L150-L182)).

## Integrations, OAuth, and MCP administration

The integrations list loads API-backed entities, supports grid/table views, search, create/edit/delete, and selected export/import interactions. Integration connection status summarizes OAuth state in the UI; a displayed state is only the client’s current read of the integration API ([integration page flow](../../client/src/pages/Integrations.tsx#L88-L140), [focused list tests](../../client/src/pages/Integrations.test.tsx#L138-L167), [connection status component](../../client/src/pages/Integrations/IntegrationConnectionStatus.tsx#L100-L143)). OAuth login-provider configuration lives separately under platform settings, while a user's personal connections are part of self-service.

MCP administration also has two separate browser surfaces: platform admins manage configured MCP servers and server connections, and a user manages personal MCP connections from User Settings. Provider credentials, OAuth scopes, discovery, and authenticated tool execution are backend concerns documented in [OAuth and external MCP](../integrations/oauth-and-external-mcp.md); editing a connection form is not proof that an external server is reachable.

## Event sources and platform settings

Event-source administration is an admin-only route with list, organization/global filtering, search, status filtering, and source detail paths. Its UI uses the event API service and renders source lifecycle controls; event delivery, provider webhook setup, and scheduling behavior are covered in [events and schedules](../runtime/events-and-schedules.md) and [provider-managed webhooks](../runtime/provider-webhooks.md) ([event routes](../../client/src/App.tsx#L714-L738), [event-source query and filters](../../client/src/pages/Events.tsx#L63-L113), [event UI tests](../../client/src/pages/Events.test.tsx#L168-L206)).

Platform settings collect AI provider/model configuration, OAuth login providers, MCP exposure, GitHub, workflow keys, memory, policy-like controls, and other operator settings under `/settings/:tab`. That broad settings shell makes the specific page and service the ownership seam for changes; keep sensitive configuration or backend guarantees grounded in their API and service owners ([settings routes](../../client/src/App.tsx#L740-L755), [model settings page](../../client/src/pages/settings/AIModelSettings.tsx#L52-L80)).

## Administration component families

| Family | Responsibility and implementation anchor |
|---|---|
| Configuration values | `config/` supplies typed config edit/delete dialogs. `ConfigDialog` uses the config hooks and clears secret values instead of displaying their stored contents ([ConfigDialog](../../client/src/components/config/ConfigDialog.tsx#L75-L112)). |
| Integration setup | `integrations/` covers connection setup, schema/mapping, org overrides, entity ID mapping, testing, and SDK generation; the overview composes integration status and management actions ([IntegrationOverview](../../client/src/components/integrations/IntegrationOverview.tsx#L128-L160)). |
| OAuth and MCP | `oauth/` presents connection/provider and refresh-job state; `mcp/` configures MCP server connection and discovery fields. These forms do not prove provider reachability ([OAuthConnectionCard](../../client/src/components/oauth/OAuthConnectionCard.tsx#L58-L90), [MCPServerForm](../../client/src/components/mcp/MCPServerForm.tsx#L139-L180)). |
| Policy and roles | `policy-rules/` manages rule CRUD, usage impact, and delete conflict presentation ([PolicyRulesManager](../../client/src/components/policy-rules/PolicyRulesManager.tsx#L91-L128)); `roles/` edits the role records and assigned consumers shown by the [role route](../../client/src/pages/Roles.tsx#L56-L77). Backend policy evaluation and authorization remain separate. |
| Users and self-service | `users/` holds account detail, role, and bulk action dialogs; `user/` holds user-owned connections. `UserSettings` maps profile, security, connections, preferences, and developer tabs to URL routes ([UserDetailsDialog](../../client/src/components/users/UserDetailsDialog.tsx#L30-L64), [user settings tabs](../../client/src/pages/UserSettings.tsx#L15-L35)). |
| Solution administration | `solutions/` contains capture, setup, install/import review, and managed-state UI. `SolutionSetupWizard` derives steps from configuration, connections, and endpoint-key items, while the parent owns writes ([SolutionSetupWizard](../../client/src/components/solutions/SolutionSetupWizard.tsx#L1-L15), [wizard steps](../../client/src/components/solutions/SolutionSetupWizard.tsx#L46-L75)). |

## Change guidance

For an administration change, follow the route wrapper, page-specific query/mutation hook, API DTO, and backend authorization boundary. Keep route-access presentation separate from tenant, organization, role, and credential enforcement. Use the adjacent component/page tests for UI behavior and the backend authorization tests for server guarantees.

## MCP, role, Solution, and account component entry points

| Family | Responsibility and change location |
|---|---|
| MCP server connections | `pages/mcp/components/ServerConnectionList.tsx` summarizes organization connections, OAuth-token presence, cached-tool count, and availability for chat/autonomous use; `ConnectionToolCatalog.tsx` exposes per-tool enabled state and disabled reasons through parent callbacks ([connection summary](../../client/src/pages/mcp/components/ServerConnectionList.tsx#L14-L22), [display and manage link](../../client/src/pages/mcp/components/ServerConnectionList.tsx#L28-L93), [tool selection contract](../../client/src/pages/mcp/components/ConnectionToolCatalog.tsx#L56-L76)). User-owned MCP OAuth connections are presented separately by `components/user/UserMCPConnections.tsx`, which documents the personal connect/forget surface ([component boundary](../../client/src/components/user/UserMCPConnections.tsx#L1-L16), [API client seam](../../client/src/components/user/UserMCPConnections.tsx#L18-L39)). |
| Role consumers | `components/roles/ConsumerTab.tsx` renders role consumer records and candidate assignments with loading/error/retry handling; the role page and API mutation remain the owner of the resource changes ([tab inputs](../../client/src/components/roles/ConsumerTab.tsx#L90-L105), [read-error behavior](../../client/src/components/roles/ConsumerTab.tsx#L57-L87)). |
| Solution inventory | `pages/solutions/SolutionCounts.tsx` formats per-Solution counts for workflows, apps, forms, agents, tables, claims, and files from the Solution model; it is inventory presentation, not a deployment result ([count categories](../../client/src/pages/solutions/SolutionCounts.tsx#L10-L20), [entity keys](../../client/src/pages/solutions/SolutionCounts.tsx#L79-L85)). |
| User profile and security | `pages/user-settings/BasicInfo.tsx` loads profile state and calls the profile service for updates; `SecurityPasskeyDialogs.tsx` presents add/delete passkey dialog state and delegates register/delete actions to the parent ([profile service boundary](../../client/src/pages/user-settings/BasicInfo.tsx#L21-L27), [load and save](../../client/src/pages/user-settings/BasicInfo.tsx#L57-L79), [passkey dialog callbacks](../../client/src/pages/user-settings/SecurityPasskeyDialogs.tsx#L27-L44)). |

As with the surrounding administration pages, these UI components display and collect decisions. They do not prove an MCP provider is reachable, a role mutation is authorized, or a security credential has been accepted; use the corresponding server owners for those claims.
