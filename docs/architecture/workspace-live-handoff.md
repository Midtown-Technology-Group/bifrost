# Guarded handoff from Workspace Live to a Solution

The active Workspace release owns source bytes and registration bindings for
loose workflows. Setting `Workflow.solution_id` through ordinary Solution
capture would make the next execution stop pinning that release immediately.
The current capture guard therefore remains in force.

`POST /api/solutions/{solution_id}/deployments/{deployment_id}/live-handoff/candidate`
stages a candidate for a new, configured, disconnected Solution with no
installed workflows. An administrator supplies a fresh deployment ID, the
expected Live release row, release ID, artifact ID, governed manifest ID,
registration fingerprint, and the workflow UUIDs. The server reads the verified
Live bytes, computes the static import and workflow-reference closure, rejects
unresolved or dynamic edges, and writes an exact source archive and runtime
files with create-only object keys. A retry accepts only identical bytes. The
builder registers the candidate as ready and returns the preflight evidence.
It does not move ownership or a runtime pointer. An orphaned immutable object
can remain if staging fails after an object write; it cannot affect execution.

`POST /api/solutions/{solution_id}/deployments/{deployment_id}/live-handoff/preflight`
is a read-only inspection of that immutable Solution deployment. The service
checks that:

- the Solution is active, configured, and sealed from mutable Workspace source;
- the candidate is ready on the expected Solution base and its DB manifest,
  stored manifest, source archive, and runtime files agree;
- every selected UUID is still loose, active, in the Solution scope, bound to
  the expected Live registration, and present with the same source, runtime
  metadata, and Live duration and output limits in the candidate;
- the candidate contains exactly the selected UUIDs and workflows already
  installed in that Solution;
- the candidate source paths equal the complete static dependency closure of
  the selected workflows, every path is governed by Live, and its stored bytes
  equal the verified Live bytes. The source archive contains exactly those paths.

The response binds this inspection to a digest of the release, registration,
candidate, Solution base, selected UUIDs, and source hashes. The endpoint does
not change registry rows, runtime pointers, event subscriptions, or
source-release obligations.

Dispatch rechecks Solution ownership if it changes between the initial Solution
lookup and the Live lookup. A candidate copied from Live carries the bounded
timeout and the full Live runtime bounds in its immutable workflow definition.
The Solution queue pin carries those bounds to the worker; the worker enforces
the duration and output limits. Existing Solution deployments without bounds
retain their current behavior.

`POST /api/solutions/{solution_id}/deployments/{deployment_id}/live-handoff/activate`
accepts those exact expectations plus the preflight evidence ID. It takes the
Solution write lock and the global Live transaction lock, locks the Solution
and selected workflow rows, and repeats preflight against the stored runtime
bytes. The transaction compares the empty Solution pointer, then marks the
candidate active and transfers the selected UUIDs to the Solution together.
A failed check rolls the entire transaction back. Dispatch before the commit
can keep a durable Live pin; dispatch after the commit gets the Solution
deployment pin. The selected UUIDs, roles, endpoint settings, event
subscriptions, and API callers remain the same registry identities.

`POST /api/solutions/{solution_id}/deployments/{deployment_id}/live-handoff/rollback`
requires the active pointer and original handoff evidence. It verifies that
the same Live release is still active, that the Solution owns exactly the
selected UUIDs, and that the Live bytes and candidate runtime still match.
One transaction clears the pointer, restores the loose owners, and marks the
deployment superseded. Already queued Solution executions retain their
immutable deployment pin; new dispatch pins Live again. If the Solution write
lock is lost near commit, the API reports an ambiguous outcome and requires
pointer and owner readback before any retry.

## Source revisions after handoff

`POST /api/solutions/{solution_id}/deployments/{deployment_id}/source-revision/candidate`
accepts the active deployment ID and manifest hash, a Git commit SHA, and the
exact Python file closure with base64 encoded contents. This is a source-only
path for adopted workflow identities. It keeps each workflow's registration,
runtime bounds, and UUID fixed. The server rejects paths outside the closure,
dynamic or unresolved imports, changed workflow identities or metadata, and
files larger than the bounded archive. It stages a new source archive, runtime
files, manifest, and ready deployment at create-only keys. The old pointer
continues serving production.

The staging response and `/source-revision/preflight` include the Git commit,
source hashes, workflow UUIDs, active subscription UUIDs, and an evidence ID.
Review the exact source bytes against that commit, the changes to behavior,
callers and active triggers, the runtime bounds, and any source-release
obligation before activating. The API records the asserted Git SHA but does
not fetch Git to prove the assertion. A merge or stage call does not activate
production code.

`/source-revision/activate` takes the Solution write lock and workflow row
locks, repeats the stored byte, import closure, registration, trigger, and
pointer checks, then compares the reviewed evidence ID. One database
transaction moves the active pointer and marks the old deployment superseded.
Queued work retains its old immutable pin. To restore old source, stage its
exact archive as a new candidate against the current pointer and review and
activate it the same way. This path keeps registration metadata fixed; adding
or renaming workflows requires a separately reviewed install path.

The generic immutable deployment API still registers references to already
stored objects, and its activation hooks are unconfigured. Legacy Solution
full-replace deploy refuses an install with an active immutable pointer: it
could otherwise change registration rows while execution kept using the old
closure. Before production uses this handoff, the current Live history,
trigger, dependency, and obligation evidence must be reconciled. Do not use
direct SQL ownership updates or generic Solution capture as a substitute.
