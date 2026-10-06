# Reviewed workflow revisions for adopted Solutions

The superuser operator API supports compatible registration additions and
updates for an already adopted immutable Solution. It uses the same recipe
compiler, registration checks and atomic activation service as protected Git
delivery. This path supports reviewed global installations whose scope is not
covered by the configured Git producer. Git OIDC and source-declaration tokens
do not authorize this operator API.

Use the `/{deployment_id}/workflow-revision/candidate`, `preflight` and `activate`
endpoints below `/api/solutions/{solution_id}/deployments`. The candidate request
includes the complete `reviewed_recipe`, Python `files`, bounded JSON/PowerShell
`resources`, exact `source_commit_sha`, expected active deployment ID and manifest
hash. Derive the bytes from reviewed protected Git. The operator attests this
provenance; these endpoints do not independently authenticate a Git producer.
Initial ownership adoption still uses the guarded Live handoff.

For an install with existing owned tables, offline `review_workflow_recipe` and
`review_solution_recipe` accept `owned_table_ids`, taken from the exact current
Solution inventory. This is review context, not a resource grant or live proof;
the result retains `live_state_verified=false`. Leave `shared_tables` empty when
no Root binding exists. Native candidate and preflight derive table capability
from actual Solution ownership independently. Read back the same table UUIDs,
metadata, policies and ownership before and after activation; this workflow
adapter does not deploy tables or rewrite their data.

Existing sealed legacy descriptors remain in the successor's immutable evidence
and registry projection. Existing registry timeouts are retained while the new
immutable runtime bounds remain enforced. Reviewed effect declarations can
change through this adapter; the body-only source adapter still rejects them.

Staging creates immutable candidate objects without changing registrations or
the installed pointer. Preflight independently reads the stored bytes, current
registration and trigger snapshots, reviewed bindings and active base. Activation
requires that fresh evidence ID, repeats the checks under the Solution write lock
and commits compatible registrations and the pointer together. After an ambiguous
response, inspect the pointer and candidate before another write.

Trigger snapshots are observed during each check. Root trigger writers do not
share the Solution lock, so verify them independently after activation and
reconcile concurrent changes before accepting the delivery evidence. This
adapter preserves existing workflow identity and compatible parameters and does
not create or change triggers.

Existing workflows retain UUID, path/function identity, organization, access,
endpoint, execution mode, cache and retry settings. Removal and incompatible
parameter changes fail. New workflows require unused UUIDs, the installation's
scope, existing reviewed roles, role-based access and disabled endpoints. Check
active and inactive production identities before minting a new portable identity.

After delivery, verify the installed source hashes, complete registration rows,
dependency bindings and appropriate runtime evidence. Activation does not execute
workflow bodies, grant integration consent, prove vendor behavior or close source
obligations. Already accepted executions retain their immutable runtime pins.
Restore source through a reviewed successor; generic pointer rollback remains
unconfigured.

## Execution-scoped Root table bindings

A global Solution can bind an explicitly reviewed organization Root table.
The immutable binding names the exact table UUID, organization, metadata hash
and read or read-write access. Its organization must match the durable execution
organization and the SDK request scope. A scoped Solution still requires its
organization to match the binding. A global table binding continues to mean
an actual global Root table; null is never an organization wildcard.

The existing signed active-attempt check, immutable deployment evidence,
metadata lock, table ownership/schema/inline-policy hash and document policy
checks remain required. This permits global workflow registrations to keep
their UUID and global scope while using their caller's explicitly bound
organization data. It grants no access from an execution in another organization.

Review the exact organization binding before activation. Prove success and
foreign-execution rejection with synthetic data on the canary, then reread the
production table metadata and workflow registrations before handoff. Candidate
validation alone does not prove runtime access. An older runtime rejects this
global-Solution organization binding; rollback must retain a compatible image
or restore workflow ownership before reverting the image.

### Aliases with more than one reviewed scope

When the same Root table name exists globally and in an execution organization,
retain both exact contracts. A binding can carry `additional_scopes`, each with
its own table UUID, organization, metadata hash and read or read-write access.
Preview each table's metadata independently. The complete install permits at
most 100 table grants; duplicate scopes for one alias and reused UUIDs fail.

Default name lookup selects the signed execution organization's reviewed table,
then its reviewed Global fallback. Explicit `scope="global"` selects only the
Global table. A UUID selects that exact reviewed table and still requires the
execution's own-or-Global scope. A foreign organization selector or UUID fails.
The server checks the selected table's write grant and locks its metadata.
Candidate, preflight and activation validate every scope's metadata, including
variants that the current execution will not use.

This is an additive contract. Omitted or empty `additional_scopes` serialize
identically to existing single-table bindings, preserving accepted deployment
hashes. Older runtimes reject a nonempty extension; keep a compatible image
while any accepted execution references it. Prove both default and explicit
Global reads with distinct synthetic rows, foreign UUID rejection, independent
durable execution/attempt pins and unchanged Root ownership before enabling it.
