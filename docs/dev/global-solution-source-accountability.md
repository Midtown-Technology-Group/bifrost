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
parameter rows and rejects changed defaults. Body-only Source delivery also
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
