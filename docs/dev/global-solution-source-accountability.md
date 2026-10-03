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

## Immutable source export readback

An immutable Solution export reads the active deployment's complete source and
resource closure and verifies each content hash against that deployment. Empty
Python package initializers are valid source members and retain their empty-file
hash. Transport reads remain bounded by the archive budget. If an object store
rejects the prefix range with HTTP 416, the reader accepts empty bytes only after
metadata for the exact same object key proves an integer size of zero. A nonzero
or unknown size, failed metadata read, other download error or changed hash
fails closed. The reader never substitutes Root bytes or issues an unbounded
download to handle the range failure.

Export source verification complements independent matching worker-pin and
registration readback. A shareable export does not certify customer table data,
connection secrets, retention copies or full disaster recovery.
