---
type: security-architecture
title: Security, Audit, and Sensitive Data Boundaries
description: Summarizes request limits, secret sanitization, privileged audit access, and repository vulnerability handling policy.
tags: [security, audit, secrets, request-limits, tenancy]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T02:14:28.318Z
sources:
  - id: openwiki-source-e216bb032f489583c4ed4f95
    resource: repo://api/src/core/execution_variable_safety.py
  - id: openwiki-source-26eb8db4c16c76c8c8f8df86
    resource: repo://api/src/core/request_body_limit.py
  - id: openwiki-source-8714a15bd042e45313ddf068
    resource: repo://api/src/core/secret_string.py
  - id: openwiki-source-de1257550254e1c4095fea5a
    resource: repo://api/src/main.py
  - id: openwiki-source-84505822f7e6955a28f395eb
    resource: repo://api/src/repositories/audit_logs.py
  - id: openwiki-source-af8a59be3a748f0a4a44fcb4
    resource: repo://api/src/routers/audit.py
  - id: openwiki-source-1f9c9aa30c0ecc3fdaac75c4
    resource: repo://api/src/services/execution/engine.py
  - id: openwiki-source-3a44815832a872f4778f822b
    resource: repo://SECURITY.md
generated: { by: "bifrost-atlas", at: "2026-10-18T02:14:28.318Z" }
---

# Security, Audit, and Sensitive Data Boundaries

Security controls are distributed across the API boundary, execution persistence, privileged operational routes, and repository response process. The controls described here establish code and policy behavior; they do not certify a running deployment. See [identity and authorization](../architecture/identity-and-authorization.md), [OAuth and external MCP](../integrations/oauth-and-external-mcp.md), and [observability and operations](observability-and-operations.md).

## Request handling

The ASGI `RouteBodyLimitMiddleware` applies only to explicitly selected HTTP method/path pairs. It rejects an oversized declared `Content-Length` before parsing, and counts streamed request chunks as they arrive to reject chunked or under-declared bodies. Rejection returns HTTP 413 with a route-specific or default message. Current middleware registrations cap solution archive preview, external-worker identity, and Codex credential-cache import bodies before multipart/JSON parser work (`api/src/core/request_body_limit.py`, `api/src/main.py`).

## Secrets and execution persistence

`SecretString` masks values in `repr`, `str`, and ordinary logging formatting while remaining a string value. Because JSON encoding of a string subclass can bypass those display methods, serialization and persistence paths must use recursive redaction. `sanitize_execution_variables` replaces `SecretString` values, values under sensitive keys, and active-path cycles; execution output handling also scrubs collected secret values from results, variables, logs, errors, and public context before persistence (`api/src/core/secret_string.py`, `api/src/core/execution_variable_safety.py`, `api/src/services/execution/engine.py`).

Body caps, display masking, and execution redaction are separate defenses. They do not make it safe to log request bodies or place secrets in telemetry attributes; keep sensitive payloads out of logs and traces (`api/src/core/log_safety.py`, `docs/runbooks/otel-platform-observability.md`).

## Audit access and retrieval

The audit API is read-only and guarded by `CurrentSuperuser`. It filters by action prefix, resource type, outcome, actor, execution, time range, and free-text search, and returns newest-first records with bounded (maximum 500) keyset-paginated pages. The response decorates rows with actor and organization names; audit records can include IP address, user agent, and event details, so access to the endpoint is itself a sensitive administrative capability (`api/src/routers/audit.py`, `api/src/repositories/audit_logs.py`).

## Vulnerability handling and limits

The repository security policy says security reports should use GitHub private vulnerability reporting or another private maintainer channel, not a public issue. It describes vulnerability acknowledgement and triage targets and lists supply-chain controls such as dependency updates, CodeQL, secret scanning/push protection, and Scorecard (`SECURITY.md`). These are repository policy declarations; this page does not query their current service status or inspect private incident records.

The code references and public security docs intentionally do not expose incident identifiers or live infrastructure details. For a suspected vulnerability, follow the private process in the policy and avoid publishing exploit details, credentials, or full proof-of-concept data.
