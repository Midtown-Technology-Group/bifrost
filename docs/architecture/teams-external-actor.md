# Authenticated external actors for agent events

## Current boundary (2026-09-24, `ef055ab1c`)

`MicrosoftBotFrameworkAdapter` verifies a Bot Framework JWT against Microsoft's
OpenID keys, issuer, audience, Teams key endorsement, and signed service URL.
It then copies the Teams tenant, sender, activity and conversation metadata into
an ordinary `Deliver`. The JWT authenticates the Bot Framework channel request;
it does not itself supply a Bifrost user or organization grant. The Teams actor
key should be `from.aadObjectId`, scoped by `channelData.tenant.id`, rather than
the display name, email, or channel-local `from.id`.

`IntegrationMapping` already relates an integration's external entity ID to an
organization. The central Teams bot can use one configured Teams Bot integration
and its existing mapping rows; the mapping table does not require entity IDs to
be unique across organizations, so resolution must reject zero or multiple
matches, a deleted integration, a null organization, and an inactive organization.
No Teams Bot integration definition or mapping appears in this platform checkout
or the checked workspace source; deployment configuration remains necessary.

The event processor persists `Event`, creates `EventDelivery`, then queues an
agent with `enqueue_agent_run`. Ordinary webhook/event runs intentionally have
`caller_user_id=None`: arbitrary webhook data is not a human identity claim.
Chat passes a full `UserPrincipal` into the same queue. The worker forwards
`caller_user_id` to the existing workflow-tool and MCP dispatch paths. MCP token
resolution already selects delegated user credentials, explicitly allowed
service fallback, or reauthorization. Agent workflow and MCP grants constrained
the advertised tool list, but direct model tool-name dispatch could bypass
those grants. Dispatch must recheck the grant; ingress must not add grants.

`Event` and `AgentRun.event_delivery_id` give an event-to-run link. Agent steps,
workflow execution IDs, MCP resolution-path audit, and `AuditLog` cover parts of
the remaining chain. There is no generic agent action approval primitive in
the current agent tool dispatch path. Existing confirmation flows address other
features and cannot gate consequential agent tools.

## Chosen seam and schema

An adapter may opt into a typed `AuthenticatedExternalActor` result **only after**
its provider-specific authentication succeeds. The event processor rejects actor
claims from adapters without this explicit contract. Generic webhooks continue
as autonomous runs. The processor resolves the actor in two separate steps:

1. Provider scope to exactly one active organization, using the configured
   integration's existing `IntegrationMapping` rows. The source's configured
   organization, if present, must agree.
2. `(provider, external_scope_id, external_user_id)` to one active Bifrost user.
   A new `external_identities` table is needed because
   `UserOAuthAccount` is SSO-specific and lacks an explicit tenant scope. No
   email or name matching and no guest or system fallback. A user in the
   mapped customer org is allowed directly. A provider-org staff user requires
   `authorized_organization_id` on that exact identity link, matching the
   tenant's mapped customer org. This is an explicit, revocable per-actor
   customer grant; other cross-org links fail closed.
   A provider-owned central bot source may route to the mapped customer; a
   customer-owned source may only route within its own organization. The
   authenticated actor's integration ID comes from the webhook source's
   configured integration, and the processor checks that binding before
   resolution.

`Event` stores non-secret actor provenance and the resolved identity ID; its
organization is stamped from the tenant mapping. The delivery queues a run only
after revalidating the mapping and user, then passes the existing Chat caller
fields. The run's existing event-delivery reference and event provenance form
the durable ingress chain. The worker revalidates that link and the provider
grant before planning tools and again at dispatch. The actor field is data only
after persistence; code never treats a JSON value in an arbitrary webhook
payload as proof. Historical
event and approval IDs stay in their rows when a live identity, user, agent, or
workflow is deleted, so the audit chain remains queryable.

For consequential workflow tools, use one workflow-level approval policy and a
durable proposal. The shared agent workflow-tool boundary checks that policy
for Chat, Teams and future surfaces. Approval releases that exact proposal into
the existing execution path; denial or no approval never mutates. Direct event
subscriptions cannot run an approval-required workflow. Read-only tools
continue through existing grants. An MCP server's remote side effects cannot
be inferred safely from a tool name; a deployment must expose only approved
read capabilities to this agent and route remediation through a tagged
workflow. Per-MCP-tool approval remains a separate platform gap.

The provider grant scopes this external run to Customer A without changing
Jane's home organization or granting general customer access to her Chat
sessions. The Endpoint agent and MCP connection must themselves be Customer
A-scoped and explicitly granted. Delegating this external run to a child agent
is denied until a scoped child grant can be carried and revalidated.
Provider administrator status is stripped from the customer-scoped principal;
the grant never supplies platform-wide authority.

## Security invariants

Generic adapters cannot assert an actor; Teams JWT failure never reaches the
resolver. Tenant and sender keys come only from the authenticated adapter
result. Missing, ambiguous, inactive, cross-org, or stale mappings fail closed.
User and agent permissions are checked independently. The queue receives a
human caller only after full resolution; otherwise webhook runs retain the
autonomous invariant. Provenance stores IDs, not bearer tokens.
