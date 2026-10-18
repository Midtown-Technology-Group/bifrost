# MTG device and promotion grants in the upstream RBAC merge

This bridge accompanies upstream target `9e104ea1a9be74726a30fd2e9189f85a69c0e5c0`.
It preserves that target's nonadministrator assignment ceiling: delegated
assignment authority can change only nonbuiltin roles with zero permission
rows, on unprotected targets. The later upstream policy is outside this merge.

## Permission contract

The only legacy JSON translations are JSON boolean `true` values:

| Legacy key | Permission | Authority |
| --- | --- | --- |
| `can_manage_config` | `devices.readwrite` | Registry, enrollment/device identities, key metadata/revocation and freshness. |
| `can_execute_devices` | `devices.execute` | Device jobs, cancellation, results/logs and script bodies. |
| `can_promote_agent` | `agents.promote` | Promote an ordinary caller's own private agent in their current home organization. |

All three permissions are privileged. None is seeded into User, Platform
Operator or Secrets Reader, including through authenticated-route derivation.
Platform Admin retains its current stored wildcard. General identity grant
editing does not expose these permissions to delegated role editors.

Ordinary bridge authority requires an effective organization boundary at the
holder's current home organization. Foreign, platform and managed-organization
boundaries confer no ordinary bridge authority. A missing home does not mean
platform scope. Permission checks load current database assignments rather than
trust token role names, legacy administrator bits or cached role membership.

Usable control-key creation and rotation require `devices.readwrite` and
`devices.execute` at the same resolved target organization. Separate roles may
supply the two grants; different organizations may not. Device allow-lists stay
nonempty and organization-bound. Enrollment and device-key authentication remain
separate existing credential lanes.

Promotion also preserves the Solution ownership guard, including for Admins.
The Admin wildcard exception bypasses the ordinary owner/home/grant restrictions;
it does not authorize direct mutation of Solution-managed source.

## Grant maintenance and protected principals

`GET`/`PUT /api/roles/{role_id}/bridge-grants` accept only the three typed grants.
The editor requires a current real Platform Admin and rejects execution,
service and embed credentials. Writes lock the role and require both the
previous grant set and its version. Unrelated permission rows remain intact.
The shared row service emits audit records and invalidates additional/base-role
holder caches. Assignment, base-role, home-organization and grant writers share
a transaction fence and recheck current actor/holder authority under that fence.

Bridge-protected account support, lifecycle and assignment mutations retain an
Admin-only boundary. Bridge-target impersonation and managed-identity delegation
also check current actual Admin authority; transport-superuser claims alone do
not establish it. Existing nonbridge credential behavior remains separate.

Legacy buffered role and user-role writes lack an authoritative caller. They
are refused before flushing any buffered changes; the supported SDK uses the
canonical authenticated API. There is no JSON writer or fallback reader.

## Knowledge namespace sharing

`knowledge_namespace_roles` remains a namespace selector with its existing
row IDs, CRUD/counts/UI, global NULL semantics and `NULLS NOT DISTINCT`
uniqueness. Selection intersects roles effective at the caller's home
organization, including a custom base role, with existing home/global namespace
assignment scope. It creates no `knowledge.read` rows and is not converted into
a universal content-read ACL. See [namespace behavior](knowledge-namespace-rbac-bridge.md).

## Migration and rollback boundary

The unchanged fork head leads through the retained incoming chain to:

```text
20260928_audit_op_surface
 -> 20261017_mtg_rbac_preflight
 -> 20260929_r2b_roles
 -> 20261017_mtg_device_grant_bridge
 -> 20260929_model_catalog
 -> remaining incoming revisions
 -> 20261012_oauth_ownership
```

Preflight locks the source authority tables before R2b DDL. It rejects every
nonobject JSON value, unknown key (even false), nonboolean value, reserved
builtin UUID collision, orphan reference, unexpected row/boundary schema and
ordinary device-grant holder without a home. Empty objects are valid. The
bridge checks exact permission/boundary sets and unchanged original identity,
membership and namespace state. The segment must share one transaction.

The earlier incoming system-account guard also refuses legacy memberships or
a legacy Admin flag on the canonical system account under the same write
fence. It never deletes those memberships or guesses an authority conversion.
The supported system-account shape has neither and upgrades without an
additional role or boundary.

The physical `roles.permissions` column is retained, unmapped and frozen
against normal runtime writes for one release. A private migration-owned
rollback record retains original JSON, fingerprints and created rows. Public
and unexpected inherited ACL grants are refused. Normal DML cannot rewrite the
archive. The existing database owner/superuser remains a trusted DDL principal;
this bridge does not establish a separate runtime/migrator database identity.

Only immediate unchanged bridge-segment rollback is supported. An authority
change epoch also catches revoke/regrant and conservatively blocks rollback
after attempted authority writes. Later incoming autocommit/forward-only
revisions require consistent-backup restoration with matching code, rather than
code-only rollback to a legacy JSON reader. Contraction is a separate revision.

## Release requirements

Source inspection and restricted live inventory are not migration acceptance.
Rehearse the full graph from pristine and schema-faithful fork-head databases,
the invalid-input/rollback matrix, boundary and revocation behavior, builtin
seeds and namespace parity in the authorized Linux Docker/CI lane. Regenerate
contracts, build the client, and satisfy the exact final candidate's normal
pre-PR, review and CI gates.

Run `./test.sh rbac-backup-rehearsal` from a clean committed candidate in the
Linux Docker lane. It uses matching PostgreSQL 16 dump/restore tools and
synthetic databases for the clean fork head, historical delivery-column stamp,
and withdrawn Builder residue. After an interrupted forward-only upgrade it
restores the original archive, exercises actual read-only readers from frozen
`5085cc467` source and dependencies, and upgrades to the candidate head. This
proves imported-reader compatibility, not complete old-service startup or a
deployment rollback. Comprehensive CI requires the rehearsal in E2E shard 1.
Retain the restricted `rbac-backup-*/proof.json` and per-variant receipts from
the worktree's test results directory; raw dumps and private snapshots stay out
of CI artifacts.

The SDK/server contract advances to version 14. Before runtime cutover, the
documented release lane must pause admissions/triggers, drain old executions
and their buffers, and freeze competing migrators. Deploy the accepted
same-source API and workers. Never replay old RBAC buffers, migrate with stock
R2b, use stale JSON as a fallback, or restore a snapshot over later revocations.
Merge and artifact publication alone do not establish deployed behavior.
