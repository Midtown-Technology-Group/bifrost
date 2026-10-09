# Proposed common execution-profile codec

Independent Go wire prototype against PR #1132 candidate
`35daf020d5e1286f82bdfed6bb3fc537bf3d78f6`. Exact shared document/corpus
hashes are retained in `testdata/provenance.json`. This is not a profile freeze,
runtime launch adapter, authorization verifier, lifecycle state machine or
durable receipt implementation.

Only this package knows the proposed messages. Authored Go and the capability
SDK are unchanged. It imports no Rust implementation or Python oracle. The
private schema interpreter supports only the pinned shared document vocabulary,
loads embedded schemas without network/hooks, and fails on unsupported keywords.
It is not a general schema API for application authors.

`Read`/`Write` handle framed streams, including partial/interrupted IO. `Decode`
validates envelope/body structure and within-frame correlations, preserving exact
payload bytes for receipt digesting. Direction, cross-frame legality, trusted
custody, actual provision, current fences and owner transactions remain separate
unimplemented responsibilities. Parsing a Start grants no execution permission.

The new corpus has 94 raw wire specimens. Additional tests exercise stream IO,
exact raw-byte digest preservation and pinned schema/corpus bytes. Existing P0
source and its 99 wire/7 binary/35 session vectors remain unchanged. Supported
CI runs all existing and new Go tests with the isolated build recipe; physical
host runtime tests are forbidden. The package does not run the application
through the full profile or prove cross-language execution-profile interchange.

Structural/session conformance now also has an independent Go specification
model in `conformance_test.go`. It is compiled only into tests. Its injected
commit/grant/delivery/release/cleanup events are fixture assumptions: no SDK
credential, workload callback, SQL, OS launch or durable store exists. A successful
model release is never runtime authority. It checks the published 68-case
transcript, both directional frontiers, rejection rollback and raw receipt
preimages; its status remains unproved until supported CI passes the exact source.
The same pinned structural documents are consumed by the independent Python and
Rust components, distinct from their wire/correlation and session layers.
