# Reviewed Root files for migrated workflows

Seven global workflow registrations still import `bifrost.files`. Four use
Root file objects; three share their source modules. The current immutable
workflow handoff rejects that import and Solution file APIs reject the Root
workspace namespace. Broad outbound access does not resolve workspace files.

## Preserve the existing authority

Keep the Root XML profile, PowerShell installer, uploaded packages and callback
download objects in their current locations. Keep repair backups under
`.artifacts/ninjaone-webhook-config-repairs/`. Do not copy mutable objects into
an installation or grant cross-organization file access.

The September 30 readback found the XML profile at 320 bytes and the installer
at 15,571 bytes. Neither path belongs to the active Workspace release's
effective file map or governed paths. Their stat hashes describe mutable Root
objects. The global uploads policy for `cisco-secure-client/packages` has no
allow actions; the new adapter must preserve that denial. Existing Root file
policy evaluation still applies.

## Deployment contract

Add explicit immutable Root file bindings to the manifest, resolution map and
reviewed recipe. Each binding names a Root location, a canonical exact
path or directory prefix, allowed operations, a positive byte bound and a
maximum signed-download TTL. Restrict locations to `workspace` and `uploads`.
No custom location, local backend, organization selector, delete, overwrite,
upload URL or policy mutation is granted. Workspace read bindings must exclude
Python source and hidden platform authentication paths. Backup creation permits only bounded
JSON objects of at most 64 KiB under the exact repair prefix. Backups contain
webhook credentials; no backup read, list or signed-download capability exists.

Uploads select exactly the durable execution organization, or global when that
execution has no organization. Global workflow registrations can execute in an
organization context. Preserve that existing selector; do not substitute the
installation organization or add a global fallback. A request scope must agree
with the durable execution. Workspace remains one unscoped Root namespace.

Root FilePolicy remains authoritative for uploads and is evaluated on every
request at the selected scope. Workspace retains its existing superuser check.
The engine token already carries that identity; the adapter does not mint an
elevated token. Source revisions preserve the immutable binding grants; changing
them requires a separately reviewed workflow revision. An old accepted execution
retains its deployment's binding contract after a successor.

Only a signed, active execution attempt can use a binding. Verify its durable
deployment ID, immutable evidence, Solution identity and registration scope.
Ordinary user tokens, caller-supplied installation IDs and inactive
attempts receive no Root grant. Match the requested operation and normalized
path before selecting the Root tier. Apply existing Root FilePolicy rules and
retain Root metadata and storage ownership.

SDK file routes reject a signed Solution engine request that drops its
Solution context. Explicit cross-install requests keep the existing Solution
file tiers and policies and receive no Root grant. Existing generic editor
calls keep their separate Root authorization. The generic SDK API does not
append Solution query context; operational JSON consumers use this editor
contract today. This adapter does not make the generic API or editor endpoints
a capability sandbox.

Workspace asset reads and download signing require the reviewed SHA-256.
Exact JSON operational
data reads may omit a hash so updates to the existing Root data authority remain
visible without a deployment. An optional JSON hash is enforced when supplied.
Both kinds of reads enforce the reviewed byte limit. Candidate inspection and
activation check required exact workspace objects for existence, bounds and any
configured hash. They check the candidate's bindings, permitting a reviewed
workflow revision to repair old grants. Dynamic uploads and backup prefixes are
not enumerated during delivery.

Use existing file APIs for `exists`, bounded reads and GET signing. Check the
actual bytes before returning a read; a separate stat cannot prove its size or
hash. Download signing also checks current bytes and any configured hash.
Signed URLs authorize an object key and a TTL of at most 600 seconds.
They do not pin an object version: a later Root overwrite can change the bytes
that the external client fetches. Do not claim immutable resource evidence for
these downloads. Version-pinned external delivery needs a separate backend
snapshot or verifying proxy contract.

For backup
writes, force the existing create-only transaction and path lock, check the
decoded byte bound and JSON type, and reject an existing path. Activation never
invokes source code or changes a file policy.

## Acceptance checks

- A handoff with the exact reviewed bindings preserves source hashes, UUIDs,
  scopes, roles, endpoints, triggers and effective runtime bounds.
- Missing bindings still reject file imports; bindings never authorize another
  location, scope, method, path or oversized object.
- Current Root policy denials, stale pointers and stale execution evidence fail closed.
- Dropping the Solution query cannot obtain Root reads, writes, deletes,
  listings, metadata or upload signing with a signed Solution engine token.
- A bounded mutable JSON read sees subsequent Root data updates without a new
  deployment. Installer changes fail the configured byte hash.
- A real local worker can read the bounded Root installer, sign an allowed
  synthetic download, and create one repair backup while Root ownership stays
  unchanged. Denied upload policy stays denied.
- A second create at the same backup path fails. No upload, overwrite, delete,
  Root Python write or authentication-file read is possible through a binding.
- Repeat the scoped worker proof in the isolated native canary before using
  this contract for the seven production registrations.

The obsolete Workspace promoter is a separate retirement task. This adapter
does not grant its Root source writes or release activation operations.
