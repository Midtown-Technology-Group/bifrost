---
type: security-architecture
title: Identity, Tenant Isolation, and Authorization
description: How Bifrost validates typed credentials, builds principals, resolves organization scope, and applies role-aware access to platform resources.
tags: [authentication, authorization, multi-tenancy, security]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T10:48:47.021Z
sources:
  - id: openwiki-source-f15d5434da58dac0df4b43d3
    resource: repo://api/shared/file_policies.py
  - id: openwiki-source-121ff082db8ef5cb7a60b778
    resource: repo://api/shared/policies/probe.py
  - id: openwiki-source-9db59af1301541a53b34aa57
    resource: repo://api/src/core/auth.py
  - id: openwiki-source-8a017cde4e67cd51621cc4d1
    resource: repo://api/src/core/org_filter.py
  - id: openwiki-source-2c27822728321bf9972156d7
    resource: repo://api/src/repositories/org_scoped.py
  - id: openwiki-source-f8ff6c0fbadd604974c3e944
    resource: repo://api/src/repositories/workflows.py
  - id: openwiki-source-e21a1573760c1694515a2ac3
    resource: repo://api/src/routers/auth.py
  - id: openwiki-source-f5da7400d57c95edf47b3954
    resource: repo://api/src/routers/tools.py
  - id: openwiki-source-b3a7ed3e17a90f01350470f5
    resource: repo://api/tests/e2e/platform/test_external_users.py
  - id: openwiki-source-5f577523bf6055da91bd4cc7
    resource: repo://api/tests/unit/test_org_scoping_enforcement.py
  - id: openwiki-source-fbaf21473f7ee6fbb0c92b51
    resource: repo://client/src/contexts/AuthContext.test.tsx
  - id: openwiki-source-6eed354fc700c07823d765c6
    resource: repo://client/src/contexts/AuthContext.tsx
generated: { by: "bifrost-atlas", at: "2026-10-18T10:48:47.021Z" }
---

# Identity, Tenant Isolation, and Authorization

Bifrost authorization is a chain: credential validation creates a `UserPrincipal`; endpoint dependencies distinguish ordinary users from administrators and internal execution credentials; effective organization scope is resolved before data access; repositories and policy evaluators enforce resource-level access. A valid token alone does not grant access to every organization. See [data and contracts](data-and-contracts.md) for persistence boundaries and [OAuth and external MCP](../integrations/oauth-and-external-mcp.md) for provider credentials.

## Credential to principal

REST authentication accepts a bearer access token or browser `access_token` / typed embed-token cookie. Token decoding checks the expected access-token type and issuer/audience; REST rejects MCP-scoped and actor-scoped tokens. The token must identify a user and include the required email claim. A regular non-embed user must carry a valid organization ID, while a superuser may have no organization as the global system account. Invalid or wrong-purpose credentials fail closed instead of being reused across protocol boundaries.

Browser logout first calls `POST /auth/logout` with the same-origin refresh cookie and CSRF header when available; the server revokes the refresh-token session. The client clears local authentication only after success, retries once after refreshing a 401 response, and retains local state if logout still fails (`client/src/contexts/AuthContext.tsx`, `api/src/routers/auth.py`).

`UserPrincipal` carries organization and superuser identity alongside typed flags for embeds, external principals, engine attempts, delegated identity, and service identity. Those flags matter because an internal engine token transports execution authority while delegated identity supplies the caller context for authorization and audit. `get_execution_context` verifies renewable engine credentials against an active attempt and loads current role IDs/names from the role cache (database fallback on cache miss) before execution-time policy evaluation.

## Resolving tenant scope

List and write helpers do not trust a request's `scope` value for an ordinary organization user. `resolve_org_filter` lets a superuser request all organizations, global-only rows, or one organization; ordinary users always see their own organization's rows plus global rows, regardless of a supplied scope. `resolve_target_org` similarly restricts non-bypass callers to their home organization. These rules prevent request parameters from widening tenant access.

For entity lookups, `OrgScopedRepository` centralizes organization-to-global fallback and optional role checks. Name/key lookups choose the organization-specific entity on collision; list operations include visible organization and global rows. Table row policies are a separate gate: they evaluate action rules, default deny when no rule grants access, and compile read filters into SQL. File policies apply a parallel per-path decision. Use the repository and policy services that own the resource rather than writing router-local filters; the enforcement tests detect common bypass patterns.

Workflow-tool catalog listing applies the workflow access gate after organization scope: non-superusers see only tools allowed by their access level and role, while superusers bypass per-row checks. A global tool is only in the candidate scope; global scope alone does not grant an external user access to a role-based tool (`api/src/repositories/workflows.py`, `api/src/repositories/org_scoped.py`, `api/src/routers/tools.py`).

## Elevated and public boundaries

`get_current_superuser` is the platform-admin dependency for operations that intentionally cross organization boundaries. Internal engine/service credentials use a distinct dependency, so an ordinary user session cannot be substituted for execution authority. Embedded app/form sessions are typed and tied to an authorized resource; the embed token's HMAC verification permits a resource to be served without pretending the token holder is a member of its organization. External principals remain subject to organization cascade and do not receive the `authenticated` access tier implicitly.

Authentication and organization authorization are complemented by CSRF/request controls, audit actor context, and explicit public endpoint contracts. An endpoint that is deliberately outside ordinary scope resolution should have an explicit reason and focused test coverage. This page describes source behavior, not a live identity-provider or tenant configuration.

## Verification and extension

When adding an endpoint, identify the credential class it accepts, derive scope from the principal or owning resource, use the relevant repository/policy boundary, and preserve audit attribution. Focused checks include org-scope enforcement, role and policy tests, auth token tests, and route-level security tests under `api/tests/unit/` and `api/tests/e2e/security/`. Do not treat these source-level checks as proof of deployed configuration. See [security and audit](../operations/security-and-audit.md) for sensitive-data and audit invariants.
