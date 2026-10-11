# Device peer session lighthouse admission

BiFrost authorizes a finite, isolated Vojeto lighthouse lease through the existing
shared platform-job host. This feature is disabled until all four
`BIFROST_DEVICE_PEER_LAUNCHER_{URL,CA,CERTIFICATE,KEY}` settings are configured.
CA/certificate/key settings name mounted credential files, never PEM values.
The router control endpoint requires TLS 1.3 and a pinned broker client identity.

An authenticated human with the canonical device execute permission calls
`POST /api/devices/{device_id}/peer-sessions` with a unique `request_id`, endpoint
`operator_key` and `target_key` (base64 X25519 public keys), one numeric IPv4
`destination` (host:port), and an absolute UTC `expires` (10 seconds to 30 minutes).
Organization and device authorization precede job admission. Target private keys
must be generated on the target; operator private keys stay on the operator.
This API does not bootstrap the Sopdet agent, attest target key collection, or
supply application credentials. Its destination authority matches device execute
permission; it does not introduce a separate LAN destination allowlist.

The response is the existing PlatformJobAccepted envelope. Poll the shared job
surface for its result, containing public operator/target grants. Locally launch
`vojeto-peer operator` and the device-side `vojeto-peer target` with those grants
and their locally held keys. Provisioning success means the lighthouse child
started; it does not prove target reachability. Verify an actual encrypted
service connection before declaring the session ready.

Request identity is scoped to actor, device and request UUID and persists across
terminal provision jobs. A changed payload under the same identity conflicts;
retry cannot mint a new lease. Use a new request UUID for a genuinely new session.
Revocation uses `POST /api/devices/{device_id}/peer-sessions/{job_id}/revoke` and
another shared job. Device scope plus requester ownership (or platform admin)
is required. Revocation remains allowed when the device is disabled. This revokes only the
lighthouse lease. Issued endpoint certificates remain valid until their signed
expiry, and an established direct tunnel can continue without the lighthouse.
Prompt disconnection requires explicitly stopping both endpoint processes;
endpoint bootstrap/stop integration is not implemented by this change. Each runner
rechecks current actor permission and device scope before touching the launcher.

Known provisioning failures attempt revocation of the job's deterministic nonce,
including unknown RPC outcomes. The launcher fences revoke-before-create races.
Hard runner loss cannot guarantee immediate cleanup: the independent absolute
30-minute maximum deadline is the fail-safe. No scheduler slot or new service
container is held for a tunnel's lifetime. Router restart terminates lighthouse processes; endpoint certificates retain
their signed expiry.

## Review and rollout

This is delivery Lane 3: human review is required for auth, execution and tenant
isolation changes. Rollout also requires a reviewed immutable Vojeto image,
router mTLS enrollment and narrowly scoped ingress from bifrost-infra's router
launcher runbook. Source merge alone does not authorize live installation.
Keep admissions disabled until these dependencies are deployed and verified.

Verify the Windows canary with locally generated keys, canonical permission and
cross-organization denial, lighthouse discovery, relay-only connectivity,
actual service forwarding, expiry and revoke. Preserve the existing Defined
router identity and UDP 4242. Rollback disables admissions and stops only the
launcher service; preserve public tombstones until signed deadlines pass.
