---
type: system-architecture
title: Platform Architecture and Runtime Boundaries
description: The main Bifrost service boundaries, request and startup wiring, browser client composition, and the source paths that connect them.
tags: [architecture, api, frontend, runtime]
sources:
  - id: openwiki-source-2c36cf1f7141958155e52521
    resource: repo://api/src/core/app_wiring.py
  - id: openwiki-source-c80d5915c1013751f8951725
    resource: repo://api/src/core/cache/data_provider_cache.py
  - id: openwiki-source-a810440d1886bf61815e747a
    resource: repo://api/src/core/cache/invalidation.py
  - id: openwiki-source-85f8ba46436ef1f7e5230853
    resource: repo://api/src/core/cache/keys.py
  - id: openwiki-source-de1257550254e1c4095fea5a
    resource: repo://api/src/main.py
  - id: openwiki-source-e91649c18f3633512e39bdfc
    resource: repo://client/src/App.tsx
  - id: openwiki-source-d833fdf3f8a51669ca439c84
    resource: repo://client/src/main.tsx
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
generated: { by: "bifrost-atlas", at: "2026-10-18T02:51:00.673Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T02:51:00.673Z
---

# Platform Architecture and Runtime Boundaries

Bifrost is a browser client backed by a Python platform service. The client lives in `client/`; the API, workflow workers, shared Python packages, and service integrations live under `api/`. PostgreSQL owns application records, Redis supports caching and coordination, and S3-compatible storage owns object content. Work delivery is configurable: the checked-in Compose stacks default to RabbitMQ, while repository documentation records PostgreSQL delivery for MTG production. That distinction is configuration evidence, not proof of any currently running deployment.

## API boundary

`api/src/main.py` creates the FastAPI application and publishes its OpenAPI document at `/openapi.json`. It installs shared exception and request-context wiring, middleware, and the platform's domain routers. The application also mounts MCP OAuth discovery routes and the FastMCP ASGI application at the root after the REST routers, so the MCP mount can handle its own protocol paths without displacing registered API endpoints. Dynamic workflow endpoints are registered during startup to expose enabled workflow contracts in OpenAPI.

The lifespan starts with database initialization, entity-change hooks, dynamic endpoint registration, and configured bootstrap behavior. Built-in policy seeding and Kubernetes transition announcements are best-effort startup tasks that log warnings rather than aborting startup. Shutdown closes the event-loop lag monitor, retry HTTP client, pubsub, health-check clients, and database resources. The FastAPI and FastMCP lifespan contexts are combined so MCP session management is active alongside the API lifecycle.

Global exception handlers map validation failures, database conflicts, missing results, timeouts, unavailable database connections, and unexpected exceptions to a shared `ErrorResponse` shape. The same handlers and request attribution middleware are shared with the worker-local SDK HTTP app, keeping SDK calls over its Unix socket aligned with the public HTTP boundary.

## Browser boundary

The React application starts in `client/src/main.tsx`, which installs the shared query client, theme provider, organization-scope invalidation, editor setup, and UI notifications. `App.tsx` composes authentication and organization providers, protected routes, layouts, error boundaries, and lazy-loaded pages. The client proxies API traffic through its development server or production web server, so the normal browser path is same-origin. Generated OpenAPI types are described in [data and contracts](data-and-contracts.md).

## Cache ownership and consistency

Redis cache keys are generated centrally and namespace organization-owned data separately from global data. The data-provider result cache combines that scope, provider name, and a deterministic parameter hash; it applies a five-minute default TTL, exposes targeted or provider-wide invalidation, and uses short SET-NX locks to reduce duplicate concurrent computation ([scoped keys and parameter hashing](../../api/src/core/cache/data_provider_cache.py#L25-L82), [result storage and invalidation](../../api/src/core/cache/data_provider_cache.py#L136-L229), [compute lock](../../api/src/core/cache/data_provider_cache.py#L232-L265)). Cache connection/operation failures are logged as optional-cache failures rather than changing the provider response path. Config cache invalidation has a separate versioned-key mechanism: global writes advance a global version, so organization merged views naturally move to a fresh key without relying on enumeration alone ([versioned config key](../../api/src/core/cache/keys.py#L59-L97), [config invalidation](../../api/src/core/cache/invalidation.py#L134-L167)). These helpers describe cache policy in source; they do not prove Redis is reachable or any cache is warm.

## Cross-cutting ownership

Routers define HTTP surfaces and should keep domain decisions in services, repositories, or shared policy modules. Database access belongs behind request-scoped async SQLAlchemy sessions and repository patterns. Workers own long-running execution; API requests enqueue or create durable work rather than making a web process the implicit owner of every task. See [execution and workflows](../runtime/execution-and-workflows.md) and [jobs and schedulers](../runtime/jobs-and-schedulers.md).

The repository's architecture is intentionally broader than a single process: API, client, worker, scheduler, database, queue, cache, and object-storage roles are composed by different development, test, and deployment manifests. Use [development and verification](../operations/development-and-verification.md) and [deployment topology](../operations/deployment-and-infrastructure.md) to understand a specific lane. This page does not establish runtime health or deployment state.
