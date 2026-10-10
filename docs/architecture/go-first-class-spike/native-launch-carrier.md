# Isolated guardian launch carrier

SDK `0.0.0-spike.2`; isolated implementation under the existing interface release.
This does not accept a runtime, enable production dispatch or authorize deployment.

`cmd/launcher` is a first-party minimal container init, separate from the authored
workflow and common runtime adapter. Its scratch image contains only the launcher:
no shell, compiler, source, DB/Redis authority, signing key or customer credential.
It accepts only a lowercase 64-character delivery-index digest. The adapter,
read-only session bundle and one private FIFO are fixed guardian-controlled paths.
The launcher requires a nonroot identity and a readonly, owner-only mode `0600` FIFO
owned by that exact effective UID. Regular files, symlinks and shared mode deny.
It forwards protocol standard streams and supplies the FIFO as inherited FD3;
its adapter environment contains only nonexistent PATH/HOME values. It forwards
SIGTERM and waits/reaps the adapter with a bounded kill fallback.

Opening that FIFO is not SDK issuance or release authority. The actual Rust
coordinator must own its writer and container/channel custody, verify accepted
bytes/source and current eligibility, commit/observe Start and finite provision,
and unambiguously observe release before writing any material. No retry,
retransmission, matching UUID, persisted release row or launcher exit can replace
those gates. The carrier makes no lifecycle decision and writes no durable state.
The adapter retains its existing single-delivery and Start/provision validation.

The existing build plane compiles the launcher with the pinned Go toolchain,
CGO disabled and readonly modules, then builds/exports a task-owned scratch image.
The build evidence binds launcher bytes, Docker image config identity and exported
image archive bytes. This is private guardian infrastructure evidence, distinct
from the common native workload artifact's image field; it is not presented as a
production registry-manifest digest. Only the task-created carrier tag is removed,
with cleanup diagnostics retained. An existing tag causes denial rather than
ownership reassignment. The authored workflow bytes remain pinned to the original
`160917deeb94275f31ca9ddee2dacae00fb079fdf54cb60e206c8def261f9c34`.

The guardian must verify this carrier evidence and accepted immutable mounts,
retain the existing nonroot/read-only/no-capability/resource limits, and attach
only the restricted SDK network. Workloads must receive no DB network, Docker
socket, source, compiler, signing key or ambient parent credential. The FIFO must
be private to this execution before any tenant initializer runs; no renewal or
second material delivery is authorized. Container PID-namespace exit must be
observed independently to establish descendant cleanup. A direct-child wait or
advisory Stopped alone is insufficient, and source cleanup requires its own proof.

Kernel tests cover invalid invocations, FIFO ownership/readonly access and
regular/symlink/shared-mode rejection in the existing nonroot hosted Go lane.
These are carrier component tests. Actual container/channel custody, live SDK
HTTP ingress, durable Result/Receipt and authenticated Execution API readback,
Cancel, crash/uncertain-spawn/lost-ACK recovery and complete descendant/source
cleanup are still required before acceptance. No carrier component or green
conformance result changes that status.
