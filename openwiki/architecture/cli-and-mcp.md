---
type: architecture
title: CLI, Python SDK, and MCP Surfaces
description: Explains the installed Bifrost CLI and Python API client, their compatibility and request boundaries, and how CLI and selected MCP mutation tools stay aligned with server DTOs.
tags: [cli, sdk, mcp, api-contracts, compatibility]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T09:57:17.518Z
sources:
  - id: openwiki-source-08fce95810213449179558e2
    resource: repo://api/bifrost/cli.py
  - id: openwiki-source-609431a8bf8d8c4265713872
    resource: repo://api/bifrost/client.py
  - id: openwiki-source-517ab58fea1c74e9dc99ac59
    resource: repo://api/bifrost/commands/__init__.py
  - id: openwiki-source-0ff0adb0f973a79394104506
    resource: repo://api/bifrost/commands/agents.py
  - id: openwiki-source-af8732fd09e079aba62b2535
    resource: repo://api/bifrost/commands/base.py
  - id: openwiki-source-cc02db31530d472523f278c0
    resource: repo://api/bifrost/commands/forms.py
  - id: openwiki-source-809d63d9a9442382f4fa7e23
    resource: repo://api/bifrost/commands/workflows.py
  - id: openwiki-source-cbea4c476793d718d3b331de
    resource: repo://api/bifrost/contracts/agents.py
  - id: openwiki-source-66458fca4be851582fdbb39f
    resource: repo://api/bifrost/contracts/workflows.py
  - id: openwiki-source-b2713ccf773549a5c55d0f6f
    resource: repo://api/bifrost/dto_flags.py
  - id: openwiki-source-e25793aca1b65a783efbdc6f
    resource: repo://api/bifrost/solution_dev/app_select.py
  - id: openwiki-source-f50af41c31cd2b8e75083387
    resource: repo://api/bifrost/solution_dev/function_host.py
  - id: openwiki-source-23bea113920a6ac33ca2b14f
    resource: repo://api/bifrost/solution_dev/proxy.py
  - id: openwiki-source-3faa8b24eda901516a9e08e5
    resource: repo://api/bifrost/tui/sync_app.py
  - id: openwiki-source-9d32af8fef80ca75c2552775
    resource: repo://api/src/routers/mcp.py
  - id: openwiki-source-a5fa6eff51f3467e0a81e4ee
    resource: repo://api/tests/e2e/mcp/test_mcp_parity.py
  - id: openwiki-source-a04163824ba9917c0c5c2fe3
    resource: repo://api/tests/unit/test_cli_command_branch_coverage.py
  - id: openwiki-source-70d8733a3ef526228a3de330
    resource: repo://api/tests/unit/test_cli_login_ephemeral.py
  - id: openwiki-source-587bd7b56cd95166a5f2d8e7
    resource: repo://api/tests/unit/test_dto_flags.py
generated: { by: "bifrost-atlas", at: "2026-10-18T09:57:17.518Z" }
---

# CLI, Python SDK, and MCP Surfaces

Bifrost exposes platform operations through an installable Python package, an in-workflow SDK, and MCP tools. These are distinct callers of the platform API: the CLI is a Click command tree, the SDK provides Python access from workflows and CLI processes, and MCP exposes authenticated tool calls to external clients. The package entry point is `bifrost`, declared by the standalone package under `api/bifrost/` ([package metadata](../../api/bifrost/pyproject.toml#L5-L45)). For contract ownership and database shapes, see [data model and contracts](data-and-contracts.md); for external MCP authentication and tool behavior, see [OAuth and external MCP](../integrations/oauth-and-external-mcp.md).

## CLI compatibility and command flow

The CLI checks server compatibility before ordinary commands by reading `/api/version`. A mismatch in the explicit CLI contract version is a hard stop; matching contract versions with different build versions produce a non-blocking update notice. An unreachable or older server produces a visible warning, and the `update` command bypasses the gate so an incompatible CLI can replace itself ([CLI version gate](../../api/bifrost/cli.py#L216-L320), [dispatch](../../api/bifrost/cli.py#L841-L887)).

Email/password login stores the returned access and refresh tokens through the credential backend keyed by API URL. It writes the target URL to the current directory's `.env` and removes legacy `BIFROST_ACCESS_TOKEN` and `BIFROST_REFRESH_TOKEN` entries; if updating `.env` fails, the CLI warns and prints only the API URL, not token values ([password-grant handling](../../api/bifrost/cli.py#L1277-L1300)).

Entity command helpers pass a Bifrost client and reference resolver into async Click handlers. The shared command boundary presents structured errors consistently: unresolved or ambiguous references return a distinct exit code with candidates, guarded file conflicts include current-version detail, ordinary client errors preserve server response bodies, and server failures recommend retrying later ([command error mapping](../../api/bifrost/commands/base.py#L157-L281), [async command wrapper](../../api/bifrost/commands/base.py#L284-L370)). This mapping is the CLI presentation contract; it does not replace server-side authorization or conflict checks.

## Python client and workflow transport

`BifrostClient` serves both CLI use and platform-injected workflow use. Its request layer refreshes authentication and applies operation-aware retry rules: safe/idempotent calls can retry transient status responses, while ordinary writes do not gain replay after an uncertain transport failure. Calls under the endpoint prefix used for execution are explicitly excluded from retry replay ([retry policy](../../api/bifrost/client.py#L100-L179), [request gate](../../api/bifrost/client.py#L1020-L1026)).

The SDK facade has a separate `engine_request` path for an engine-injected Unix socket. That path uses the trusted local transport when supplied and otherwise uses the ordinary API client; a local attempt does not fall back to the network API or perform a network token refresh ([client transport boundary](../../api/bifrost/client.py#L1028-L1042)). Keep this transport choice inside the SDK boundary rather than teaching individual workflow helpers about worker transport.

## CLI contract package by domain

`api/bifrost/contracts/` contains lightweight Pydantic mirrors used by installed CLI commands to build flags and request payloads. It is organized by domain—agents, applications, events, files, forms, integrations, organizations, policy rules, services, tables, users, and workflows—with a separate shared enum module. For example, the agent mirrors include agent-specific limits and model settings ([agent DTOs](../../api/bifrost/contracts/agents.py#L12-L52)), while workflow updates use a dedicated update DTO because workflow creation happens through Python-function registration rather than a create request ([workflow DTO and registration note](../../api/bifrost/contracts/workflows.py#L1-L56)). The matching command modules import these contracts to assemble typed payloads before calling the API, such as [agent commands](../../api/bifrost/commands/agents.py#L46-L52), [workflow commands](../../api/bifrost/commands/workflows.py#L56-L65), and [forms commands](../../api/bifrost/commands/forms.py#L35-L45). These SDK-side DTOs mirror the wire shape consumed by the CLI; the FastAPI request contract remains the server-side source and the parity tests guard intended alignment.

## CLI and MCP mutation parity

The CLI derives entity flags and request bodies from the server's create/update Pydantic DTOs. Field types map to Click options, references are resolved before the request body is assembled, and intentional exclusions are listed with reasons so UI-managed fields do not accidentally become CLI inputs ([DTO flag contract](../../api/bifrost/dto_flags.py#L1-L25), [exclusions](../../api/bifrost/dto_flags.py#L42-L118)).

Two focused checks protect that surface. The unit parity test compares generated flags with every writable DTO field, requiring each new field to become a CLI option or receive an explicit exclusion ([CLI DTO test](../../api/tests/unit/test_dto_flags.py#L111-L150)). The MCP parity E2E test compares selected tool signatures with the same DTO fields and exercises CRUD round-trips through the test API bridge; its scope is the tools listed in that test, not every MCP tool ([MCP parity coverage](../../api/tests/e2e/mcp/test_mcp_parity.py#L18-L27), [signature contract](../../api/tests/e2e/mcp/test_mcp_parity.py#L101-L117), [round-trip cases](../../api/tests/e2e/mcp/test_mcp_parity.py#L297-L386)).

MCP adds its own authenticated execution context. The protocol endpoint uses OAuth-backed MCP authentication and derives each caller's user and organization context from a validated token; the default context used to construct tool schemas is not the caller's effective identity ([MCP router contract](../../api/src/routers/mcp.py#L4-L16), [context construction and tool filtering](../../api/src/routers/mcp.py#L344-L418)). CLI/MCP parity does not imply identical authentication, authorization, or transport semantics.

## Command groups and development loops

The entity command tree is organized by platform domain—such as apps, agents, files, forms, integrations, roles, tables, workflows, solutions and custom claims—under a shared invocation layer. That layer creates an authenticated client and a per-command reference resolver; entity handlers remain async HTTP clients of the platform API. The common `--json` option is applied to command groups and their subcommands, and shared output/error helpers define the CLI presentation contract ([command registration](../../api/bifrost/commands/__init__.py#L1-L70), [shared invocation and output](../../api/bifrost/commands/base.py#L48-L85), [resolver injection](../../api/bifrost/commands/base.py#L331-L353)). For example, `claims` translates an organization target to the API `scope` parameter and rejects global targeting because custom claims are organization-scoped ([claims scope and list/create](../../api/bifrost/commands/claims.py#L38-L55), [claim create](../../api/bifrost/commands/claims.py#L100-L120)). CLI SDK request DTOs live in `api/bifrost/contracts/` by domain; special CLI response mirrors, such as the accepted Solution candidate/deploy job pair, are typed Pydantic models rather than claims that every server response has an SDK model ([Solution deploy response contract](../../api/bifrost/contracts/solutions.py#L1-L12)).

`bifrost solution start` adds a local development loop for a selected `standalone_v2` app. App selection reads `.bifrost/apps.yaml` and requires an explicit slug when multiple standalone apps are declared ([app selection](../../api/bifrost/solution_dev/app_select.py#L21-L52)). Its local workflow host indexes decorated Python functions and manifest aliases, while the proxy serves the Vite app, forwards API and WebSocket requests, and handles the local workflow execution endpoint ([local workflow discovery](../../api/bifrost/solution_dev/function_host.py#L63-L105), [development proxy routes](../../api/bifrost/solution_dev/proxy.py#L145-L164)). A watcher re-discovers functions after Python or workflow-manifest changes and reports import failures in the local loop ([watch/reload behavior](../../api/bifrost/solution_dev/reload.py#L1-L55)). This is a development execution path; it does not itself assert that source has been deployed.

The CLI's Textual interface is a presentation layer for interactive selection and progress. In sync review, each row cycles only through its allowed actions, and the batch returns items grouped by push, pull, delete, and skip; enter confirms and escape cancels ([sync result and row action state](../../api/bifrost/tui/sync_app.py#L42-L53), [sync key bindings](../../api/bifrost/tui/sync_app.py#L198-L207)). File selection, connection selection, progress, and watch each have focused TUI apps, while the CLI retains a non-TTY path for scripts ([file selector](../../api/bifrost/tui/file_select.py#L16-L30), [connection selector](../../api/bifrost/tui/connection_select.py#L12-L35), [progress app](../../api/bifrost/tui/progress.py#L44-L66), [watch dashboard](../../api/bifrost/tui/watch.py#L83-L103), [sync TUI selection gate](../../api/bifrost/cli.py#L2346-L2360)).

## Change guidance

When changing an API field exposed to the CLI, trace the server DTO into `api/bifrost/dto_flags.py`, its entity command, the matching MCP tool when one exists, and the API contract-version fingerprint. Keep intentional exclusions explicit and run the parity tests. Breaking CLI-consumed wire changes need a deliberate contract-version decision; generated browser types are a separate contract path described in [data model and contracts](data-and-contracts.md).
