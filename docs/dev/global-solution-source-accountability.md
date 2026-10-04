# Global Solution source accountability

A Workspace source record belongs to the organization that declared the reviewed
Git change. That accountability scope can differ from a global Solution's
runtime scope. Migration must preserve existing global workflow scope.

The platform-superuser disposition endpoint accepts
`reviewed_global_solution_ids` in supersession evidence. Supply only the exact
global Solution identities included in the production review. The service
requires each selected deployment and its locked Solution to have global scope,
and rejects a missing or unrelated global identity, or one without a selected
deployment anchor. A deployment from
another organization remains invalid, even if its identity is listed here.
The record lookup stays scoped to the accountable organization.

Every anchor must be a reviewed active deployment newer than the source record,
with the current Solution pointer and valid canonical runtime closure. Every
old source path needs fresh operator readback, a current Git hash, and the exact
runtime path/hash, or verified removal. Immutable resource paths use the
closure's separate resource hash map. Removed paths must be absent from Root,
its index, Live and every active Solution's source and resource maps.

Supported anchors include Live handoff, Source revision, populated legacy
workflow adoption and reviewed workflow revision deployments. Adoption alone
does not dispose a historical record: the same complete path evidence, later
activation, explicit Global review, scope and current-pointer checks apply.

Legacy and reviewed signatures use one static parameter compiler, including
unambiguous module-level literal scalar defaults. Adoption preserves legacy
parameter rows and rejects changed defaults, types, required arguments and
removals. A reviewed adoption recipe may add optional arguments only when the
legacy list representation is independently sealed and every existing property
and admission rule is unchanged. Complete modern registry schemas still require
equality. The new runtime signature is bound into the immutable artifact and pin;
normal workflow revision can subsequently project the complete registry schema.
Body-only Source delivery also
rejects a changed constant default; use the reviewed workflow revision path
for a supported parameter contract change. Carried code is never imported or
executed to resolve defaults.

This remains an audited operator attestation, with the review included in the
immutable completion evidence digest. It does not imply automatic completion
of a Solution deploy obligation or independently authenticate the operator's
external readback artifact. Preserve the producer, source object, registration,
runtime and obligation readbacks before submitting a disposition. The explicit
global review list changes accountability validation only; it does not change
workflow ownership, runtime permissions or tenant scope.

A successful new Solution deployment does not itself resolve older loose-source
records. Review all declared paths in those records, including pending current
main changes and new workflows, before disposing them. Existing accepted
Workspace execution pins remain valid across Solution ownership changes and
Live retirement. Retain immutable release objects while accepted work or
retained evidence references them.

Retirement checks unresolved obligations against every path in the full Live
artifact, including its dependency closure. A complete, valid, nonempty path
map that is disjoint from that artifact does not veto retirement. The retirement
receipt records its ID, path-map digest and the exact artifact boundary digest;
the obligation itself remains unresolved and unchanged. Overlapping, malformed,
empty or deletion-bearing maps still block. The bounded inventory is refreshed
under a table fence through the retirement commit so concurrent declarations
cannot escape this check. This exclusion does not prove delivery or grant a
completion disposition.


## Read-only retirement inventory

`GET /api/workspace-promotions/live/retirement-inventory` is an additive,
platform-admin-only contract. Use the Live release owner's organization context.
It reads the same Root registration predicate as mutation-time retirement,
including inactive rows, uncaptured normalized governed paths and effective
registration UUIDs whose current path differs. More than 1,000 matching rows
fails closed rather than claiming a complete truncated census. Obligation counts
come from the Live owner's journal. Another organization context is rejected for
retirement and idempotent retirement readback so it cannot hide owner debt.

This GET creates no candidates, locks, audit events, activations or dispositions.
It is available for inspection without enabling the retirement mutation flag.
Its response is an observed inventory, not a retirement authorization or proof
of indirect/external callers, source bytes or accepted work. Refresh all those
gates and the exact identity CAS before any separately authorized retirement.
Existing SDK raw HTTP helpers can read the contract; no entity mutation command,
manifest field or CLI contract version is changed. The existing retirement
request and response are preserved. Generate client types from the actual CI API
schema readback, not from a handwritten schema or sibling checkout.
