# Isolated restricted SDK transport

Implementation candidate under Thomas's existing isolated interface release;
SDK `0.0.0-spike.2`. No new public runtime message, lifecycle writer, production
route, credential, renewal or dispatch is introduced.

The accepted authored workflow still calls the same Go SDK HTTPS origin. The
trusted scratch-image launcher may explicitly enable `--sdk-relay`; it listens
only at `127.0.0.1:8443` inside the workload's network-none namespace and forwards
opaque TLS bytes only to `/sdk/ingress.sock`. A single readonly mount exposes that
private per-session socket directory. It contains only the owner-mode `0600`
socket, in a `0700` directory owned by the runtime UID. No private key, DB URL,
application credential, source repository or general network socket is mounted.

The relay neither terminates TLS nor reads HTTP authorization. The Go SDK still
verifies the server certificate against the supervisor-supplied CA and exact
loopback hostname/IP, sends its finite bearer to the restricted SDK ingress,
disables redirects and performs no renewal or retry. The parent gateway alone
owns TLS/signing/DB material in a separate process and filesystem namespace.
The socket must serve only that closed ingress; connecting it to a general API,
Docker, DB or arbitrary forwarder would violate this design.

The launcher pins the original socket device/inode and checks it before each
connection. There is no alternate destination or reconnect fallback. Four total
connections, two MiB per direction and a ten-second connection deadline bound
transport resource use; these limits grant no SDK permission. Closing/cancelling
the launcher closes listener and in-flight transports and waits for relay work.
EOF, budget exhaustion or I/O failure closes both directions without replay.
The socket's parent is trusted and immutable during custody; a replaced socket
cannot stand in for the original ingress. The guardian retains its optional
socket path in the exclusive launch intent and verifies the exact readonly mount,
launcher arguments, unchanged image ID and absence of additional networks.

The ordinary Go tests exercise a real certificate-verified HTTPS exchange through
an actual Unix listener, cancellation of an in-flight connection, replacement
denial and rejection of shared, symlink, regular-file and non-loopback inputs.
The hosted guardian probe adds a dormant carrier with the actual SDK socket
mount, observing kernel loopback-only interfaces and physical drain followed by
socket/source/material path cleanup. These checks require inspected hosted
evidence for this exact candidate. A dormant relay is not a restricted issuer,
SDK admission or tenant-execution proof.

The next connection must supply real restricted ingress: verify the dedicated
finite JWT and all immutable preimages, refresh current caller/source entitlement,
recheck the original live guardian and obtain Rust SDK admission under the common
lock order before the stable integration fetch. Use a synthetic integration with
no OAuth provider so fetching cannot refresh credentials or contact a vendor.
Cancellation must interrupt the waiting SDK request while Rust retains its
winning decision and finalizes only after physical and source-consumer drain.
Durable Result/Receipt and the existing authenticated Execution API readback
remain mandatory. The common protocol and authored binary are unchanged; this
transport candidate does not satisfy or waive those remaining acceptance gates.

## Private ingress-to-owner bridge

The isolated ingress uses a separate private `gate.sock`, which is never mounted
into the workload. Its original actor PID, UID, kernel start-time and pathname
come from parent process custody. Ingress verifies the actual Unix peer with
`SO_PEERCRED`, start-time and original socket inode. The trusted ingress and owner
must be able to observe the same original PID; an invisible or changed peer fails
closed. Sharing custody between trusted components must never expose their PID,
filesystem, keys or DB namespace to the workload.

This private bridge has one read-only operation and no lifecycle dispatch. Its
request is a four-byte big-endian payload length followed by sorted-key, compact
UTF-8 JSON, bounded to 4096 bytes, followed by sender write EOF. The closed object
contains exactly `grant_id`, `grant_digest`, `integration_name`,
`organization_id`, and `solution_id`; identifiers use canonical lowercase UUID
text, the digest is 64 lowercase hex characters, and the trimmed integration name
is nonempty, at most 255 UTF-8 bytes and contains no NUL. Whitespace, duplicate or
unknown keys, type coercion, truncation, missing EOF, a second request, or a
lifecycle command denies before SQL. The existing finite JWT/preimage verifier
must produce this intent first; these fields alone are not a credential.

Rust supplies the original SessionFence from its own retained actor state. It
matches the observed custody digest and Offer incarnation, rereads the exact
pinned neutral session bundle, checks its binding against that fence and immutable
owner source, then performs the common SQL SDK admission. A final fresh physical
check prevents process/channel loss during SQL from qualifying an external fetch.
No table row or saved digest can restore custody to a recovered actor.

The reply uses the same length prefix and a closed JSON object with boolean
`admitted` and `request_sha256`, the SHA256 of the exact request payload excluding
its prefix. Ingress accepts only `admitted: true`, its own matching request hash
and reply EOF; a bounded 1024-byte response, denial, mismatch, malformed/truncated
response, changed peer or timeout prevents fetch. There is no retry or reconnect.
The reply belongs to one private connection/request, not a portable permission.
The owner bounds accepted private connections to sixteen and removes only its
original socket inode during explicit retirement. This does not settle physical
runtime or durable source-consumer cleanup.

Rust and Python independently test canonical request bytes, unknown/duplicate
fields and response request fencing. The live guardian probe exercises original
session/custody matching and a lifecycle-command denial through an actual private
Unix stream before SQL, with no database authority at its lazy test endpoint.
These new checks still require exact-source hosted evidence. Positive finite JWT
HTTP ingress, real source/owner transactions and the same unchanged tenant binary
must be connected before this bridge can qualify as the required SDK slice.
