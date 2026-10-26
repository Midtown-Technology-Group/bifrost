---
type: architecture-guide
title: Applications, App Builder, and SDK
description: Explains App source ownership, server compilation and bundling, V2 deployment, runtime assets, and SDK provenance/update paths.
tags: [applications, app-builder, sdk, deployment, source]
sources:
  - id: openwiki-source-564694badeb8b27c2ff74e75
    resource: repo://api/src/routers/app_code_files.py
  - id: openwiki-source-3dedfabd7ed53f1bf6f303b2
    resource: repo://api/src/routers/applications.py
  - id: openwiki-source-078680632a3abec27cb55537
    resource: repo://api/src/services/app_builder/README.md
  - id: openwiki-source-35fa1ec68f4754ae7f6519a1
    resource: repo://api/src/services/app_bundler/bundle.js
  - id: openwiki-source-23eec7d3585ed82f3e37a7a5
    resource: repo://api/src/services/application_sdk_status.py
  - id: openwiki-source-8aff3d54f9e79dc5c0ec5a01
    resource: repo://api/src/services/sdk_package/__init__.py
  - id: openwiki-source-9bf01d690da5a625e9646553
    resource: repo://api/src/services/sdk_package/build_sdk.js
  - id: openwiki-source-03ccfe7a1f5e62c06901355c
    resource: repo://client/src/lib/app-sdk/execution-stream.ts
  - id: openwiki-source-772c54842f830d450e17dff0
    resource: repo://client/src/lib/app-sdk/sdk-contract.json
  - id: openwiki-source-08ed72b9e5022d3732d574de
    resource: repo://client/src/lib/app-sdk/sdk-contract.test.ts
  - id: openwiki-source-64c679fe48b0a2ce0bfd6adb
    resource: repo://client/src/lib/app-sdk/use-workflow.ts
  - id: openwiki-source-30a14c1619cce15bc443e6e4
    resource: repo://client/src/lib/app-sdk/wire-surface.ts
  - id: openwiki-source-53790951630250b28556cccd
    resource: repo://docs/runbooks/application-sdk-update.md
generated: { by: "bifrost-atlas", at: "2026-10-18T02:34:56.337Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T02:51:00.673Z
---

# Applications, App Builder, and SDK

Bifrost contains two related App execution models. The legacy builder persists structured draft/live versions and server-compiled files; independent standalone V2 Apps are built from retained source into immutable deployments and served as same-document bundles. Their lifecycle commands and ownership differ: legacy Apps are published, standalone V2 Apps are deployed, and Solution-managed Apps are updated through Solution deployment. See [application authoring](../client/application-authoring.md), [data and contracts](../architecture/data-and-contracts.md), and [Solutions and workspace promotion](solutions-and-workspace-promotion.md).

## Structured App Builder and source files

The legacy builder models an application as organization-scoped metadata with draft and active version pointers. A published version is a snapshot of pages and components; rollback changes the active pointer without rewriting the draft. Pages contain routes and data-source configuration, while components form ordered trees through parent IDs. This is the structured low-code model documented by the maintained App Builder service notes (`api/src/services/app_builder/README.md`).

Server-side App file APIs read source from S3-backed repository storage and compiled artifacts from app storage. Draft files are editable for server-managed apps; writes are platform-admin gated, reject Solution-managed or release-governed paths, update storage/index state, and compile TS/TSX on write. The Babel compiler rewrites `bifrost` imports to the injected runtime, strips relative imports resolved by the runtime loader, maps package imports to injected dependencies, then transforms TypeScript/React and exports component entry points (`api/src/routers/app_code_files.py`, `api/src/services/app_compiler/compile.js`).

The render endpoint serves cached preview/live compiled code and CSS, reads compiled artifacts on cache misses, generates Tailwind CSS from code candidates, and warms the cache. It does not compile on render (`api/src/routers/app_code_files.py`).

## Standalone V2 build and deployment

Independent V2 source deployment is a durable PlatformJob. The API streams the uploaded archive to temporary staging, stores the source payload for the worker, enqueues a deduplicated App deployment with a resource lock, and returns the job location. The endpoint rejects Solution-managed and non-standalone V2 applications. The job builds before activating a deployment, and failed builds leave the current active deployment in place (`api/src/routers/applications.py`, `docs/runbooks/application-sdk-update.md`).

The V2 builder uses esbuild in an isolated temporary build directory, validates source/output path containment, emits hashed JavaScript/CSS assets, and distinguishes preview sourcemaps from live minification. Runtime assets are served from the active deployment; HTML is no-cache while hashed JS/CSS assets are immutable-cacheable (`api/src/services/app_bundler/bundle.js`, `api/src/routers/app_code_files.py`).

Publishing a legacy App queues a durable build/publish operation and deduplicates concurrent requests. Standalone V2 Apps must use deploy, not publish (`api/src/routers/applications.py`).

## SDK source and compatibility

The web SDK source is owned in the client tree at `client/src/lib/app-sdk`, copied into the API image, and bundled as one ESM package by the API's SDK package builder. React and React DOM remain peer dependencies so the consuming app shares one React instance. The server package is version-stamped for the running Bifrost instance, and its fingerprint hashes the exact bundled bytes (`api/src/services/sdk_package/__init__.py`, `api/src/services/sdk_package/build_sdk.js`).

A deployed V2 App records package version, SDK fingerprint, contract version, and build time. A Bifrost upgrade can make an SDK update available but does not mutate an existing deployment. An administrator starts an SDK-only rebuild against retained source; it uses the normal V2 compiler and atomically swaps the active deployment only after success. Independent V2 source is retained as a sanitized archive, while prebuilt-only deployments without source cannot be rebuilt (`docs/runbooks/application-sdk-update.md`).

## SDK wire contract and runtime transport

The installable `bifrost` package is built on demand from the client-owned `client/src/lib/app-sdk` sources and served as an ESM tarball. The shared esbuild toolchain keeps `react`, `react-dom`, JSX runtime, and `lucide-react` external so the consuming app supplies those peer dependencies. The package metadata carries both a content fingerprint of the exact bundle bytes and an integer SDK/server wire-contract version. The fingerprint changes with built content; the contract version is a manual signal reserved for a decided breaking wire change. ([`sdk_package/__init__.py`](../../api/src/services/sdk_package/__init__.py#L1-L13), [`sdk_package/__init__.py`](../../api/src/services/sdk_package/__init__.py#L147-L185), [`sdk_package/__init__.py`](../../api/src/services/sdk_package/__init__.py#L188-L210), [`build_sdk.js`](../../api/src/services/sdk_package/build_sdk.js#L27-L46))

The client maintains `wire-surface.ts` as a source-derived inventory of HTTP request/response fields and WebSocket frame fields consumed by the SDK. Its test hashes that object and requires a conscious snapshot refresh on any change; the instructions say to bump `sdk-contract.json` and explain the break when the change is incompatible. This is a useful client-side tripwire, but its own source says the inventory is not executed against the server, so it does not prove runtime API parity. The JSON history also records an additive streaming change without a contract bump and a later breaking table-invalidation change with one. ([`wire-surface.ts`](../../client/src/lib/app-sdk/wire-surface.ts#L1-L17), [`sdk-contract.test.ts`](../../client/src/lib/app-sdk/sdk-contract.test.ts#L1-L59), [`sdk-contract.json`](../../client/src/lib/app-sdk/sdk-contract.json))

The SDK `useWorkflow` hook executes through the `BifrostProvider` authenticated transport and includes the app and organization scope when present. A normal run starts asynchronously with `POST /api/workflows/execute`, consumes status/log frames from the execution WebSocket, and fetches `GET /api/executions/{id}` for the terminal result because terminal frames omit result bodies. If the socket goes down it polls the execution endpoint; transient and already-terminal responses settle directly from the POST. These are SDK client semantics and remain distinct from the server-side app source compiler described above. ([`use-workflow.ts`](../../client/src/lib/app-sdk/use-workflow.ts#L1-L30), [`use-workflow.ts`](../../client/src/lib/app-sdk/use-workflow.ts#L248-L314), [`execution-stream.ts`](../../client/src/lib/app-sdk/execution-stream.ts#L1-L66))

SDK status is derived from both app provenance and current bundle metadata: missing/incomplete provenance (or unavailable current fingerprint/contract) is `unknown`; a different contract version is `update_required`; matching fingerprints are `current`; otherwise the SDK is `update_available`. Inline V1 Apps are `not_applicable`. If building current SDK metadata fails, the status loader degrades to a null fingerprint/contract instead of failing the app metadata request. None of these calculated states indicates an SDK update has already been applied; only a successful build/deployment records new provenance. ([`application_sdk_status.py`](../../api/src/services/application_sdk_status.py#L50-L90), [`application_sdk_status.py`](../../api/src/services/application_sdk_status.py#L93-L101))

## Source and operational limits

Source storage, compiled preview/live artifacts, and immutable V2 deployment assets are separate layers. A repository path alone does not imply that Bifrost manages the App source, and a legacy server-side draft endpoint conflicts when source is local to the developer. This page describes frozen code and repository documentation; it does not establish current SDK status, deployment versions, or live App behavior.
