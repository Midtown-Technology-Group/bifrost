---
type: client-guide
title: Agent, Chat, and AI Configuration Workbench
description: Maps browser routes and state for agent setup, live chat, run review, and reusable model profiles while keeping UI behavior distinct from server runtime authority.
tags: [client, agents, chat, ai-models, run-review]
sources:
  - id: openwiki-source-9d88a6722516b04c86493cd4
    resource: repo://client/src/components/agents/AgentRunsPanel.tsx
  - id: openwiki-source-b57084c1e669ea4d3ec064c4
    resource: repo://client/src/components/agents/RunReviewPanel.tsx
  - id: openwiki-source-6497ed259902d754b60e1830
    resource: repo://client/src/components/ai/ModelProfileSelector.test.tsx
  - id: openwiki-source-56bc5dbf027ce64d585dafcc
    resource: repo://client/src/components/ai/ModelProfileSelector.tsx
  - id: openwiki-source-b8fe74c21db0c8ad58643bb3
    resource: repo://client/src/components/chat/ChatInput.tsx
  - id: openwiki-source-5fd8ceeff7e5fedc8299829e
    resource: repo://client/src/components/knowledge/KnowledgeDocumentDrawer.tsx
  - id: openwiki-source-7a5aa0a7191704c050b86dbb
    resource: repo://client/src/hooks/useAgentRunUpdates.ts
  - id: openwiki-source-56a6a6b41429c393368e6eac
    resource: repo://client/src/hooks/useAgents.ts
  - id: openwiki-source-038193d17818a71921adf2b0
    resource: repo://client/src/hooks/useChatStream.test.tsx
  - id: openwiki-source-d34d0a368908151a82f9e22e
    resource: repo://client/src/hooks/useChatStream.ts
  - id: openwiki-source-1762fe412fdad90ead10e917
    resource: repo://client/src/pages/agents/AgentDetailPage.test.tsx
  - id: openwiki-source-920204f6c60ee950e22432cd
    resource: repo://client/src/pages/agents/AgentDetailPage.tsx
  - id: openwiki-source-273c503d46ba497f8b30c174
    resource: repo://client/src/pages/agents/AgentRunDetailPage.tsx
  - id: openwiki-source-4f677497ef27338b4efb0ee7
    resource: repo://client/src/pages/agents/FleetPage.tsx
  - id: openwiki-source-86f9fb3d86c7cee939cc1db7
    resource: repo://client/src/pages/knowledge/KnowledgeDocumentList.tsx
generated: { by: "bifrost-atlas", at: "2026-10-18T02:51:00.673Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T02:51:00.673Z
---

# Agent, Chat, and AI Configuration Workbench

The browser groups agent management, conversation, and run-investigation tasks around API hooks and focused service modules. The pages render and reconcile server-owned state; they do not execute agent or chat work themselves. For backend run and provider behavior, see [agents and chat](../runtime/agents-and-chat.md) and [model inference and code apps](../runtime/model-inference-and-code-apps.md). The shared React router, query client, and generated OpenAPI layer are described in [frontend architecture](frontend-architecture.md).

## Agent fleet and detail routes

The fleet page composes agent queries with fleet statistics, org labels for platform administrators, local name/description filtering, and an opt-in inactive-agent view. `useAgents` encodes scope as an omitted parameter, `global`, or an organization ID, and exposes optional inactive, statistics, and discovery filters; these are request controls whose access semantics remain server-owned ([fleet composition](../../client/src/pages/agents/FleetPage.tsx#L95-L140), [agent query parameters](../../client/src/hooks/useAgents.ts#L33-L88)).

Agent detail uses `AgentDetailPage` for both create and edit. In create mode it renders Settings while Overview and Runs are unavailable; edit mode loads the agent and recent-run count, and the selected tab is carried in the URL query so a deep link can open Runs or Settings. A user can start a chat from the detail header only when the agent exposes the chat channel and is active; the page creates a conversation and navigates to its route ([detail state and tabs](../../client/src/pages/agents/AgentDetailPage.tsx#L66-L123), [chat admission UI](../../client/src/pages/agents/AgentDetailPage.tsx#L156-L170), [route-mode tests](../../client/src/pages/agents/AgentDetailPage.test.tsx#L3-L8)).

## Durable chat projection and streaming

`Chat` sets the active conversation from the URL and checks chat availability. It gives platform administrators a setup or retry state when model availability is absent or cannot be read, while the chat layout owns the conversation experience ([chat route](../../client/src/pages/Chat.tsx#L17-L62), [setup states](../../client/src/pages/Chat.tsx#L66-L122)).

`useChatStream` starts and cancels server-owned runs over HTTP. The WebSocket carries replayable events; on initial entry and reconnect the hook reads server run state and hydrates the local projection. Sending stages an optimistic user turn, submits a durable run command, then reconnects and replays server state to close the subscription race. Stop sends cancellation and reloads the server projection ([durable chat hook](../../client/src/hooks/useChatStream.ts#L1-L6), [restore and reconnect](../../client/src/hooks/useChatStream.ts#L348-L431), [send and stop](../../client/src/hooks/useChatStream.ts#L433-L529)). The focused hook tests cover subscription-before-hydration, reconnect replay, cancellation and retry after restore failure ([chat stream tests](../../client/src/hooks/useChatStream.test.tsx#L137-L158), [reconnect test](../../client/src/hooks/useChatStream.test.tsx#L317-L333), [restore failure test](../../client/src/hooks/useChatStream.test.tsx#L425-L435)).

## Run review and live updates

Agent run detail combines an API-fetched record with streamed steps, sorts merged steps by sequence number, and resolves the owning agent from the run when necessary. It subscribes to run updates so summary transitions and step writes can refresh the displayed record ([run detail merge and ownership](../../client/src/pages/agents/AgentRunDetailPage.tsx#L164-L209), [update subscription](../../client/src/pages/agents/AgentRunDetailPage.tsx#L321-L325)). The update hook invalidates run-list, per-agent statistics, and detail query caches; its shared list channel is documented as platform-admin-only, while a detail view can subscribe to its run-specific channel ([run update hook](../../client/src/hooks/useAgentRunUpdates.ts#L1-L8), [cache invalidation](../../client/src/hooks/useAgentRunUpdates.ts#L27-L61)). UI review, flagging, tuning, and verdict actions stay separate from the worker's durable run lifecycle.

## Reusable model-profile selection

AI settings and selectors call typed service functions that read and mutate provider connections, model profiles, and assignment records through API endpoints ([AI model service](../../client/src/services/aiModels.ts#L40-L105), [profile and assignment operations](../../client/src/services/aiModels.ts#L121-L204)). `ModelProfileSelector` loads saved profiles and connections, optionally filters for profiles enabled for chat, and preserves the currently selected saved profile even if that profile no longer matches the filter. Inline creation requires at least one provider connection; a new profile created in chat-only mode is marked enabled for chat ([selector behavior](../../client/src/components/ai/ModelProfileSelector.tsx#L80-L129), [chat profile creation](../../client/src/components/ai/ModelProfileSelector.tsx#L305-L390)). Component tests exercise reusable selection, failed-create draft preservation, retry, and the chat-enabled flag ([selector tests](../../client/src/components/ai/ModelProfileSelector.test.tsx#L78-L106), [failed create and retry](../../client/src/components/ai/ModelProfileSelector.test.tsx#L253-L308), [retained selection test](../../client/src/components/ai/ModelProfileSelector.test.tsx#L310-L366)).

## Component family responsibilities

| Family | Responsibility and source to follow |
|---|---|
| Agent run/review components | `AgentRunsPanel` owns the run-history filter state, pagination, and rerun interaction; run cards, timelines, captured-data, and review sheets are rendered subcomponents. Start at [AgentRunsPanel](../../client/src/components/agents/AgentRunsPanel.tsx#L110-L157) and follow [review panel](../../client/src/components/agents/RunReviewPanel.tsx#L1-L60). |
| Chat components | `ChatWindow` joins message state into a timeline and renders tool-call cards; `ChatInput` owns draft text, attachment selection, mentions, and send submission. See [ChatWindow](../../client/src/components/chat/ChatWindow.tsx#L188-L218) and [ChatInput](../../client/src/components/chat/ChatInput.tsx#L61-L107). |
| Knowledge route and document components | The knowledge list presents document rows and scope/filter interactions, while the drawer provides document detail/edit framing. See [document list](../../client/src/pages/knowledge/KnowledgeDocumentList.tsx#L56-L90) and [document drawer](../../client/src/components/knowledge/KnowledgeDocumentDrawer.tsx#L100-L126). |
| AI configuration components | Provider cards and profile dialogs support reusable connection/profile management; the existing [profile selector](../../client/src/components/ai/ModelProfileSelector.tsx#L80-L129) is a shared selection/control point. |

These are browser-side responsibilities and change locations. API services own data exchange, and backend services own execution, provider credentials, and durable state.

## Change guidance

When changing an agent or chat screen, trace its route, hook/service request, query-cache or projection behavior, and focused component or hook tests. If the change alters permissions, run admission, durable state, cancellation, or model resolution, update the backend contract owner as well; a UI state or successful request does not establish those guarantees.
