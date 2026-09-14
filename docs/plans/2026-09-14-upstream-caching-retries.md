# Upstream caching and SDK retries

Integrate four upstream commits through
`57be30ac721739e302be7f0ba42d18c4defca501` into fork base
`d24f399bea972b505b63e1f0e40265bbfe4c9c1f` with a two-parent merge.
The batch contains SDK transport retry support and synchronous coverage,
Anthropic prompt caching, and persisted per-endpoint cache capability.

## Fork reconciliation

The SDK conflict combines upstream `retry_transient` with the fork's existing
`retry_safe` callers. HTTP reads gain upstream transport retries. Read-only
POSTs retain retries for transient 5xx responses and connection establishment
timeouts, with one shared six-attempt budget. Ordinary POST, PATCH, PUT, and
DELETE calls do not gain transport replay; callers must explicitly declare
`retry_transient=True` to retry uncertain failures. Existing idempotent-method
5xx behavior remains intact. Both synchronous and asynchronous write tests
exercise that boundary. Internal helper tests use the new upstream names.

Migration `20260914_merge_mtg_cache` joins the fork's existing SDK migration
head and upstream's cache-capability migration. Neither parent history changes.
API types regenerated from the merged running service reproduce the imported
type additions without removing fork contracts. The MTG-only deployment
boundary remains intact.

## Verification

The focused run passed 201 tests across SDK retries and consumers, cache
behavior, model configuration, agent runtime, CLI contracts, DTO parity, and
the live AI-model settings API. Alembic reports one head,
`20260914_merge_mtg_cache`. The settings API checks include the new nullable
capability in connection and profile responses.

The clean-commit `./test.sh pre-pr` gate and PR/merge-queue checks remain
required before landing. The PR records their results for the exact candidate.
Source integration does not itself deploy these images to production.
