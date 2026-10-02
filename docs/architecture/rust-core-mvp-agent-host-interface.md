# C1-R host provisioning and disposition

Status: **minimal design accepted; replacement source interface not yet frozen**.
2026-10-02. Root and independent Sol review select alternative A within the
existing trusted CI host/Docker-admin boundary. This is source architecture,
not privileged/runtime execution, nominal acceptance, merge or deployment.

## Superseded component and exact evidence

C1-R-HOST-S's custom elevated subsystem remains SOURCE STOP. Its incomplete
854-line draft required bootstrap/disposal/tests still to come, estimated above
1100 lines. Completing that framework exceeded its own complexity gate.
The final862-line fail-closed draft was never imported/executed or integrated;
AST/locked Ruff alone passed. Root preserved exact bytes outside the reference
worktree at `/tmp/bifrost-agent-reference-host-stopped-84937e4e.py`, mode0600,
SHA256 `84937e4ebf8ace19a8ee618e7200074f2f00eaf387e0077eaba33058195a443a`.
Do not restore/complete/execute it under the historical release.

The [historical e037 interface](https://github.com/Midtown-Technology-Group/bifrost/blob/c5559c33f3168c8f5aa1db7220db45a0ffe74f73/docs/architecture/rust-core-mvp-agent-host-interface.md)
is retained as provenance only. Its active root-prefix, snapshots, bootstrap,
private record and operation-dispatch requirements are superseded here, not
carried forward into another helper. Controlling [integration](rust-core-mvp-agent-reference-integration.md),
[wire](rust-core-mvp-agent-observation-wire.md) and unchanged codec remain governing
except these explicitly selected internal provisioning/cleanup changes.

Reviewed alternative packet SHA256:
`d283a95ba1eefce46c928ec22d9e48a642a4b5fdcb87d09845b0430385717931`.
Independent review, including root's publisher receipt correction, SHA256:
`c57438fe661b0c79d0f82003d2898b85b4313b87e464eca9092d18cabeba0474`.
Source inspected: reference `a01437e68948ec08d96c070229e93144f40cde43`.

## Three fixed effects under the existing trusted coordinator

The isolated host coordinator already controls the local Docker-root daemon.
Host administration and that coordinator are trusted; authored workflow/container
processes and their JSON are not. Another root-local identity registry cannot
exclude the administrator who controls it. Retain real FD/namespace/resource
checks while removing that redundant framework. If hostility by this existing
Docker/sudo administrator must become a threat requirement, stop and redesign
rather than claim these literals isolate it. [Docker daemon security](https://docs.docker.com/engine/security/).

Only THREE independently reviewed constant installed-stdlib literals are proposed:

| Operation | Fixed effects | Closed actual inputs |
| --- | --- | --- |
| Provision | FD-based ownership transfer of exactly six existing0600 materials; exclusive api/api-replica0700 directories in fresh saved status volume; exclusive runner-owned0700 C/host-status | Independently verified C/parent and volume/Mountpoint identities, expected installed readers, original material identities |
| Publish | Fixed exclusive0600 temporary file, expected owner before final replacement visibility, atomic replacement/fsync, actual final FD identity and bounded reread | Saved C/directory/readers, actual prior-final identity/digest or fixed first-absence sentinel, one prevalidated body<=4096 |
| Host-status disposal | Admitted fixed regular final only; temporary must be absent; then empty directory removal through pinned descriptors | Actual saved directory/file identities after verified reader removal/unmount |

Fixed sudo/system-Python `-I -S -B -c` invocation executes the frozen literal bytes,
never a mutable source pathname, codec, renderer, fixture or product module.
No shell interpolation, generic operation/command/path selector, elevated Docker
client/TAR/schema parser, root registry, helper service, new dependency or sudoers
change. Installed executable/stdlib protection remains an actual runtime gate.
Exact argv/data grammar, allowed ancestry, complete short-read/write/EOF behavior,
literal bytes/hashes and partial-failure disposition need a separate source freeze.
This document releases no replacement implementation by implication.

Ordinary host may lack access to protected DockerRootDir/Mountpoint inodes.
The first provision therefore captures their actual nofollow FD identities,
anchored to the trusted coordinator's independently saved fresh default-local
volume inspect/name/path and empty/no-consumer proof. It returns bounded fixed
ordered private directory-identity metadata, including created role/host-status
directories, for later continuity checks. No fabricated ordinary pre-stat,
additional root probe, existing-volume adoption or registry is permitted.
Exact receipt/argument framing remains part of the unreleased literal interface.

Six transferred basenames are fixed: observer-ingest-key, observer-control-key,
model-oracle-input.json, observer-server-key.pem, observer-ca.pem,
observer-server.pem. Preserve contents/inode/nlink1/mode and descriptor checks;
host-only CA private key/CSR/extensions/owner records remain host-owned/unmounted.
Expected readers come from the actual stopped-created NORMAL init's account and
entrypoint, followed by independent AFTER-start foreground UID:GID/executable/
argv/readability proof. No Config.User, hardcoded1000 or TAR entry alone is enough.
Reuse that actual init ID; retain distinct template-Alembic custody/exit/removal.
No secret mode, mount role, source custody, TLS or initialization-order relaxation.

## Actual publisher receipt and unchanged evidence semantics

Ordinary host UID cannot stat a prior final inside runner-owned0700 host-status.
Publisher therefore returns ONE private bounded receipt: canonical ASCII
`<actual_final_st_dev>:<actual_final_st_ino>\n` prefix<=64 bytes, followed by actual
final host_status bytes<=4096 and EOF. Integers are canonical positive decimal
<=2^64-1. Reopen the actual fixed final nofollow/nonblock, require regular/nlink1/
0600/expected owner, and read body from that SAME FD. Return its actual fstat,
not an anticipated inode or another pathname. Coordinator bounds/parses this
internal frame, validates actual body through the unchanged pure codec, compares
to the admitted body, and retains actual tuple plus SHA256(body). Next publication
uses this observed prior identity; missing/uncertain/changed continuity fails.
No raw body, whole receipt or capability in logs, argv or environment. The fixed
continuity argument may contain only the prior actual native device/inode tuple
and SHA256 of its actual receipt body. This is not a shared wire
family, observer ACK, actual exit truth, filesystem-erasure or closure proof.

Before publication, the directory entry set must be empty for initial absence,
or exactly the fixed final for a prior actual receipt; the fixed temporary and
all unknown entries must be absent. After publication, require exactly the fixed
final and recheck its name/FD/directory identity. Never prune foreign entries.
The exclusive temporary initially belongs to root with mode0600; fchown and
owner/mode checks precede FINAL replacement visibility, not temporary creation.
Provision directory ownership is checked before consumer creation or mounting.
Caught input/Python/syscall failures emit fixed static diagnostics only. Signals,
SIGKILL and uncertain termination can leave empty/partial pipes; those outcomes
fail and retain an owner. Never infer rollback, synthesize a receipt or resume.

The ordinary trusted coordinator validates unchanged host_status BEFORE publish
and AFTER actual returned-byte readback. The privileged operation only writes
bounded bytes to a fixed destination. One writer, strict generations and sticky
failure remain; crash means failed attempt, no resume/adoption/replay into success.
Actual finish identity, saved API exits, closed ACK/status/Uvicorn lifespan,
domain/public/DB/source/process/event joins remain independently required.

## Cleanup and complexity gate

After retaining safe evidence and the acceptance/failure disposition, inventory
ALL actual daemon references, remove verified owned consumers and prove mounts
gone. Revalidate saved exclusively new default-local volume metadata/Mountpoint;
then use stock daemon volume removal only for that saved volume, without force,
prune, name discovery or retry. Verify absence. Foreign references, changed
identity, truncated inventory or uncertainty fail cleanup with a retained owner.
This supersedes per-entry privileged recursion over API-created volume contents.
Corrupt/missing status still fails nominal acceptance even if owned-volume cleanup
succeeds. Fixed host-status disposal rejects unknown/replaced entries; no recursive
host deletion or permission repair. Generic down-v must not preempt these gates.

Target120–180 TOTAL privileged executable-logic lines; STOP at200 or more across
all THREE emitted literals, including duplicated/inlined logic. No compressed
syntax, hidden constructor/import or another file/operation evades the ceiling.
Ordinary integration growth is separately reviewed; reuse existing verifiers.
Independent code/literal/footprint review precedes supported units/quality and
literal clean committed pre-pr. Installed custody, actual replacement visibility,
genuine lifecycle/closure and exact resource/context absence precede nominal PASS.

Provisioning within current init/fixture alone (alternative B) is source-falsified:
entrypoint drops root before Python, private mounts are RO, subpaths must exist
before API creation, and host disposition still needs an actual host observer.
Adding writable secret mounts/root entrypoint effects is not an implicit fallback.
