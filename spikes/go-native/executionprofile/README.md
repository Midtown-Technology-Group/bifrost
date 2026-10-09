# Proposed common execution-profile codec

Independent Go wire prototype against PR #1132 candidate
`370230fb4d42f481c9fb94efc8cc9ca7f1185e8c`. Exact shared document/corpus
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
