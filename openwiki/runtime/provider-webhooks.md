---
type: integration-lifecycle
title: Provider-Managed Webhook Subscriptions
description: How webhook adapters register, renew, replace, and remove external subscriptions, resolve organization-scoped OAuth, and persist provider state independently of inbound event verification.
tags: [webhooks, events, subscriptions, oauth, integrations]
sources:
  - id: openwiki-source-663f1951f4c0e930e83308d5
    resource: repo://api/src/jobs/platform/system_maintenance.py
  - id: openwiki-source-2d83c16595d181eab8b76cee
    resource: repo://api/src/jobs/schedulers/webhook_renewal.py
  - id: openwiki-source-76977824a3bea7944d4e23fc
    resource: repo://api/src/models/orm/events.py
  - id: openwiki-source-521891cb02d8e6a2b1faaaa5
    resource: repo://api/src/routers/events.py
  - id: openwiki-source-1bd3d013de2efef2651a9d95
    resource: repo://api/src/scheduler/main.py
  - id: openwiki-source-1d506e381e28f88063e2658a
    resource: repo://api/src/services/webhooks/adapters/local_fixture.py
  - id: openwiki-source-f190d78edc5127f7d5abec4f
    resource: repo://api/src/services/webhooks/adapters/microsoft_graph.py
  - id: openwiki-source-81f1d829d853cb7541fa6155
    resource: repo://api/src/services/webhooks/auth.py
  - id: openwiki-source-ab92b703c21844e226d53d96
    resource: repo://api/src/services/webhooks/lifecycle.py
  - id: openwiki-source-091b1019c19cc3370caccd28
    resource: repo://api/src/services/webhooks/protocol.py
  - id: openwiki-source-83564e3ec9e74c29309ff264
    resource: repo://api/src/services/webhooks/registry.py
  - id: openwiki-source-d34196fa467904c59291be46
    resource: repo://api/tests/unit/jobs/schedulers/test_webhook_renewal.py
  - id: openwiki-source-348a04725dd62e51f9e9b7ec
    resource: repo://api/tests/unit/services/test_webhook_lifecycle.py
generated: { by: "bifrost-atlas", at: "2026-10-18T06:28:44.263Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T06:28:44.263Z
---

# Provider-Managed Webhook Subscriptions

A webhook event source has a local Bifrost identity and may also have an external provider registration. The adapter contract owns provider-specific subscription operations and request interpretation; source persistence and scheduled renewal are coordinated by the platform. Subscription creation or renewal does not itself prove that a later callback is authentic. The inbound adapter must return a `ValidationResponse`, `Deliver`, or `Rejected` result according to its own verification rules. See [events and schedules](events-and-schedules.md), [OAuth and external MCP](../integrations/oauth-and-external-mcp.md), and [security and audit](../operations/security-and-audit.md) for the adjacent contracts.

## Adapter contract and selection

`WebhookAdapter` separates `subscribe`, `unsubscribe`, optional `renew`, and `handle_request`. Subscribe results carry an optional external subscription ID, adapter-owned state, and expiry; renewal results carry a new expiry and optional state update. An adapter that does not implement renewal returns `None`, and one with no `renewal_interval` is excluded from scheduled renewal. `handle_request` returns one of the explicit validation, delivery, or rejection outcomes, so provider challenge handling and event delivery are distinct from subscription management. ([`protocol.py`](../../api/src/services/webhooks/protocol.py#L76-L106), [`protocol.py`](../../api/src/services/webhooks/protocol.py#L217-L230), [`protocol.py`](../../api/src/services/webhooks/protocol.py#L234-L276), [`protocol.py`](../../api/src/services/webhooks/protocol.py#L286-L359))

The registry exposes built-in adapters and caches instances. Generic webhooks need no integration and have no external registration or renewal. Microsoft Graph requires an organization-scoped Microsoft integration and declares a 24-hour renewal interval. A local fixture adapter is registered only outside production for development/test exercises. Custom adapter discovery from workspace files is documented as a future seam but is not implemented in this repository version; registry lookup currently supports its registered built-ins and local fixture. ([`registry.py`](../../api/src/services/webhooks/registry.py#L22-L57), [`registry.py`](../../api/src/services/webhooks/registry.py#L110-L160), [`generic.py`](../../api/src/services/webhooks/adapters/generic.py#L22-L40), [`microsoft_graph.py`](../../api/src/services/webhooks/adapters/microsoft_graph.py#L25-L54))

## Organization-scoped authentication

For an adapter that requires an integration, the lifecycle resolves credentials against the event source's organization. The integration must exist, be active, have an OAuth provider and an effective tenant; delegated OAuth also needs a token connected for that organization. A still-fresh stored token is decrypted for use; otherwise the OAuth refresh context is used to request a fresh token. The resulting `WebhookIntegrationAuth` carries the integration, organization, entity/tenant and access token into the adapter call, while its token is excluded from the dataclass representation. ([`auth.py`](../../api/src/services/webhooks/auth.py#L33-L83), [`auth.py`](../../api/src/services/webhooks/auth.py#L85-L114), [`protocol.py`](../../api/src/services/webhooks/protocol.py#L109-L120), [`lifecycle.py`](../../api/src/services/webhooks/lifecycle.py#L17-L33))

## Create, replace, and delete ordering

Creating a webhook source first resolves the adapter and any required integration, then writes the local source and webhook configuration. It commits that provisional record before calling `subscribe`, allowing providers such as Graph to synchronously call the callback URL and resolve the source during validation. On success, the source stores the adapter's `external_id`, state and expiry. If provider registration fails, the create path deletes the provisional source and returns a gateway error rather than leaving a partially-created source behind. ([`events.py`](../../api/src/routers/events.py#L461-L512), [`events.py`](../../api/src/routers/events.py#L514-L543))

Resubscription preserves the Bifrost event source but replaces its provider registration in stages: unsubscribe the old ID/state, clear and commit the old registration fields, then subscribe and persist the replacement. If removal fails, the old subscription data remains and the error is recorded. If new registration fails after removal, the local source remains with empty external registration fields and a visible error, making the partial state inspectable and retryable. The route rejects Solution-managed sources because their lifecycle belongs to Solution deployment. ([`lifecycle.py`](../../api/src/services/webhooks/lifecycle.py#L55-L105), [`events.py`](../../api/src/routers/events.py#L740-L794), [`test_webhook_lifecycle.py`](../../api/tests/unit/services/test_webhook_lifecycle.py#L33-L101))

Deleting a source attempts provider unsubscribe before deleting local state and cascaded event/subscription history. If provider cleanup raises, the endpoint records a safe error on the source, returns a gateway error, and retains the local row so an operator can retry instead of losing the only Bifrost record of a possibly orphaned remote registration. Solution-managed sources are also protected from this endpoint. ([`events.py`](../../api/src/routers/events.py#L797-L860), [`lifecycle.py`](../../api/src/services/webhooks/lifecycle.py#L36-L52), [`test_webhook_lifecycle.py`](../../api/tests/unit/services/test_webhook_lifecycle.py#L104-L119))

## Scheduled renewal

The scheduler enqueues webhook renewal as the shared `webhook.renew` PlatformJob, with a single-concurrency maintenance policy and bounded retry policy. The scheduled sweep selects subscriptions expiring within 48 hours, skips adapters without renewal support, and separates database reads, provider HTTP, and result persistence into phases so a DB session is not held while calling the provider. When `renew` returns a result, expiry and adapter state are updated; if renewal returns `None`, the job recreates the registration with `subscribe`. On success a renewal merges returned state into existing state, while recreation replaces state and external ID. Provider, OAuth, and recreation failures are counted and stored on the event source as a visible renewal error. ([`main.py`](../../api/src/scheduler/main.py#L440-L456), [`system_maintenance.py`](../../api/src/jobs/platform/system_maintenance.py#L54-L70), [`system_maintenance.py`](../../api/src/jobs/platform/system_maintenance.py#L157-L172), [`system_maintenance.py`](../../api/src/jobs/platform/system_maintenance.py#L265-L270), [`webhook_renewal.py`](../../api/src/jobs/schedulers/webhook_renewal.py#L26-L81), [`webhook_renewal.py`](../../api/src/jobs/schedulers/webhook_renewal.py#L107-L245))

The recurring scheduler itself is configured to check every six hours; the expiry query uses its own 48-hour threshold, while individual adapter `renewal_interval` values describe adapter renewal cadence/capability metadata. Microsoft Graph currently exposes a 24-hour adapter interval and the development fixture uses one minute to make renewal behavior testable. Those source declarations do not establish a running scheduler or provider health. Focused tests cover renewal success, unsupported adapters, renewal/recreation failure, persisted state merge/replacement, and manual resubscription failure. ([`main.py`](../../api/src/scheduler/main.py#L440-L456), [`webhook_renewal.py`](../../api/src/jobs/schedulers/webhook_renewal.py#L84-L153), [`microsoft_graph.py`](../../api/src/services/webhooks/adapters/microsoft_graph.py#L45-L54), [`local_fixture.py`](../../api/src/services/webhooks/adapters/local_fixture.py#L19-L67), [`test_webhook_renewal.py`](../../api/tests/unit/jobs/schedulers/test_webhook_renewal.py#L88-L181))

## Persistence and operational boundaries

`WebhookSource` stores adapter name, integration binding, configuration, external ID, adapter state, expiry, and rate-limit settings alongside the event-source relationship. The active adapter owns the meaning of its state fields; callers should pass state through its protocol rather than reinterpret provider-specific values. Generic adapter state includes an optional HMAC secret, whereas Graph state includes provider subscription validation material. ([`events.py`](../../api/src/models/orm/events.py#L176-L236), [`generic.py`](../../api/src/services/webhooks/adapters/generic.py#L22-L40), [`microsoft_graph.py`](../../api/src/services/webhooks/adapters/microsoft_graph.py#L220-L275))

The subscriptions described here are registrations with external senders. The callback route is a separate ingress boundary, and each adapter applies its own checks before returning a delivery result. For generic endpoints, HMAC is optional and enforced only when a secret is configured. Microsoft Graph compares each incoming `clientState` with stored adapter state only when `client_state` is truthy; if that value is absent or falsey, this equality check is skipped. The Graph adapter also handles validation challenges. Trace actual callback handling through the adapters and event processor before changing event authenticity or delivery semantics. ([`generic.py`](../../api/src/services/webhooks/adapters/generic.py#L140-L190), [`microsoft_graph.py`](../../api/src/services/webhooks/adapters/microsoft_graph.py#L380-L445), [`events.py`](../../api/src/routers/events.py#L1-L80))
