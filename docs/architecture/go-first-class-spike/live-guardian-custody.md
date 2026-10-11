# Isolated Rust guardian custody

SDK `0.0.0-spike.2`; implementation under the existing common interface release.
This is a prerequisite component, not runtime acceptance or a lifecycle writer.

The private Rust guardian launches only an already-built carrier image by exact
image config digest, with `--pull=never`. It mounts only independently verified
native bundle bytes, the exact adapter, and this session's private material FIFO.
The initial lane is dormant and network-none: no Prepare, Start, grant, SDK bearer
or tenant initializer. The normal case sends only common Select after receiving
the actual adapter's Offer; the two recovery cases send no release material.
No compile, source import, dependency installation or Rust linkage occurs in the
runtime container. Authored workflow and Go SDK bytes remain unchanged.

The coordinator creates an exclusive, synced private launch intent before daemon
effects. An existing intent or container name denies creation. The image, user,
readonly root, all-capability drop, no-new-privileges, private PID/IPC modes,
disabled restart, fixed resource budgets, exact readonly mounts and absence of
additional networks must match actual Docker observations. The guardian owns the
attached CLI and protocol stream descriptors, with bounded frame size and a
bounded receive queue. Outbound I/O is nonblocking; partial/error closes the
channel and cannot be resent. Exact received payload bytes remain available for
future common Result receipt hashing without JSON normalization.

Actual PID/start-time and cgroup membership, loopback-only kernel interfaces,
the observed Offer payload, source mounts and attached CLI identity are combined
into the private custody observation. Kernel identity is retained before returning
that observation. This is implementation-private evidence, not a public Rust wire
type or transferable authorization token. Admission still requires actual source,
caller, immutable association and owner fences; custody alone grants nothing.

Recovery verifies the original private intent and actual container configuration
and can only stop/drain that container. Recovered instances cannot attach/start,
even if no effects were observed. A retained kernel record prevents recovery from
recapturing a different incarnation. The original process may exit between Docker
and kernel reads; stop-only recovery retains its original evidence through that
race instead of treating disappearance as permission to launch again.

Drain independently observes stopped container state, original init-process
absence (with PID reuse fenced by start-time), and an empty or removed original
cgroup. It then removes the exact container and waits/reaps its owned attached
CLI. A container that previously started without retained kernel evidence does
not acquire a namespace-drained claim solely from a stopped status. Physical
drain never finalizes a lifecycle or settles the database source consumer.

The hosted live probe stages the same unchanged workflow and actual immutable
archive, then exercises normal Offer/Select/drain, a discarded create reply with
stop-only recovery, and actual SIGKILL of a Rust guardian after observed Offer.
The crash case requires parent wait of that guardian, original namespace/cgroup
drain, observed reaping of its original attached CLI, and denial of fresh start.
After container/mount release, private FIFO and staged bundle pathnames are
removed; journals and exact diagnostics remain. Independent task-container
inventory and carrier-image cleanup are retained. These checks are pending until
the exact candidate's hosted evidence is inspected.

This does not prove the required unchanged-binary SDK execution, authenticated
issuer/ingress, real Rust admission/Start/result/finalization chain, Execution API
readback, cancellation after possible effects, native workload grandchildren,
uncertain owner commit/material/spawn/lost-ACK settlement, or durable source
consumer cleanup. Those remain mandatory for the complete vertical slice and
human runtime acceptance. Production dispatch and Python-owned executions remain
unchanged; no deployment, merge, credential expansion or renewal is authorized.
