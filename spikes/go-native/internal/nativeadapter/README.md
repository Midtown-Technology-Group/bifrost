# Trusted native adapter components

This package implements the private material reader released in the isolated
owner proposal. It imports the language-neutral Go codec and standard library;
it does not import tenant authoring code or Rust implementation types.

The inherited descriptor must be a read-only pipe, is marked close-on-exec before
reading, has a caller-supplied finite preparation deadline, and is closed on every
return. A reader consumes at most one delivery, including failed deliveries. The
guardian must close its writer after one four-byte big-endian length-prefixed
UTF-8 JSON envelope of at most 65,536 bytes. Unknown keys, duplicate decoded keys,
invalid Unicode, excessive nesting, trailing bytes and another delivery reject.

The envelope has exactly these fields:

* `version`: `runtime-private-delivery/v1`.
* `provision`: the exact structurally valid common Provision frame. Every decoded
  field must match the Provision already observed on the public protocol stream.
* `sdk_configuration`: exactly `endpoint`, `bearer`, `organization_id`,
  `solution_id`, `test_ca_pem`, all strings. HTTPS origin and certificate parsing
  remain mandatory. This isolated fixture requires a supplied trusted CA.

This finite readiness slice requires a workflow binding, organization scope,
matching organization/Solution, an unexpired grant and exactly `integration-get`.
The only configuration returned to the later tenant FD3 excludes the public
control frame and delivery references. Error text is static and never embeds
private material. This is a closed private delivery format, not an additional
public lifecycle message or a production profile freeze.

Descriptor shape and matching wire fields do **not** authenticate the issuer,
prove sole-writer custody, observe a committed admission, or authorize spawn.
The verified guardian, actual issuer/ingress, release transaction, native adapter
entrypoint and accepted bundle remain required. No process is launched by this
component; no runtime acceptance is claimed. The existing Go workflow and SDK
remain unchanged. Tests run through the existing isolated Go CI lane.

On the initial Linux/amd64 target, `SealExecutable` copies the supplied accepted
child into a memfd, verifies the exact binary digest and applies irreversible
write/grow/shrink/seal locks. The later authorized launch can use that retained
handle rather than re-opening the source path. Empty, oversized, modified or
unsealable bytes fail closed. This addresses byte replacement between validation
and exec; it does not verify deployment acceptance, grant permission, guarantee
descendant cleanup or execute the binary. No Rust type or tenant import is used.

The build-plane `cmd/probe --sealed-artifact-sha256 <accepted-digest>` fixture
exercises actual exec of that sealed handle, 20 successful SDK runs, a changed
control-flow branch and a cancelled SDK wait. `measure.sh` pins the original child
digest and retains `sealed-execution.json` separately from the unchanged baseline
measurement path. Its synthetic SDK server has no real restricted issuer and its
launch is local authoring only, never evidence of Rust admission or release.

`PrepareNative` now handles inert common Prepare validation against the supplied
bundle references: exact binding/context/artifact identity, finite deadline,
schema byte digests, structured input, adapter digest, sealed child digest and
Linux/amd64 ELF entrypoint. It returns a detached common Prepared body and retains
the sealed handle; it has no launch operation and receives no SDK material.
Supplying matching references still does not prove deployment acceptance or
trusted guardian custody.

The initial workload schema subset supports JSON types and nullable unions,
object properties/required/closed objects, array items and string minLength.
Unknown keywords fail even on absent optional properties. Schema bytes are
bounded to 64KiB with strict duplicate/Unicode checks and depth 64; private
material retains its independent depth-16 limit. Numeric validation preserves
exact decimals and bounds exponent expansion to +/-65536 before allocation.
This is an explicit subset, not a general JSON Schema implementation. minLength uses
canonical nonnegative integral JSON numbers (not decimal/exponent spelling),
matching the current build metadata. Rust minLength support is now implemented;
its existing floating numeric-data handling still needs an exact-number review.

Preparation tests use the published independent Prepare vector and the test
executable as inert synthetic ELF bytes. They test identity/schema/deadline and
byte drift plus detached retained state; they do not launch it or establish
accepted bundle provenance. Full adapter dispatch and Rust guardian integration
remain unfinished.

`NativeFrontier` checks parent Start/Provision observations after Prepared in
both contract-permitted delivery orders. It retains detached frames, rejects
identity/correlation/start mismatches, duplicate IDs, stale parent sequences,
expired grants and unbounded budgets, and narrows the read deadline to the
minimum of absolute deadline, observed remaining budget and grant expiry.
Cancel or transport closure permanently prevents subsequent material access.
These are runtime-side observations only. They do not observe a database commit,
authenticate material, permit physical spawn or finalize cancellation. The live
protocol loop must serialize calls; live guardian/issuer integration remains
required before this state can participate in an accepted launch.

The initial ELF target also requires little-endian amd64 and rejects PT_INTERP:
the CGO-disabled workflow must not depend on an ambient dynamic loader. Negative
tests mutate actual synthetic ELF headers and update both byte digests, ensuring
format validation rejects them independently of the digest check.

`PrepareTransport` performs the actual length-prefixed common negotiation and
preparation exchange on supplied streams. It offers only the supported native
class, checks Select/Prepare correlation, session, unique message IDs and parent
sequence, and emits Prepared only after inert preparation succeeds. A failed
Prepared write closes the sealed handle and returns no runnable preparation.
Caller-owned transport deadlines/cancellation, custody and subsequent guardian
launch remain required. Its stream tests use contract frames and synthetic ELF;
no fake durable store or lifecycle authority is added.
