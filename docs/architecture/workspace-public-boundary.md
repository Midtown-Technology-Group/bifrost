# Workspace public platform boundary

The workspace depends on the BiFrost public contract, not the backend implementation.

Workspace-authored workflows, tools, modules, integrations, and operator scripts
use the public bifrost SDK for platform operations. They must not import
src.*, SQLAlchemy, backend models/sessions, or platform FastAPI services.
Normal third-party libraries remain supported. Install the standalone SDK;
never insert a sibling platform checkout into Python's import path.

## Historical workflow migration

Existing workflow IDs, names, and signatures remain unchanged.

| Workflow | Removed internal dependencies | Public replacement |
| --- | --- | --- |
| inspect_ninjaone_oauth_tokens | Database session, SQLAlchemy select, OAuthProvider/OAuthToken ORM | bifrost.oauth_admin.inspect("NinjaOne", scope="global") |
| repair_ninjaone_oauth | Session, secret decryption, removed OAuthConnectionRepository, OAuthProviderClient, URL resolution | bifrost.oauth_admin.recover(connection_name, refresh_token=…, scope="global") |
| repair_ninjaone_oauth_copy_refresh_token | Session and direct SQL across providers/tokens | bifrost.oauth_admin.reconcile("NinjaOne", scope="global") |
| repair_ninjaone_oauth_no_scope_exchange | Session, secret decryption, removed repository, URL resolution, inline vendor HTTP | bifrost.oauth_admin.recover(connection_name, code=…, redirect_uri=…, scope="global") |
| redact_execution_sensitive_fields | Session, SQLAlchemy update, Execution ORM | bifrost.executions.get(id) for target scope, then redact_sensitive_fields(id, scope=…) |

## Security and behavior

These are privileged administrator actions. Their REST endpoints authenticate
the caller, require a platform administrator (including the original delegated
workflow caller), and require an exact organization UUID or global. A
delegated platform administrator can explicitly select a target scope even when
the engine assigns the caller’s organization to a global workflow; non-admin
delegation grants no administrative access. Audit records use the
original delegated identity and target organization; mutations fail if the
audit cannot be persisted in the same transaction.

Diagnostics and reconciliation now select one connection and token scope.
They no longer enumerate all tenants or copy credentials across providers or
organizations. Reconciliation chooses the freshest complete token pair by
expiry then creation time, updates only that connection's scoped rows, and
returns counts. No eligible source means zero updates. Repeating the action
preserves token state. An org-scoped token on a global provider does not change
the global provider's status.

Recovery refreshes a stored token when no plaintext input is supplied. The
existing refresh-token and authorization-code signatures remain available for
operator recovery. Encryption, decryption, exchange and persistence stay on the server. Current platform token reads
query the database directly; there is no OAuth token-cache invalidator. Code exchange omits scope and requires a
refresh token in the response; a configured redirect URI must match. Rotating
credentials and single-use codes are not automatically retried. Provider
failures and validation errors do not echo submitted credentials. Successful
responses contain only IDs, expiry, scope, and token-presence metadata.

Prefer direct SDK/API administration with stored tokens. Existing workflow
parameters are still execution inputs, so supplying a plaintext refresh token
or code to a workflow can place it in execution history. Sanitize that terminal
execution afterward; this migration does not redesign input persistence.
SDK recovery registers credential inputs with the existing execution secret
scrubber for logs and outputs.

Execution sanitation writes fixed redaction markers to parameters, result,
variables, and execution context, and clears the error message. It never
accepts arbitrary fields. Missing or wrong-scope executions return 404;
non-terminal executions return 409; repeated calls on terminal executions
succeed. IDs, workflow identity, status, timing, and the independent audit log
are preserved. Logs and other related records are outside this specific
payload sanitation operation.

## Delivery and audit

Deploy the platform endpoints and distribute their matching standalone SDK
before releasing these wrappers. Older SDKs have no oauth_admin namespace.

The quality gate retains an explicitly empty
PLATFORM_INTERNAL_IMPORT_ALLOWLIST, with a regression test forbidding
additions. Its AST boundary scan includes ordinary authored roots, Solution
Python sources, google_ops_worker, and top-level Python files; it excludes vendor/generated
content and existing compatibility exclusions. Negative fixtures in quality
gate tests intentionally contain forbidden imports as text.

Archived plans under docs/plans describe historical backend implementations
and their src/SQLAlchemy examples are platform-only. They are not workspace
authoring instructions. No current workspace guide recommends direct backend
imports.

Final boundary audit: 2,095 authored Python files, zero internal-import findings,
an empty allowlist, and no remaining direct-import matches in authored roots.
Verification evidence and coordinated PR links are recorded in the PRs.
This is source/API preparation; it does not change any live OAuth credentials
or execution history, nor begin a backend rewrite.
