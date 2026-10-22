---
type: architecture-reference
title: Extension Points, Plugins, and Generated Documentation
description: Maps the operation catalog, plugin skill distribution, SDK generation template, and screenshot documentation pipeline.
tags: [extensions, plugins, skills, sdk, generated-docs]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T02:14:28.318Z
sources:
  - id: openwiki-source-731206ebec48d0e3f858a228
    resource: repo://api/src/services/operation_catalog.py
  - id: openwiki-source-3b8e476a99f100ce7cb044c9
    resource: repo://docs/dev/agent-skills.md
  - id: openwiki-source-8ed4fe1f0104f2e292879028
    resource: repo://plugins/bifrost/.codex-plugin/plugin.json
  - id: openwiki-source-3315aca354864dcb167256a5
    resource: repo://scripts/docs/decide-captures.mjs
  - id: openwiki-source-2a4d3b05acd27cd54ccf08e1
    resource: repo://scripts/docs/post-process.mjs
  - id: openwiki-source-9d2aa1b37ac8bc73e63f094f
    resource: repo://scripts/docs/post-process.test.mjs
  - id: openwiki-source-28700cb15395a4546a162141
    resource: repo://scripts/docs/run-pipeline.sh
  - id: openwiki-source-10b4adcbfed25a006db3d347
    resource: repo://scripts/sync-codex-skills.sh
generated: { by: "bifrost-atlas", at: "2026-10-18T02:14:28.318Z" }
---

# Extension Points, Plugins, and Generated Documentation

Bifrost exposes several extension seams with different ownership rules: catalog-backed operation surfaces coordinate REST, CLI, MCP, and manifest identities; SDK packages are generated from API contracts; public plugin skills are mirrors of canonical repository procedures; and the screenshot pipeline produces docs assets from browser captures. These mechanisms are related by generated output, but none makes generated files an independent source of truth. See [platform architecture](../architecture/platform.md), [development and verification](development-and-verification.md), and [Apps and SDK](../workflows/apps-and-sdk.md).

## Operation catalog

The canonical operation registry defines operation identities and bindings for supported transports. `operation_route()` reads one definition and attaches the operation ID plus MCP, CLI, scope metadata, and asynchronous policy to the FastAPI route's OpenAPI metadata. Import-time validation rejects duplicate operation IDs or duplicate REST, CLI, and MCP bindings, and checks the naming relationship between CLI paths and MCP tool names. The catalog also records exclusions where an operation is intentionally unavailable to an application SDK; availability is surface-specific rather than inferred from the REST route alone (`api/src/services/operation_catalog.py`).

The current implementation lives under `api/src/services/operation_catalog.py`; the originally assigned `api/src/scripts/operation_catalog` seed path is absent in this baseline. Treat the service module and operation contracts as the maintained implementation unless the repository changes again. The validator's source comments explicitly scope some action-scope metadata as inert rather than an enforcement mechanism, so do not interpret catalog presence as authorization proof (`api/src/services/operation_catalog.py`).

## SDK and plugin packaging

The Python integration SDK template is parameterized by generated model and client metadata. It emits dataclasses and conversion helpers, an internal client, request methods, and retry/error handling from the template context; it is a generation input, not an independently maintained integration client (`api/src/services/templates/sdk.py.j2`). For application APIs, TypeScript declarations are generated from the running server's OpenAPI schema as described in [development and verification](development-and-verification.md).

Repository skills use `.claude/skills/` as canonical source. The sync script mirrors all skills into `.agents/skills/` and only the public subset selected by `skills/` symlinks into `plugins/bifrost/skills/`. A validation script guards mirror drift, duplicate IDs/namespaces, legacy copies, and description limits. The plugin manifest declares the plugin identity and points its `skills` field at the packaged skill directory. Thus changing a generated mirror alone is not the documented authoring path (`docs/dev/agent-skills.md`, `scripts/sync-codex-skills.sh`, `plugins/bifrost/.codex-plugin/plugin.json`).

## Documentation screenshot pipeline

`scripts/docs/run-pipeline.sh` coordinates three stages: select capture entries, run the client documentation capture suite, and post-process successful screenshots. Diff mode selects entries whose source globs changed since their recorded Bifrost commit; `--full` selects all non-external entries, while explicit IDs narrow the selection. A capture failure is retained while successful captures are still post-processed, then the pipeline exits with the capture failure (`scripts/docs/run-pipeline.sh`, `scripts/docs/decide-captures.mjs`).

Post-processing applies manifest crop and callout annotations, compares output pixels against existing images, and writes changed images atomically. A successfully exercised capture advances its recorded source SHA even when the pixels are unchanged, preventing no-op captures from being selected forever. The focused post-process tests verify this watermark behavior. This is a docs-site integration; it does not indicate that screenshots were generated during this OpenWiki run (`scripts/docs/post-process.mjs`, `scripts/docs/post-process.test.mjs`).
