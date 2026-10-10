# Isolated immutable native bundle recipe

Candidate implementation under the existing isolated interface release; SDK
`0.0.0-spike.2`. This is not production registration, profile freeze, producer
trust approval or runtime acceptance. It replaces the local probe's fixture-only
identity for the upcoming owner/guardian integration. The workload is not rebuilt
on admission, and packaging does not import or execute authored application code.

`isolated-native-bundle/v1` uses uncompressed canonical USTAR bytes. The six
regular files, in exactly this order, are `adapter`, `build-evidence.json`,
`input-schema.json`, `module-graph.txt`, `output-schema.json`, `workflow`.
Executables have mode `0500`; other entries mode `0400`. UID, GID, mtime are zero;
user/group/link/prefix names are empty. No directory, link, device, PAX/GNU
extension, duplicate, path traversal or additional entry is permitted. Content
is padded to a 512-byte block; two zero end blocks and further zero padding close
the archive at a multiple of 10,240 bytes. The total bound is 96 MiB; executable
bounds are 32 MiB each, build evidence 16 MiB, other inputs 1 MiB each. Regular-file
device fields are zero bytes, not octal strings; magic/version are `ustar\0`/`00`.
Numeric fields are zero-padded ASCII octal with a final NUL. Checksum uses six
octal digits, NUL, space after treating its eight bytes as spaces.

`artifact_id = "sha256:" + SHA256(all exact archive bytes)`. The external common
native-executable/v1 descriptor carries that identity and independently binds the
adapter, child, build evidence, module graph, SDK and toolchain. It is not included
in its own hash preimage. Input/output schema hashes are retained beside it.
The original build evidence identifies source closure, Go flags/platform/toolchain,
producer run and checks. It is immutable inside the archive; different producer
evidence may correctly produce another artifact identity while the child binary
remains unchanged. Assembly verifies those byte relationships and all existing
producer checks, then creates archive/descriptor outputs exclusively.

The recipe is implemented in `spikes/go-native/scripts/native_bundle.py`. Its
consumer verifies the accepted archive digest and parses the closed regular-file
set without filesystem extraction, then requires exact canonical reserialization
and all evidence bindings. This rejects hidden trailing data and alternate
headers even when a caller supplies their newly calculated archive digest.
It does not authenticate a producer or accept a deployment merely because a
hash/claimed check matches. The trusted owner must independently verify producer
run/source, signature and review, source closure and current eligibility before
pinning the external descriptor in the existing common artifact association.
No signing identity is passed to a builder, adapter or workload.

The existing hosted Go proof lane runs recipe tests, packages the actual unchanged
readiness binary plus adapter after isolated tests/build/scanning, and signs and
verifies the external descriptor with its existing ephemeral experimental key.
The key is not a production trust anchor. Tests cover deterministic bytes, pin
mismatch, changed content, traversal/link/owner/mode/time changes, reordered or
missing/extra entries, truncation/trailing data, size bounds, ambiguous/failed
metadata and overwrite/symlink rejection. `owner-rs/src/archive.rs` independently implements the same closed byte recipe,
with no archive library or filesystem extraction. The hosted proof compiles its
consumer under the existing locked/offline Rust checks and verifies the actual
Python-produced bundle and unchanged workflow hash. It directly uses the same
sha2 0.10.9 already present in the lockfile; no registry package is added. Hosted
interoperability evidence and actual guardian staging are still required. The published runtime protocol
is unchanged; no archive parser or builder becomes part of the authored Go API.

The per-execution delivery index remains external to this immutable archive and
may bind the current session to the accepted artifact. It must never substitute
its own fixture hash for the accepted archive identity. Source archives,
registration and durable source-consumer cleanup remain separate acceptance
obligations; this recipe does not silently claim they have been implemented.
