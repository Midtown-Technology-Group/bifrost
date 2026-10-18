---
type: integration-architecture
title: OAuth, Integrations, and External MCP
description: How Bifrost configures workflow OAuth providers, stores user or service credentials, calls external MCP servers, and exposes its own role-filtered MCP tools.
tags: [oauth, integrations, mcp, authentication, security]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T02:14:28.318Z
sources:
  - id: openwiki-source-5d674a40718e7aa7da02ac10
    resource: repo://api/src/routers/mcp_connections.py
  - id: openwiki-source-6d8329f2468b634a4713cd55
    resource: repo://api/src/routers/oauth_connections.py
  - id: openwiki-source-71fead95462da8a2e9c9d8d3
    resource: repo://api/src/services/mcp_client/auth_resolution.py
  - id: openwiki-source-993cf13ee0deb261105e808d
    resource: repo://api/src/services/mcp_client/client.py
  - id: openwiki-source-b2fcfffdeb7347ed8317017a
    resource: repo://api/src/services/mcp_server/auth.py
  - id: openwiki-source-c03efcc833e9a07a34b76a61
    resource: repo://api/src/services/mcp_server/tool_access.py
generated: { by: "bifrost-atlas", at: "2026-10-18T02:14:28.318Z" }
---

# OAuth, Integrations, and External MCP

There are separate OAuth surfaces in Bifrost. `/auth/oauth` handles user sign-in; `/api/oauth` configures OAuth connections used by workflows and integrations; `/api/mcp` exposes Bifrost as an MCP server, while `/api/mcp-connections` connects Bifrost to external MCP servers. Treating these as one credential pool creates the wrong trust boundary. See [identity and authorization](../architecture/identity-and-authorization.md) and [events and schedules](../runtime/events-and-schedules.md).

## Workflow and integration OAuth

OAuth provider configuration is admin-managed and contains provider endpoints, flow type, client configuration, requested scopes, and optional tenant/entity URL templates. The authorization-code path builds a redirect URL with state for CSRF protection and adds a PKCE challenge when the provider opts in. The callback validates the provider and redirect binding, consumes the PKCE verifier once before token exchange, and rejects missing or replayed PKCE state. Per-mapping state binds the resulting token to the mapping's organization rather than overwriting the global fallback token. Client-credentials providers skip user authorization and obtain tokens through the refresh path.

Tokens are stored through the OAuth repository and are used by integration dispatch; a refresh scheduler maintains expiring tokens. Per-user and per-mapping credentials must retain their scope so one tenant's authorization cannot become a global integration credential. The workflow OAuth route is distinct from SSO and from MCP's protocol-specific access tokens.

## Bifrost calling external MCP servers

An MCP server template and an organization-scoped connection are separate records. A connection can override the template URL and stores its remote tool catalog; public response conversion omits the encrypted client secret. Before calling a tool, Bifrost resolves whether the vendor receives the requesting user's credential or a configured service credential. User tokens can be refreshed; a failed refresh falls through only where the connection's availability flags permit that service identity, otherwise the call requires reauthorization.

The outgoing MCP client uses Streamable HTTP. It negotiates modern `server/discover` first and falls back to the legacy initialize handshake only when the peer does not provide positive modern evidence. The auth resolver chooses the token before transport; the HTTP client only sends that token. Tool invocation checks the connection's catalog and enabled state before calling the remote server, then returns a normalized result that preserves structured content and error state.

## Bifrost as an MCP server

Bifrost's hosted MCP endpoint uses its own OAuth 2.1 provider, including authorization-code with PKCE, dynamic client registration, discovery metadata, and Bifrost JWT validation. Issued access tokens carry an MCP-specific marker/scope and resource, which keeps them distinguishable from ordinary REST bearer tokens. The MCP ASGI app is stateless over HTTP, applies host/origin protection, and starts a workflow catalog synchronizer for the lifespan of the app.

Tool visibility is computed from agents a user may access and the tools available through those agents, then filtered by the global MCP allow/block configuration. Workflow visibility uses the same repository gate as execution so tools/list and tools/call do not drift. This makes MCP another authenticated platform interface, not a bypass around REST authorization. See [agents and chat](../runtime/agents-and-chat.md).

## Failure and verification boundaries

OAuth discovery against an external MCP server may return no metadata on unavailable, invalid, or non-JSON responses; the admin flow can use manual configuration instead. Missing or expired PKCE state fails closed for PKCE-enabled providers. Remote MCP tool errors remain tool-call errors, while credentials that cannot be refreshed require the user or operator to reauthorize unless an explicitly configured service credential applies.

Focused tests cover integration PKCE round trips, OAuth state replay and token refresh, MCP external connection scope, tool access matrices, and protocol conformance. This source documents the platform's client/server contract; it does not establish the current identity-provider settings or behavior of any vendor MCP server.
