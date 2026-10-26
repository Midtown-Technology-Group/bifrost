# Anthropic Prompt-Cache Capability Design

## Goal

Keep prompt caching enabled for Anthropic-compatible endpoints that support it,
while allowing an endpoint that explicitly rejects `cache_control` to succeed
without caching and remembering that result for the configured model profile.

## Behavior

Model profiles hold a nullable `anthropic_prompt_cache_supported` value.
`NULL` means untested, `true` means a cached request succeeded, and `false`
means the profile's endpoint and configured model explicitly rejected cache
control. Unknown and supported profiles send cache settings. An explicit
400/422 cache-control rejection is retried once without those settings.
Authentication, rate-limit, transport, timeout, generic validation, and server
failures are never converted into an uncached retry.

Only after the uncached retry succeeds does the runtime update the in-memory
decision and persist it through an isolated best-effort database transaction.
Persistence failure cannot turn a successful model request into a failure.
Changing the provider, endpoint, credential, profile connection, or profile
model resets the stored decision to unknown so the next request probes the new
configuration. Persistence is additionally fenced to the profile's configured
model, connection ID, and resolved connection revision, so an in-flight request
from an older endpoint or credential cannot overwrite that reset.

Per-request model overrides never read or write the profile's stored decision.
They may retry one explicit cache-control rejection without caching, but that
one request cannot change behavior for the configured model or other callers.

## Integration

The adaptive behavior wraps the native Pydantic AI Anthropic model's final
request method, so agent runs and direct completion/streaming calls share one
decision point. The model-profile API exposes the nullable state for diagnosis.

