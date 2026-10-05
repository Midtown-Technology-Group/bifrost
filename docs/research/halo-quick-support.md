# Halo Quick Support: feasibility gates and delivery plan

## Status

2026-10-05: source investigation in progress; Windows runtime spike has not
passed. Full MVP implementation is authorized **after** the spike passes.
A minimal AGPL RustDesk patch is authorized if stock behavior cannot satisfy
the requirements. No production support session API or launcher exists as a
result of this investigation.

Repository baseline: `70ef50da1a16cad2b579f5636ac10b07036ccbbe`, worktree
`/home/thomas/.codex/worktrees/8806/bifrost`, host `codex-remote-01`.
Operator-selected NinjaOne devices: technician `3124`, destination `3595`.
Confirm current device names, signed-in users and RustDesk state before any
endpoint mutation. Preserve existing installations, services and configuration.

Read-only endpoint baseline via the registered production workflow
`342ba4e1-f72c-4894-8e96-f0136abc2352` on
`https://bifrost.midtowntg.com`: both callbacks were identity-verified and
completed successfully. Device `3124` is `LT002-H8WL8H4`, Windows 11 Business
`10.0.26200`; device `3595` is `MTG-T340-COVE`, Windows Server 2022 Standard
Evaluation `10.0.20348`. Both had an active console session; destination was
signed in as built-in Administrator. Neither had a running RustDesk process or
registered RustDesk service. This does not establish absence of retained files.
Execution references: `3c45bf4b-85ef-4ad0-95c3-d4b229045466` (3124) and
`febc8e52-21ef-4d5a-ab87-b581b8de62fc` (3595), 2026-10-05 17:09–17:10 UTC.

Follow-up identity-verified probes also completed: `a9392f38-8b0a-4840-a970-a67a7053bab6`
(3124), `bd9fed2c-a609-4b19-984c-52aced91c420` (3595). Both report `EnableLUA=1`,
`ConsentPromptBehaviorAdmin=5`, `ConsentPromptBehaviorUser=3`,
`PromptOnSecureDesktop=1` and no RustDesk directory under the checked installed,
user roaming or service-profile locations. `FilterAdministratorToken` was
unset. These are bounded checks, not an exhaustive disk inventory; built-in
Administrator still needs separate testing. Do not alter UAC policy for proof.

The operator will handle customer prompts. They requested evaluation of a thin
Azure resource versus reuse; no rendezvous/relay server has been selected or
provisioned. No endpoint software was installed or started by the baseline
probes. No debug/test stack was created. The cold skill catalog did not contain
`mtg-bifrost-execution-ops`; endpoint probes used the available NinjaOne operator
procedure and the repository's existing registered-workflow execution helper.

## Product boundary

Halo owns the technician workflow; Bifrost owns authorization, policy, session
state and audit; RustDesk OSS supplies replaceable remote transport. Customers
download one Go launcher and approve control locally. Administrative elevation
requires a separate request and local UAC approval. There is no unattended
password, persistent support service or implicit managed-agent enrollment.

Platform primitives belong in this repository. Halo runbooks, integration
mapping, ticket writeback and Solution content belong in `bifrost-workspace`.
Rendezvous/relay infrastructure belongs in `bifrost-infra`. Do not embed
Halo-specific automation or RustDesk source into the Bifrost platform binary.

## Source evidence

Investigation candidate: RustDesk **1.5.0**, release commit
`fada664df7a294d1d1a9ca3e7cd3637069122f17`; its `hbb_common` submodule is
`229b904508364c8997aad0fb5af57effac859f60`. This is a source pin, **not** an
approved Windows binary. Binary SHA-256, signature identity and internal mirror
URL remain unset until artifact review and Windows testing.

Upstream release asset metadata reports `rustdesk-1.5.0-x86_64.exe`,
25,887,600 bytes, SHA-256
`8555777215510d83d2d61c9dc984e4fcc838bd7e79f9d18a42585431f5e8bb47`.
This digest was read from the GitHub release API; no downloaded binary or
Authenticode signature has yet been verified.

| Requirement | Evidence | Consequence |
| --- | --- | --- |
| Assigned technician only | [`check_id_whitelist`](https://github.com/rustdesk/rustdesk/blob/fada664df7a294d1d1a9ca3e7cd3637069122f17/src/server/connection.rs#L1439) compares the login request's self-reported controller ID. | ID allowlisting is an exposure filter, not technician authentication. A Bifrost connect endpoint alone cannot prevent bypass via a direct RustDesk connection. |
| Unelevated start | [`core_main`](https://github.com/rustdesk/rustdesk/blob/fada664df7a294d1d1a9ca3e7cd3637069122f17/src/core_main.rs#L145) identifies Quick Support from executable naming/settings and starts portable elevation in the startup path. | Do not assume Quick Support naming or `--quick_support` preserves deferred elevation. Test an ordinary portable start separately. |
| Endpoint ID | [`--get-id`](https://github.com/rustdesk/rustdesk/blob/fada664df7a294d1d1a9ca3e7cd3637069122f17/src/core_main.rs#L476) calls `ipc::get_id()`. | Prove IPC addresses the support instance rather than an installed client. |
| State isolation | [`Config::path`](https://github.com/rustdesk/hbb_common/blob/229b904508364c8997aad0fb5af57effac859f60/src/config.rs#L780) uses platform project directories on Windows. | A temporary working directory does not prove isolated configuration. Environment redirection and elevated child behavior remain unverified. |
| Config argument | [`--config`](https://github.com/rustdesk/rustdesk/blob/fada664df7a294d1d1a9ca3e7cd3637069122f17/src/core_main.rs#L501) handles a server configuration string. | Do not treat it as an arbitrary configuration directory argument. |
| Elevation result | [`handle_elevation_request`](https://github.com/rustdesk/rustdesk/blob/fada664df7a294d1d1a9ca3e7cd3637069122f17/src/server/connection.rs#L4619) initiates portable-service startup and returns an elevation response. | A request response alone does not establish customer UAC approval or successful elevated control. |

The [advanced settings reference](https://rustdesk.com/docs/en/self-host/client-configuration/advanced-settings/)
documents click approval and separate IP/ID filters, and identifies controller
IDs as self-reported. Settings must be verified in the pinned OSS build;
custom-client/Pro documentation is not proof of stock policy enforcement.
The [portable elevation guide](https://rustdesk.com/docs/en/client/windows/windows-portable-elevation/)
describes control-side requests requiring local UAC acceptance. Test both an
administrator customer and a standard-user customer; standard-user elevation
needs an explicit credential path and must never log those credentials.

## Mandatory runtime spike

Use self-hosted, pinned `hbbs`/`hbbr` and a reviewed client artifact. Record
device names, Windows versions, client/server versions, artifact hashes,
timestamps, test results and sanitized evidence. No gate passes from source
inspection alone.

| Gate | Exercise | Pass condition |
| --- | --- | --- |
| Isolation | Run with no RustDesk, an existing stopped installation, an active installation and an existing portable instance; repeat after elevation. | Support config, logs, IPC and portable helper state are session-scoped. Baseline installation/settings remain unchanged. |
| Endpoint identity | Obtain ID programmatically, register, restart support instance. | ID corresponds to the correct instance; stale endpoint registration cannot replace the active generation. |
| Technician launch | Launch from a Halo-equivalent browser action using URI and supported CLI candidates. | Browser dispatch and client connection work without displaying IDs/passwords; wrong workstation is rejected. |
| Consent | Accept, deny, dismiss and time out incoming requests; try automatic/password access. | Every control connection requires explicit local approval. Denial grants no access. |
| Controller authorization | Connect as assigned, unassigned and spoofed assigned IDs; replay a grant. | Only authenticated, authorized technician/workstation with a live session grant can reach consent. |
| Elevation | Begin unelevated; request elevation separately; accept/deny UAC; test elevated apps and secure desktop. | Requested, denied and completed are distinguishable. Successful UAC yields elevated control; denial preserves standard control. |
| Telemetry | Repeat connect/disconnect/elevation with reconnect and launcher restart. | Stable, ordered, deduplicated events distinguish approval, connection and effective elevation. Heartbeats never stand in for connection duration. |
| Policy | Enable/disable file transfer and clipboard independently; attempt alternate connection modes and policy modification. | Disabled features remain inaccessible, including separate transfer sessions. Audit permission state without content. |
| Termination | Revoke, expire, close launcher, lose control-plane network, kill launcher and reboot. | New connections stop; session-owned helper/processes terminate within the defined bound; no unattended credentials/service remain. |
| Preservation | Compare files/settings/services/process ownership before and after every case. | Cleanup targets only resources created by this session, including elevated children. Existing RustDesk remains usable. |

Decide pass/fail and the smallest patch per failed gate. Windows customer
consent must be exercised by an interactive user; a SYSTEM PowerShell probe
cannot supply that proof. Do not productize an experiment because it compiles.

## Proposed security and lifecycle contracts

These are implementation proposals, pending spike results and review:

- Authenticate Halo requests and establish the actual human actor. A submitted
  `requested_by_agent_id` is data, never proof. Bind Halo tenant/customer/ticket,
  Bifrost principal and approved workstation; fail closed on stale mappings.
- Use a high-entropy invitation capability with rate limits, generic invalid
  responses, no-store and no-referrer behavior. A six-character example slug
  must not be the sole bearer authorization for endpoint bootstrap.
- Consume bootstrap once using an atomic exchange for a session/endpoint-scoped
  credential. Re-download/recovery requires explicit reissuance; terminal
  sessions never reissue. Do not distribute general Bifrost API credentials.
- Bind connection authorization to session ID, endpoint generation, approved
  workstation proof, policy version, short expiry and single-use nonce. Check
  it at the receiving transport boundary before customer consent. Determine
  the actual handshake binding during the spike; a signed token that can be
  copied to another connection is insufficient.
- Keep invitation expiry, active hard deadline, reconnect grace and endpoint
  lease expiry separate. Invitation expiry can preserve an existing active
  connection but forbids new authorization. Define reconnect eligibility
  explicitly. Offline endpoints enforce their local lease deadline.
- Revoke desired access immediately; record cleanup acknowledgement separately.
  Do not report cleanup as complete merely because a database row was revoked.
  Terminal transitions fence credentials, grants and endpoint generations.
- Store append-only events with actor/source, server receipt time, bounded
  endpoint timestamp, event ID and sequence. Use durable deduplicated Halo
  writeback; calculate duration from connection intervals. Heartbeat loss means
  offline/unknown, never proof of customer approval or elevation.
- Keep elevation state separate from connection state. Denied elevation must
  not end a valid standard session. Never capture clipboard, keystrokes,
  screens, credentials or file contents in telemetry.

## Minimal patch boundary, if required

Keep RustDesk separate and AGPL. First validate stock behavior. Patch only gaps:
an explicit validated configuration root with isolated IPC/helper namespaces;
propagation across elevated children; authenticated per-connection grant
validation while preserving click approval; structured local lifecycle events;
and session-owned termination. A Go parent process alone cannot guarantee
cleanup of an orphaned SYSTEM helper or enforce incoming transport auth.

Use authenticated local IPC with restricted ACLs. Validate elevated paths and
artifacts against substitution/reparse attacks. Never promote an arbitrary
user-writable path into SYSTEM execution. Elevation events must describe
observed outcomes rather than translating process-launch success into consent.

## Delivery after the spike passes

1. Freeze the tested transport contract, artifacts and failure behavior. Record
   exact patch/source/build provenance and source distribution for patched clients.
2. Implement Bifrost session/policy/event persistence, scoped capability auth,
   actor/workstation mapping and atomic transitions. Add thin handlers, canonical
   Pydantic contracts and applicable public SDK/CLI/MCP surfaces. Review migration,
   tenant isolation and audit boundaries. Reuse PlatformJob for durable platform
   operations rather than adding a second background-job framework.
3. Implement the Go customer launcher and required technician launch companion.
   Pin mirrored artifacts, verify integrity/signature before execution, maintain
   a bounded lease, emit typed events and perform ownership-scoped cleanup.
   Resolve how one downloaded EXE obtains its invitation safely before release.
4. Build the customer landing/download path and Halo sealed Solution in their
   proper repositories. Persist UX/history fields only. Keep transport credentials
   out of Halo; translate events into bounded updates and one final summary.
5. Exercise the real worktree app on the dedicated Bifrost Linux test VM and the
   selected Windows pair. Verify success, denial, replay, cross-tenant access,
   control-plane outage and revocation. Run scoped `./test.sh`, API quality and
   applicable client checks; regenerate OpenAPI types against the live worktree API.
6. Run the clean signed-candidate `./test.sh pre-pr` gate before PR publication.
   Preserve CI/review/merge gates, use each repository's deployment procedure and
   verify installed runtime separately. Clean up task-created debug resources.

All 20 acceptance criteria from the original brief remain release requirements.
The highest-risk criteria are assigned-controller authentication, independent
elevation consent, cleanup after abnormal exit and coexistence with installed
RustDesk. A source-only investigation satisfies none of their runtime gates.

## Azure hosting proposal

Read-only inventory on 2026-10-05 identified subscription `Microsoft Azure
Sponsorship` (`a1d63b24-1202-4bfa-9086-cf32d1d352fc`). It contains one standalone
Linux VM, `vm-mtg-bifrost-poc-app-01`, in `rg-mtg-bifrost-poc-core-centralus`,
size `Standard_F1as_v7`, running with Standard public IP `20.9.81.122`.
No Container Instances groups were present. This does not assert absence of
workloads on other compute. The existing VM has no RustDesk NSG ingress rules;
repository guidance identifies it as the retained private CI routing host.
Guest processes/capacity were not inspected. Reuse would require further host
evidence and explicit firewall/service scope.

Recommend a separate temporary VM. This keeps transport experiments independent
of the private CI route. The proposed resource boundary is:

| Item | Proposed value |
| --- | --- |
| Environment | Disposable RustDesk Quick Support spike, not production |
| Region | Central US, subject to subscription SKU availability |
| Resource group | `rg-mtg-quick-support-spike-centralus` |
| VM | `vm-mtg-quick-support-spike-01`, smallest available x86 Linux burstable SKU suitable for the two server processes |
| OS/storage | Supported Ubuntu LTS Gen2; 32 GiB Standard SSD OS disk |
| Network | Dedicated VNet/subnet/NSG/NIC and regional Standard static IPv4 |
| Server processes | Pinned OSS `hbbs` and `hbbr` containers; persistent shared server-key/data directory |
| Transport ingress | TCP 21115–21117; UDP 21116 |
| Management | Key-only SSH restricted to approved operator source addresses |
| Excluded ports | Pro console 21114 and web-client ports 21118–21119 |
| Ownership | MTG Quick Support spike; owner, expiry and teardown tags |
| Teardown | Stop clients, preserve sanitized evidence, then authorized removal of only the spike group |

The [RustDesk OSS Docker guide](https://rustdesk.com/docs/en/self-host/rustdesk-server-oss/docker/)
defines these native-client ports. The
[Azure Container Apps ingress contract](https://learn.microsoft.com/en-us/azure/container-apps/ingress-overview)
supports HTTP and TCP, so it does not supply the required UDP listener. A Linux
VM is the straightforward candidate. Container Instances supports UDP in its
schema but would need separate persistence, restart/IP and mixed-protocol
testing; it is not a proven replacement here.

Azure Retail Prices API, USD Central US, retrieved 2026-10-05:

| Meter | Retail rate | Qualification |
| --- | --- | --- |
| Linux `Standard_B1ms` | $0.025/hour | Azure SKU readback says `NotAvailableForSubscription` in Central US; excluded from provisioning. |
| Linux `Standard_B2ats_v2`, regular on-demand | $0.0106/hour | Availability still being checked; confirm memory suitability before selection. |
| E4 LRS Standard SSD | $2.40/month | Disk transactions are additional. |
| Regional Standard static IPv4 | $0.005/hour | Remains billable while allocated. |

For the B2ats candidate, 730 hours of compute/IP plus the disk would be
**$13.79/month**, before disk transactions, relay egress, monitoring, tax and
subscription-specific credits/discounts. This is a conditional retail estimate,
not a bill, approved budget, or capacity guarantee. Deallocating compute does
not remove disk/public-IP charges. Relay bandwidth depends on actual support
usage and is not priced by this source-only spike.

Before apply: author isolated Bicep and Ansible in `bifrost-infra`, resolve server
image digest/provenance, confirm quota/image availability and operator SSH
source, run Bicep build/what-if and guest configuration checks, then obtain
authorization for the exact candidate under the infra operating policy. Do not
reuse the old VM Compose or retired AKS deployment lanes. After apply, read back
ports/processes/key identity and exercise direct and relay transport from the
selected Windows endpoints. No Azure write operation has occurred.
