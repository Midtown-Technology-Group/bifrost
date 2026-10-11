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
