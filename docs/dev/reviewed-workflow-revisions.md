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
