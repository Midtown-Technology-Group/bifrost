# Independent Go control-profile codec

This package implements only the published C1-P0 BiFrost control wire profile,
using standard-library Go and the language-neutral structural specification.
It imports no Rust crate or Python module. Exact unchanged schema and canonical
vectors are retained in `testdata/` from PR #1015 at
`9f35278f90ba3757318ac9f07895bda9957330de`:

- `contracts/runtime/v1/control.schema.json`
- `core-rs/crates/bifrost-contracts/tests/fixtures/runtime/v1/control-vectors.json`

This is the sole reference-vector copy in this spike. Rust and Python's existing
tests consume the original canonical file at that exact reference. The Go tests
consume all 99 wire vectors and seven framing vectors unchanged, compare exact
static error categories, and roundtrip valid values through one-byte IO. Schema
and corpus updates require explicit source reconciliation.

The codec rejects unknown/missing fields, duplicate keys, invalid UTF-8/lone
surrogates, excessive nesting, unsupported messages, lexical negative zero,
unsafe/float integers, mismatched Start correlation and oversized/truncated
frames. Errors retain no supplied payload bytes. It reports observations only;
validation does not grant Start, adopt identity, launch code or finalize work.

This is not yet G3 acceptance: parent/child session-state validation, real
Prepare/Prepared, staged native artifact custody, Result/log projection and
private credential provisioning are absent. The published P0 artifact requires
`interpreter` and `requirements_lock_sha256`; the adapter must not fake a Go
interpreter or widen that closed schema. A shared artifact contract revision is
needed before native Hello. Full runtime acceptance still depends on the common
architecture gates rather than a Go-specific protocol.
