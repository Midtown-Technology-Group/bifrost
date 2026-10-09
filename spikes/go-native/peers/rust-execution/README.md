# Independent proposed-profile Rust wire consumer

Trusted first-party experiment, not a Rust owner or a Go runtime dependency.
The author/Go SDK imports none of this package. It reads the same three published
schema copies, independently validates the restricted vocabulary and framed
stream, and emits/consumes the common JSON messages. Unsupported schema vocabulary
or pattern fails closed; it is not a public user-schema engine.

The unchanged P0 strict lexical visitor is retained with source attribution.
No P0 typed bodies, Rust lifecycle enums, SQL, supervisor or oracle state enter
this codec. Registry dependency records/checksums come from the pinned existing
Rust reference; the selected closure still needs exact cargo --locked acceptance.
No Cargo command executes on workflow admission or in the Go workload.

Tests cover94 named published specimens, partial/interrupted IO, concatenation,
zero progress, preallocation limits, validation-before-output and immutable raw
receipt bytes. The hosted helper uses the W0 pinned Rust image/toolchain with its
existing rustfmt/clippy component recipe. First-party dependencies are fetched in
a separate networked step; fmt/clippy/test/build run with network disabled,
read-only source, clean environment and bounded CPU/memory/processes. No tenant
application is compiled or run in this path, no SDK/DB/signing credentials exist.

Interchange must feed real Rust bytes to both Go and independent Python readers
and their bytes back to Rust. Exact raw Result receipt hashes and a shape-valid
semantic-drift negative must pass. Until supported CI verifies the exact source,
these are expectations. This package implements no session, initialization
custody, private provision, lifecycle or durable projection. It cannot release
#1011/#1132 authority gates or approve the owner integration proposal.

`session_tests.rs` is an independent test-only transcript model consuming the
published 68 cases and their named setup fixtures. Every step checks rejection
and the exact snapshot; rejection must preserve the entire private model state.
Commit, grant, delivery, release and cleanup are injected assumptions with no
credential, process or durable effects. The `raw_value` development feature of
the already pinned serde_json package retains fixture field order for its declared
receipt preimage; no new registry package is added. Raw received Result bytes are
hashed unchanged. This test model is absent from the normal executable/library
and is not an owner/session registry or a supervisor implementation. Rust session
conformance remains unproved until supported CI verifies this exact candidate.
