---
type: architecture
title: Data Model, API Contracts, and Schema Evolution
description: How Bifrost separates request/response contracts from persistent models, scopes data access, evolves PostgreSQL schema, and keeps browser types aligned with the API.
tags: [data-model, api-contracts, persistence, migrations]
sources:
  - id: openwiki-source-8e8329e1aa8160698dc450c5
    resource: repo://api/alembic/env.py
  - id: openwiki-source-3640acd6851e27248e3f68ca
    resource: repo://api/alembic/versions/20260918_merge_plat_prof_heads.py
  - id: openwiki-source-6f844d8744f13fd9f6caa125
    resource: repo://api/shared/claims/preresolve.py
  - id: openwiki-source-cfc45a5aeddf8b19f01601a8
    resource: repo://api/shared/claims/registry.py
  - id: openwiki-source-0d5756e514c6f82a0523b826
    resource: repo://api/src/core/database.py
  - id: openwiki-source-d6c44018833858d7b0b0a6b3
    resource: repo://api/src/models/__init__.py
  - id: openwiki-source-6f99c8dbbb4bc3ea05fe385a
    resource: repo://api/src/models/contracts/base.py
  - id: openwiki-source-2d275700e6c16b7ca7873f0f
    resource: repo://api/src/models/contracts/executions.py
  - id: openwiki-source-57096325ad40c6eba9e759f5
    resource: repo://api/src/models/orm/base.py
  - id: openwiki-source-2c27822728321bf9972156d7
    resource: repo://api/src/repositories/org_scoped.py
  - id: openwiki-source-b3c7e600f2c3e32bad877dea
    resource: repo://api/tests/unit/test_alembic_migration_lock.py
  - id: openwiki-source-c1f266a4ebd1b7fb69b26276
    resource: repo://api/tests/unit/test_contract_version.py
  - id: openwiki-source-5f577523bf6055da91bd4cc7
    resource: repo://api/tests/unit/test_org_scoping_enforcement.py
  - id: openwiki-source-33b1f81c9d8cf13263763671
    resource: repo://client/package.json
  - id: openwiki-source-ff67015c30e862ea4697c025
    resource: repo://scripts/generate-types.sh
generated: { by: "bifrost-atlas", at: "2026-10-18T06:28:44.263Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T06:28:44.263Z
---

# Data Model, API Contracts, and Schema Evolution

Bifrost has three related model layers. Pydantic models under `api/src/models/contracts/` describe request and response shapes; SQLAlchemy models under `api/src/models/orm/` describe persisted rows; repositories own database access patterns that handlers and services reuse. A contract is not automatically a database row, and an ORM object should not be treated as a public response shape. For example, execution response models deliberately omit large input, result, log, variable, and context payloads from list summaries; callers fetch a single execution when those details are needed. See [platform runtime](platform.md) and [identity and authorization](identity-and-authorization.md).

## Contract and ORM packages

Contracts are organized by domain (executions, agents, forms, users, OAuth, platform jobs, and others). The package uses a static export registry and lazy import resolution: this keeps lightweight imports from initializing the complete contract graph, while still providing public package exports. SQLAlchemy entities share a declarative `Base`; the public model package also resolves ORM and enum exports lazily. When adding a model, update the appropriate domain module and its export registry if consumers rely on package-level imports.

Routers register on the FastAPI application and use those contracts at the HTTP boundary. FastAPI publishes the schema at `/openapi.json`; root-mounted MCP routes are intentionally excluded from that schema. The generated TypeScript declaration is derived from the running API schema, so a change to a public contract should be followed through the generated browser client contract rather than copied into a second handwritten type definition.

## Persistence and repository ownership

The API uses an asynchronous SQLAlchemy engine and session factory for PostgreSQL. `get_db` provides request-scoped sessions, while repositories encapsulate queries and flush/refresh behavior. Shared `BaseRepository` CRUD is suitable for simple entities; organization-scoped entities use `OrgScopedRepository`, which centralizes organization/global lookup and user role filtering. Its identity parameters are part of the access decision, so callers must construct it with the effective organization and principal appropriate to the operation. Do not reproduce organization cascade predicates in a router.

The central organization-scoped repository distinguishes single-entity lookup from listing: name/key lookup prefers the organization-specific entity and falls back to global, while listing exposes the visible organization and global entries. Regular users receive role checks when the entity has a role table; system/superuser callers rely on already-resolved scope. Repository subclasses declare the ORM model and, where applicable, the role junction table. Focused mechanical tests guard against inline scope queries, scoped models without repositories, and SDK endpoints that accept a scope without resolving it. These guards are security-relevant; see [identity and authorization](identity-and-authorization.md).

## Schema evolution

Alembic migrations under `api/alembic/versions/` are the durable database schema history. Its environment imports ORM entities into `Base.metadata` for autogeneration and uses the configured async database URL for online migration execution. Online upgrades acquire a PostgreSQL session advisory lock, reject a concurrent upgrader, and can compare expected migration heads before emitting revisions. This serializes schema changes and prevents a stale migration image from upgrading against an unexpected head set. Model edits alone do not update deployed databases; they need a reviewed migration and the repository's normal migration checks.

Migration history should be read as a revision graph rather than an inventory of every file: individual revisions declare a `revision` and `down_revision`, while explicit merge revisions join branches where development histories diverged. The environment loads ORM metadata for autogeneration, serializes the online upgrade with a nonblocking PostgreSQL session advisory lock, and can reject an unexpected current-head set before executing DDL ([revision graph example](../../api/alembic/versions/20260918_merge_plat_prof_heads.py#L1-L18), [lock and expected-head guard](../../api/alembic/env.py#L60-L116)). Unit tests exercise concurrent-lock refusal, lock release on migration failure, and stale-head rejection; this is source and unit-test evidence, not evidence of any particular database's current revision ([Alembic lock tests](../../api/tests/unit/test_alembic_migration_lock.py#L135-L177)).

## Shared custom-claim resolution

Shared claim helpers load organization-scoped custom claims, extract references from expression trees, and form a claim dependency graph with cycle detection ([claim registry](../../api/shared/claims/registry.py#L15-L49), [cycle detection](../../api/shared/claims/registry.py#L79-L105)). Before table policy evaluation, the resolver loads applicable organization and solution claims, recursively resolves their dependencies onto the request user's claim cache, and compiles queries through the source table's read filter; an unavailable read grant or invalid query returns no rows, keeping claims from expanding the caller's readable data ([policy pre-resolution](../../api/shared/claims/preresolve.py#L29-L79), [query and read-policy boundary](../../api/shared/claims/preresolve.py#L120-L174)).

## Browser contract generation and verification

`client/src/lib/v1.d.ts` is generated with `openapi-typescript` from an `/openapi.json` document, but the helper does not currently guarantee that its readiness check and generator use the same API. `scripts/generate-types.sh` polls `API_URL` (default `http://localhost:8000`) and then invokes the client package script; `generate:types` independently reads `OPENAPI_URL` (default `http://localhost:3000/openapi.json`). Unless those settings identify the same schema, the helper can check one API while generating from another, or the client command can fail if its default endpoint is unavailable. For a consistent manual run, set both explicitly, for example `API_URL=http://localhost:8000 OPENAPI_URL=http://localhost:8000/openapi.json ./scripts/generate-types.sh`; the script itself does not pass the checked URL through. Contract and model tests live under `api/tests/unit/models/` and `api/tests/contract/`; organization-scope enforcement is covered separately under `api/tests/unit/test_org_scoping_enforcement.py`. Consult [development and verification](../operations/development-and-verification.md) for the supported lane and distinguish generated-type success from runtime compatibility evidence.

## CLI and SDK compatibility gate

The CLI's contract fingerprint covers the request and response DTOs used by entity commands and execution, all SDK DTOs declared in the CLI contract module, and selected table batch DTOs. SDK DTOs are collected from the contract module programmatically, so a newly declared SDK wire model enters the fingerprint without a second hand-maintained list. The test deliberately fingerprints the `/api/version` handshake route rather than a manually enumerated route catalog; the compatibility signal focuses on parsed wire shapes ([fingerprinted DTO set](../../api/tests/unit/test_contract_version.py#L107-L188)).

A test first checks that the CLI and server contract-version constants agree, then compares the live DTO fingerprint with its committed value. A changed fingerprint requires an explicit compatibility decision: bump both version mirrors for a breaking change or refresh the fingerprint for a compatible change ([contract tripwire](../../api/tests/unit/test_contract_version.py#L317-L340)). This gate complements generated TypeScript declarations: the CLI compatibility surface and browser OpenAPI surface have different consumers and should be reviewed separately. See [CLI and MCP surfaces](cli-and-mcp.md) for generated CLI flags and selected MCP signature parity.

## Change guidance

For a field change, trace the contract, router/service mapping, ORM field and migration, then regenerate the browser declarations from the matching API. For organization-owned models, inspect the corresponding repository and role junction behavior, not just the schema. For async jobs and execution attempts, follow their lifecycle pages because those records encode durable runtime state rather than ordinary request DTOs.

The source descriptions here establish code paths and checks, not the state of any deployed database or running API. Cross-repository SDK or workspace compatibility must be verified against that repository's own versioned contract.
