---
type: runtime-guide
title: Events, Webhooks, and Scheduling
description: Explains event sources and subscriptions, public webhook validation, topic emission, delivery tracking, and cron-triggered execution.
tags: [events, webhooks, schedules, triggers, runtime]
sources:
  - id: openwiki-source-69fc36b69346912f422a945e
    resource: repo://api/src/jobs/schedulers/cron_scheduler.py
  - id: openwiki-source-2d83c16595d181eab8b76cee
    resource: repo://api/src/jobs/schedulers/webhook_renewal.py
  - id: openwiki-source-521891cb02d8e6a2b1faaaa5
    resource: repo://api/src/routers/events.py
  - id: openwiki-source-769e0c3040b6c17d616752d2
    resource: repo://api/src/routers/hooks.py
  - id: openwiki-source-ddc89780e79c801c657d6d4c
    resource: repo://api/src/routers/schedules.py
  - id: openwiki-source-1bd3d013de2efef2651a9d95
    resource: repo://api/src/scheduler/main.py
  - id: openwiki-source-e340e9a52ebab7d45c33903d
    resource: repo://api/src/services/events/processor.py
  - id: openwiki-source-c2ab3311218bac789139bfbe
    resource: repo://api/src/services/events/validation.py
  - id: openwiki-source-ab92b703c21844e226d53d96
    resource: repo://api/src/services/webhooks/lifecycle.py
  - id: openwiki-source-d34196fa467904c59291be46
    resource: repo://api/tests/unit/jobs/schedulers/test_webhook_renewal.py
  - id: openwiki-source-9cbd41e2be3d13003eb314d8
    resource: repo://api/tests/unit/routers/test_events_webhook_creation.py
  - id: openwiki-source-348a04725dd62e51f9e9b7ec
    resource: repo://api/tests/unit/services/test_webhook_lifecycle.py
generated: { by: "bifrost-atlas", at: "2026-10-18T06:28:44.263Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T06:28:44.263Z
---

# Events, Webhooks, and Scheduling

The event system normalizes webhook, topic, and schedule triggers into persisted events and per-subscription deliveries. A delivery can target a workflow or an agent; workflow deliveries enter the shared execution queue, while agent deliveries enter the autonomous run queue. The event records and delivery records preserve trigger identity and outcome independently of execution. See [execution and workflows](execution-and-workflows.md), [durable jobs and schedulers](jobs-and-schedulers.md), and [OAuth and external MCP](../integrations/oauth-and-external-mcp.md).

## Sources, topics, and subscriptions

Event sources carry a type and scope, with type-specific webhook or schedule configuration. Subscriptions identify a target, optional event criteria and input mapping, and active state. Topic names are constrained to lowercase letters, digits, underscores and dots, must include a dot, and are limited to 100 characters. The curated registry supplies platform topic descriptions/examples, while custom topic sources can be created through supported interfaces (`api/src/services/events/validation.py`, `api/src/services/events/registry.py`, `docs/events/topics.md`).

When a topic event is emitted, active subscriptions on the matching source are evaluated and delivery records capture success, failure, skipped/not-matched, and evaluation-error counts. Input mappings build workflow parameters; the full event is also carried in the reserved `_event` context. Organization scope is derived from the workflow or event/source context so event execution retains its intended boundary (`api/src/services/events/processor.py`).

## Webhook ingress and validation

Webhook callbacks use the source UUID as an unguessable path and are publicly callable without user authentication. The handler resolves the source and creates both a pre-auth limiter scoped by source and client IP and a shared source quota only when `rate_limit_enabled` is true and `rate_limit_per_minute` is non-null. It then invokes the adapter to validate the provider request; after adapter acceptance it charges the shared source quota before persisting or queueing deliveries. If either setting condition is false, neither limiter applies. Adapter rejection, unknown source, configured rate limiting, and internal processing errors have distinct HTTP outcomes; internal errors are not returned verbatim ([ingress limiter construction and quota boundary](../../api/src/routers/hooks.py#L153-L214)).

Adapters own provider-specific handshake and signature/client-state validation. A missing adapter rejects processing. The event processor turns validated requests into events and tracked deliveries; retrying a failed delivery resets its attempt state and queues it again, with queue failures recorded back as failed (`api/src/services/events/processor.py`).

## Provider-managed subscription lifecycle

A provider-managed webhook has an external registration as well as a local event source. Creation commits the provisional local source before calling the adapter's `subscribe` method so a provider's synchronous callback-validation request can resolve its callback URL. If provider setup fails, the router deletes the provisional source and returns a gateway error; the transaction-order tests pin both the commit-before-subscribe order and cleanup path ([creation lifecycle](../../api/src/routers/events.py#L406-L541), [creation regression tests](../../api/tests/unit/routers/test_events_webhook_creation.py#L44-L103)). The adapter contract and provider-specific auth details are covered in [provider webhooks](provider-webhooks.md).

Resubscribe replaces the external registration while retaining the same Bifrost source. It first unsubscribes the old registration, commits cleared `external_id`, state, and expiry, then calls `subscribe` and persists the replacement; a failed replacement leaves a visible local source with its error message for recovery ([replacement lifecycle](../../api/src/services/webhooks/lifecycle.py#L55-L105), [lifecycle tests](../../api/tests/unit/services/test_webhook_lifecycle.py#L34-L106)). Deletion has the opposite persistence constraint: provider unsubscribe must succeed before the local source can be deleted. On cleanup failure, the router records the error and keeps the source retryable ([delete boundary](../../api/src/routers/events.py#L797-L864)).

A separate scheduler trigger runs provider renewal every six hours. The renewal sweep selects registrations expiring within 48 hours, skips adapters without renewal support, calls `renew`, and falls back to creating a replacement when the adapter returns no renewal result. It persists refreshed expiry/state or a visible source error ([scheduler trigger](../../api/src/scheduler/main.py#L443-L460), [renewal selection and adapter fallback](../../api/src/jobs/schedulers/webhook_renewal.py#L79-L155), [persisted renewal outcomes](../../api/src/jobs/schedulers/webhook_renewal.py#L227-L273)). This lifecycle is separate from cron schedule-source polling below.

## Scheduled sources

User-defined schedule sources store a cron expression, timezone, enabled flag, and overlap policy. The scheduler service is replicated, but its PostgreSQL lease elects the single trigger leader. Its scheduled task polls active schedule sources, validates cron expressions and time zones, and detects a recent cron occurrence within a one-minute polling window. Runs create events and queue subscription deliveries (`api/src/jobs/schedulers/cron_scheduler.py`, `api/src/scheduler/main.py`).

The schedule validation endpoint is platform-superuser restricted and returns validity, human-readable form, next-run preview, interval, and a warning when computed runs are less than five minutes apart. The backend scheduler logs and skips invalid cron/timezone sources rather than claiming successful delivery (`api/src/routers/schedules.py`, `api/src/jobs/schedulers/cron_scheduler.py`).

## Operational interpretation

An accepted webhook means adapter validation and event admission succeeded; it does not mean each downstream workflow or agent completed. Inspect event delivery status, linked execution/run, and retry history to diagnose downstream outcomes. Scheduler health and task-run leadership are separate operational signals; consult [observability and operations](../operations/observability-and-operations.md) for their diagnostic view. This page documents source behavior only and does not establish current provider callbacks or scheduler health.
