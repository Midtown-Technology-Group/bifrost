---
type: architecture-guide
title: Forms, Tables, Files, and Storage
description: Explains form publication and workflow submission, table document APIs, workspace file policy and object storage, and the adjacent PDF renderer.
tags: [forms, tables, files, storage, workflows]
sources:
  - id: openwiki-source-14cfcf4a7b028170949110e2
    resource: repo://api/shared/role_cache.py
  - id: openwiki-source-03e9ccab6454d0aa59bf0e74
    resource: repo://api/src/core/paths.py
  - id: openwiki-source-fb512a8b5d42fb8eb0b06f5f
    resource: repo://api/src/routers/files.py
  - id: openwiki-source-94ddf7355f9484757c1dac8f
    resource: repo://api/src/routers/forms.py
  - id: openwiki-source-920b91302b2b6202914d9824
    resource: repo://api/src/routers/tables.py
  - id: openwiki-source-d3c77eab95498b62df8594c0
    resource: repo://api/src/routers/websocket.py
  - id: openwiki-source-907fee5de9a023a7e2c0c2be
    resource: repo://api/src/services/file_storage/service.py
  - id: openwiki-source-64e27e704af31160ff427dcf
    resource: repo://api/tests/unit/test_role_cache.py
  - id: openwiki-source-9f72542a66b9f96cecc89924
    resource: repo://client/src/components/tables/DocumentQueryPanel.tsx
  - id: openwiki-source-d4e49a678e9056435147c9c2
    resource: repo://client/src/components/tables/TableRecordList.tsx
  - id: openwiki-source-089682e0ec857e52b2db6be1
    resource: repo://client/src/pages/table-detail/DocumentCollectionState.tsx
  - id: openwiki-source-dfd92fe8dc80db789fc09b25
    resource: repo://client/src/pages/table-detail/DocumentPagination.tsx
  - id: openwiki-source-0bdf919554fa4a8103c61cf0
    resource: repo://client/src/pages/tables/TableActionsMenu.tsx
  - id: openwiki-source-f9687712a4982d2c3de607f9
    resource: repo://doc_renderer_service/README.md
generated: { by: "bifrost-atlas", at: "2026-10-18T09:57:17.518Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T09:57:17.518Z
---

# Forms, Tables, Files, and Storage

Forms, tables, and files expose different persistence contracts. A form is a user-facing workflow submission surface; a table is a schema/policy-governed collection of JSON documents; workspace files keep content in object storage with platform metadata/indexing and policy checks. See [data and contracts](../architecture/data-and-contracts.md), [identity and authorization](../architecture/identity-and-authorization.md), and [Apps and SDK](apps-and-sdk.md).

## Forms submit workflow work

Forms bind fields and an access mode to a workflow. The submission route loads an active form, authorizes the caller or embedded session against publication rules, enforces embed action limits, requires a linked workflow, and applies extra rules to embedded sessions such as nonce requirements and rejecting scheduling or honeypot submissions. Authorized submissions invoke the exact linked workflow rather than an arbitrary caller-selected target (`api/src/routers/forms.py`).

Form publication and runtime are separate: publication controls whether an external/public runtime can use the form, while direct form administration and role assignment remain privileged management surfaces. Embedded anonymous submissions receive a deliberately opaque confirmation; authenticated or trusted HMAC flows preserve the existing execution summary behavior (`api/src/routers/forms.py`).

## Tables store policy-checked documents

Tables are platform-defined metadata containers with organization scope; table creation is a platform-admin operation, and a Solution engine cannot create ad-hoc tables because tables must be declared by the Solution manifest. Table document APIs operate within the selected table identity. Insert assigns actor attribution, updates merge JSON data, and optional revision/data preconditions make conditional updates atomic (`api/src/routers/tables.py`).

Table policies and action checks gate reads and writes at the API boundary. Solution-owned table writes additionally verify active Solution ownership and declared manifest targets. Table metadata is not equivalent to document access: the router resolves the table and operation policy before calling the document repository (`api/src/routers/tables.py`).

For configured table WebSocket policies, the policy loader reloads the user's role IDs and names directly from the database for each evaluation, before resolving custom claims. It does this even when `has_role()` is used only by a custom claim's source policy. File-subscription role hydration is separate: when the principal has no role IDs or names yet, it uses the per-user Redis role cache with a database fallback on miss; it does not share the table loader's per-evaluation refresh (`api/src/routers/websocket.py`, `api/shared/role_cache.py`).

## Files and storage boundary

The Files API supports `local` and `cloud` modes, workspace/temp/uploads locations, organization scope rules, Solution-engine signed file context, and per-path file policies. Managed internal prefixes are blocked from freeform caller locations. The storage facade selects S3-compatible or Azure Blob backend from platform settings and composes file operations, indexing, diagnostics, entity resolution, deactivation protection, and reindex helpers (`api/src/routers/files.py`, `api/src/services/file_storage/service.py`).

File bytes and metadata/index records have distinct responsibilities: the backend stores content while the API applies path, scope, policy, and Solution boundary checks and updates metadata/indices for entity-aware workspace files. Do not infer that a local workflow scratch path is durable workspace storage; the purpose-specific path module separates uploads and execution temp paths from cloud workspace operations (`api/src/core/paths.py`, `api/src/services/file_storage/service.py`).

## Document rendering service

`doc_renderer_service/` is a separate adjacent service that owns Markdown/HTML-to-PDF endpoints and an authenticated health route. Its source/image and CI PDF smoke test live in this repository; runtime topology, secrets, ingress, and Kubernetes promotion belong to the separate infrastructure repository. The README names the workspace document-renderer module as its consumer, which is a cross-repository contract reference, not proof of a deployed renderer (`doc_renderer_service/README.md`).

This page describes source contracts and repository documentation only; it does not prove configured storage backends, live form availability, table contents, or renderer health.

## Table browser and document workspace components

The browser splits table inventory from document operations. `TableRecordList` renders scoped table rows and switches from navigation to selection callbacks in selection mode; Solution-managed edit/delete controls are disabled in the related row action menu ([list inputs and scope presentation](../../client/src/components/tables/TableRecordList.tsx#L9-L22), [selection and table navigation](../../client/src/components/tables/TableRecordList.tsx#L63-L123), [managed-action guard](../../client/src/pages/tables/TableActionsMenu.tsx#L6-L34)). The document workspace query panel builds a structured `where` object from field/operator/value conditions and hands it to its parent, which owns the request ([supported operators](../../client/src/components/tables/DocumentQueryPanel.tsx#L16-L42), [filter construction and callback](../../client/src/components/tables/DocumentQueryPanel.tsx#L79-L121)). `DocumentPagination` presents document ranges and page-size changes from parent-supplied state; its retry/error empty states are separately represented by `DocumentCollectionState` ([pagination inputs and transitions](../../client/src/pages/table-detail/DocumentPagination.tsx#L10-L26), [range and callbacks](../../client/src/pages/table-detail/DocumentPagination.tsx#L27-L77), [collection status/retry shell](../../client/src/pages/table-detail/DocumentCollectionState.tsx#L3-L19)).

At the API boundary, table creation/list routes require the platform-superuser dependency, and Solution execution contexts cannot create undeclared tables. Document insertion resolves the target table and evaluates create/update policy before repository writes; document queries resolve policies and compile the read filter before repository access ([table create/list routes](../../api/src/routers/tables.py#L842-L920), [document insert and policy checks](../../api/src/routers/tables.py#L1210-L1277), [document query filter](../../api/src/routers/tables.py#L1532-L1579)). The browser’s controls are inputs to these server checks, not their enforcement mechanism.
