# Runtime v1: partial control profile (C1-P0)

This package implements only `Hello`, `Start`, `Heartbeat`, `Cancel`, `Stopped`,
the `RuntimeArtifact`/`RuntimeError` structures they need, and a parent-input
session validator. Wire protocol is `bifrost.runtime/v1`; this partial profile's
sole negotiated capability is `control_profile/v1`. The structural contract is
[control.schema.json](control.schema.json). The canonical shared vectors are
[control-vectors.json](../../../core-rs/crates/bifrost-contracts/tests/fixtures/runtime/v1/control-vectors.json).
Rust and Python consume that one file. There is no duplicate fixture copy.

**This is not the complete v1 protocol, a running Python runtime, a process
supervisor, or production integration/authority proof.** Prepare/Prepared,
source/workload/caller/provider fields, Result/log/usage/tool reports, credential
provisioning, SQL ownership, public routes and actual effects remain gated by the
architecture amendment and reference captures. Full proposal and its eight
unresolved freeze gates remain draft; none is settled by this partial package.

## Wire validation

A frame is a four-byte unsigned big-endian payload length followed by exactly
that many UTF-8 JSON bytes. Maximum payload is **16 MiB**, maximum container
nesting is **64**. These ratified bounds are internal control-profile limits,
not public workload size guarantees. Workload size/profile acceptance remains
gated by real reference measurements. There is no fragmentation or reconnect.
Readers handle partial/interrupted reads and distinguish clean EOF before a new
prefix from truncated prefix/payload. Writers handle partial/interrupted writes.
They do not flush or manage lifecycle; the eventual supervisor owns its pipes.

All structural keys are required, including nullable fields; extra keys fail.
UUIDs use canonical lowercase spelling. Integers are lexical JSON integers
within 0..9,007,199,254,740,991; positive fields exclude zero. Boolean, `1.0`, and
lexical `-0` are rejected as integers with `InvalidFrame`. Python preserves `-0`
as a floating-point token to match Rust's classification before typed validation;
it must not normalize it into accepted unsigned integer zero. Invalid UTF8,
duplicate keys at any depth, non-finite
numbers, lone surrogates, trailing JSON and excessive depth fail before a
session transition. Unknown protocol/frame variants fail; there is no fallback
to a full workload adapter, HTTP or pickle. Error text contains static categories,
not supplied bytes or credentials.

The JSON Schema documents structure; codecs additionally enforce binary
framing, UTF8, duplicate keys, lexical integer/depth rules and Start's
`correlation_id == prepare_message_id`. JSON Schema's mathematical `integer`
definition alone does not exclude `1.0` or lexical `-0`. Non-Start correlation
IDs must be null.
Encodings are ordinary compact JSON, **not canonical signed/hashed bytes**.
Cross-language tests compare decoded typed values/semantic JSON, not invented
evidence canonicalization.

Hello carries actual artifact observations. SDK distribution/version, image
digest and lock hash may explicitly be null; null is not replaced by platform
version or a requirements filename. The launch artifact ID and any parent-known
image digest are checked against the parent binding. A child-provided digest
alone proves nothing. RuntimeArtifact does not claim immutable dependencies or
installed-package inventory. No credentials/signing keys appear in frames.

## Parent-input frontier

`PreparedBinding` is an explicit parent input from future accepted preparation;
no fake wire Prepare/Prepared or child readiness creates it. It contains:

- supervisor incarnation, session and opaque parent-observed process identity;
- either workflow logical ID + WorkflowExecutionAttempt ID/positive number, or
  agent-run logical ID + generic ExecutionAttempt ID/positive number;
- Prepare correlation ID and expected launch artifact ID/optional image digest.

Generic agent attempts do not acquire an invented lease token. Workflow and
delivery claim tokens remain parent-private. The validator checks typed pairing,
session/process, direction, independent per-direction sequences, exact Start
correlation, artifact and parent-supplied committed-start authorization. It does
not inspect/commit database state, accept child-elected authority, import code,
launch a process, or enable effects by itself. The eventual caller must obtain
process identity from its supervised handle, not from a child PID/string.

`StartAuthorization` is a parent assertion of an already committed transition,
not SQL proof. It is supplied separately after Hello to permit a matching parent
Start; missing, repeated, mismatched or early authorization fails. Hello only
records an observation against the supplied binding. Session states are
AwaitHello → Prepared → Executing → Cancelling/StoppedObserved, with pre-Start
Cancel/rejection supported. Invalid input consumes no sequence or accepted
frontier. No result/delivery receipt is accepted or projected.

The child-observed Start/Cancel frontier is tracked separately from parent-sent
commands. Independently ordered directions permit queued prepared/null-Start
heartbeats after sent Start or pre-Start Cancel, and queued executing heartbeats
or completed/null-Cancel stop after sent Cancel. Those prior observations become
invalid after the child acknowledges the corresponding command. There is no
new arbitrary message-count timeout: actual bounded process lifetime is the
future supervisor's responsibility. Parent-sent Start remains possible execution
even if a queued rejection/stop subsequently arrives. This never authorizes
replay or revokes durable custody.

StoppedObserved reports a child observation. It does not prove OS process exit,
Result acceptance, durable completion, delivery settlement, rollback, or safe
replay. `result_message_id` is opaque correlation only in this partial profile;
no Result codec/receipt exists. A Cancel does not erase possible effects. No
HTTP authority/SDK credential transport or provider-redirect proof is supplied.

## Checks and shared fixture path

Both suites consume the canonical fixture above. Rust defaults to its actual
crate fixture using `CARGO_MANIFEST_DIR`, so existing `cargo test --all` remains
usable in the `core-rs/` Docker context. Python defaults to the source tree path;
supported Docker integration sets `BIFROST_RUNTIME_VECTORS` to the same canonical
file mounted read-only. An explicitly set missing path fails; neither suite
skips or fabricates fixtures.

Supported scoped commands inside the existing containers, wired by the architect:

```sh
cargo test --locked -p bifrost-contracts
python -m pytest --confcutdir=tests/runtime_protocol tests/runtime_protocol -q --no-cov
```

The tests-only peer helpers additionally feed each real encoder's framed bytes
into the other decoder. Inside the supported containers with an owned temporary
directory mounted at `/exchange`, run in this order:

```sh
cargo run --locked -p bifrost-contracts --example runtime_control_vectors -- emit > /exchange/rust.json
python -m tests.runtime_protocol.interchange validate /exchange/rust.json
python -m tests.runtime_protocol.interchange emit > /exchange/python.json
cargo run --locked -p bifrost-contracts --example runtime_control_vectors -- validate /exchange/python.json
```

Emitters export only the nine valid synthetic golden fixtures, with known names;
validators require exact profile/cardinality, no duplicate/unknown names, one
framed record per entry and equality with its golden typed Frame. Peer artifacts
are capped at 1 MiB for this tests-only seam, contain no credentials or arbitrary
workload input, and are mounted read-only for validators. Invalid wire/binary/
session vectors remain covered by the ordinary tests. The helper is not a
production binary or a second runtime service.

The Python scoped command avoids platform autouse DB setup for this pure codec;
normal repository suite consumption still resolves the real shared fixture.
Use `./test.sh` integration on `pve-t340.netbird.cloud` or hosted CI, never host
pytest. Also retain API quality, Rust fmt/Clippy/all-feature/security/license/image
checks and clean-candidate pre-PR gates. Passing vectors proves this partial
encoding/frontier only; actual workflow and provider/tool runtime acceptance,
custody, ownership and in-flight rollback require later packages.
