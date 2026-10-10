# External worker identity ownership

Scope: optional external worker broker software. No deployment, image pin or
enabled controller policy changes are included. Vojeto owns portable networking;
this platform owns broker authorization and durable host allocation.

The durable owner is stored in `vojeto_owner` on the existing global controller
host row, under the existing PostgreSQL advisory transaction lock. This is
infrastructure bookkeeping, not an org-resolved configuration or a new job system.
The capability token is randomly generated and only its SHA-256 hash is persisted.
Provider checkpoints are encrypted with the platform secret-encryption primitive.

Acquisition refuses legacy allocations and every active, expired or uncertain
owner. The initial process attempt must be fresh. Expiry never permits takeover:
a paused transport may still possess working credentials. Only a clean release
allows another generation on that host; existing verified absence/deletion
reconciliation remains the separate abandoned-host recovery lane.

The transitions are acquired, begun, grant-started, granted, bound, checkpointed,
released. Begin and grant-started commit before crossing the Defined mutation
boundary. Grant is single-use; response loss or provider uncertainty cannot be
retried into a reusable identity. Bind requires the actual allocated host and
configured network. Checkpoint acknowledges durable encrypted state. Release
requires checkpointed state and the caller's guarantee that transport has stopped.
The trusted local agent must preserve Vojeto's checkpoint/stop/release order;
the broker cannot inspect remote process file descriptors.

The database wall clock establishes a 60-second deadline. Transactions use
`clock_timestamp()`, not the timestamp frozen at BEGIN. Renewal accepts only the
same unexpired token; late provider responses cannot revive ownership. Every
operation checks live configured-app replica membership and serializes against
the existing controller. Legacy enrollment refuses any managed owner, including
released and expired records, so it cannot bypass the new fence.

Real PostgreSQL tests cover concurrent acquisition, clean completion/reuse,
stale token rejection, paused/expired owner refusal, legacy allocation rejection,
wrong-host binding, encrypted checkpoints, failed initial checkpoint, lost grant
responses and late response expiry. Provider HTTP is synthetic in those tests.

This first slice supplies service primitives and protects the legacy boundary.
It does not expose an enabled HTTP protocol or ship the private Unix agent yet.
Those adapters must enforce managed-identity authentication, bounded private
requests, fixed consumer constraints and independent lease monitoring. Actual
Defined compatibility, platform restart/termination and canary/rollback proof
remain deployment acceptance gates. No production worker replacement is claimed.
