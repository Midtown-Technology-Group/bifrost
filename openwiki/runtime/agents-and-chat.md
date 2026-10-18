---
type: runtime-guide
title: Agents, Chat, and Model Runtime
description: Traces agent configuration, chat runs, model and tool execution, durable run state, budgets, cancellation, and model failover.
tags: [agents, chat, models, tools, runtime]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T02:14:28.318Z
sources:
  - id: openwiki-source-de22288cc45dfcd6befc678e
    resource: repo://api/src/models/orm/agent_runs.py
  - id: openwiki-source-03e71efd19c9835d81e256fa
    resource: repo://api/src/models/orm/agents.py
  - id: openwiki-source-a10ff59368f18a55701c648a
    resource: repo://api/src/routers/agents.py
  - id: openwiki-source-a1010ad7baca0f87584d70a5
    resource: repo://api/src/routers/chat.py
  - id: openwiki-source-039309d7d740f8c307f44d7a
    resource: repo://api/src/services/agent_runtime/budgets.py
  - id: openwiki-source-1fad771810daa5c09c93c0ab
    resource: repo://api/src/services/agent_runtime/model_failover.py
  - id: openwiki-source-701a377617caed477729cca4
    resource: repo://api/src/services/agent_runtime/toolset.py
  - id: openwiki-source-8a6746df0febfc81670117a9
    resource: repo://api/src/services/chat_runs.py
generated: { by: "bifrost-atlas", at: "2026-10-18T02:14:28.318Z" }
---

# Agents, Chat, and Model Runtime

Bifrost separates reusable agent configuration from each chat conversation and autonomous run. An agent record carries its system prompt, access mode, enabled channels/tools, knowledge-source references, optional model-profile override, and execution budgets. Chat requests are persisted as agent runs and can be observed, resumed, cancelled, and reconciled independently of the model call that is currently executing. See [execution and workflows](execution-and-workflows.md), [knowledge and memory](knowledge-and-memory.md), and [OAuth and external MCP](../integrations/oauth-and-external-mcp.md).

## Configuration and access

Agents are database-backed entities. Their organization and owner, active flag, role-based/private access level, Solution ownership, tool IDs, delegation references, system tools, knowledge sources, and optional model profile determine what the runtime can use. Agent creation/update validates referenced workflow tools and delegate agents for existence, active state, type, organization and self-delegation constraints; solution-managed entities are guarded against direct mutation (`api/src/models/orm/agents.py`, `api/src/routers/agents.py`).

Chat conversation creation checks that a referenced agent is active and that the caller can access it. Reusable model profiles returned by Chat are administrator-governed and expose normalized capabilities rather than secrets (`api/src/routers/chat.py`).

## Chat run lifecycle

A chat run submission associates a client run identifier with one conversation and user message. Repeating the same submission can resume the existing run, but reusing an ID with a different submission identity returns conflict. The persisted `AgentRun` records trigger/channel identity, input/output, queued/running/terminal status, token and iteration counts, model, timing, summary fields, verdict, and parent-child run relationships (`api/src/services/chat_runs.py`, `api/src/models/orm/agent_runs.py`).

Chat state loads the conversation, messages, latest run, and retained run events with a latest sequence cursor. A caller may cancel only a run in their own conversation: queued work becomes cancelled immediately, while active work is marked cancelling and receives a Redis cancellation signal. Terminal or unknown runs cannot be cancelled (`api/src/services/chat_runs.py`). The HTTP API and WebSocket event channel provide different access paths to this lifecycle; see [platform architecture](../architecture/platform.md).

## Model, tools, and bounded context

`AgentExecutor` coordinates the iterative model/tool loop. The toolset validates arguments against the live tool schema, emits tool lifecycle events, and bounds the copy of a large tool result sent back into LLM context. Complete results remain available to execution/event storage, while the model-history copy preserves the beginning and end with an explicit truncation marker (`api/src/services/agent_executor.py`, `api/src/services/agent_runtime/toolset.py`).

The shared agent run budget limits model requests and cumulative tokens, reserves room for a final handoff, and supports bounded delegated subtrees; limits are enforced against shared cumulative usage rather than reset for each delegate (`api/src/services/agent_runtime/budgets.py`). Model failover is deliberately narrow: it follows exhausted bounded transport retries for retryable HTTP/connection failures. Validation, ordinary client errors, truncation, budget exhaustion, and cancellation remain terminal on the primary (`api/src/services/agent_runtime/model_failover.py`).

## Durable autonomous runs and evidence

Autonomous runs are tracked in the same `AgentRun` domain with trigger type/source and optional event delivery linkage. Run detail includes step records and usage; summary and verdict metadata support human review and later tuning. These records describe execution outcome and lineage, while workflow platform jobs and chat event streams have their own state semantics. See [durable jobs and schedulers](jobs-and-schedulers.md) and [events and schedules](events-and-schedules.md).

Source establishes the supported contracts and state transitions. This page does not make claims about provider availability, current profile configuration, live agent behavior, or run health.
