# Guarded handoff from Workspace Live to a Solution

The active Workspace release owns source bytes and registration bindings for
loose workflows. Setting `Workflow.solution_id` through ordinary Solution
capture would make the next execution stop pinning that release immediately.
The current capture guard therefore remains in force.

`POST /api/solutions/{solution_id}/deployments/{deployment_id}/live-handoff/preflight`
is a read-only inspection for a prepared immutable Solution deployment. An
administrator supplies the expected Live release row, release ID, artifact ID,
governed manifest ID, registration fingerprint, Solution base deployment ID,
and workflow UUIDs. The service checks that:

- the Solution is active, configured, and sealed from mutable Workspace source;
- the candidate is ready on the expected Solution base and its DB manifest,
  stored manifest, source archive, and runtime files agree;
- every selected UUID is still loose, active, in the Solution scope, bound to
  the expected Live registration, and present with the same source, runtime
  metadata, and Live duration and output limits in the candidate;
- the candidate contains exactly the selected UUIDs and workflows already
  installed in that Solution;
- every candidate source path is governed by Live and its stored bytes equal
  the verified Live bytes. The source archive contains exactly those paths.

The response binds this inspection to a digest of the release, registration,
candidate, Solution base, selected UUIDs, and source hashes. It is review
evidence, not an activation token. Live and Solution state must be rechecked
under their write locks before any ownership change. The endpoint does not
change registry rows, runtime pointers, event subscriptions, or source-release
obligations.

Dispatch rechecks Solution ownership if it changes between the initial Solution
lookup and the Live lookup. A candidate copied from Live carries the bounded
timeout and the full Live runtime bounds in its immutable workflow definition.
The Solution queue pin carries those bounds to the worker; the worker enforces
the duration and output limits. Existing Solution deployments without bounds
retain their current behavior.

The current immutable deployment API registers references to an already
stored candidate. It does not build or upload that candidate, and its generic
activation hooks are not configured. A later stage must construct the source
closure from verified Live bytes, implement atomic pointer and owner movement
with a rollback path, and prove queued and running execution behavior before
production uses the handoff. Do not use direct SQL ownership updates or the
generic Solution capture route as a substitute.
