# Retained Live bindings after Solution handoff

A completed guarded handoff preserves the old Live artifact and moves each
workflow UUID to an immutable Solution installation. The next loose Workspace
preview must omit that installation's bindings without reclaiming its workflows.

`LiveHandoffReadback` proves an omission using:

- The exact inherited workflow UUID, path, function and organization scope.
- An active Solution pointer in `deployment-v1`, finalized immutable deployment
  hashes, storage identity, current registrations and exposure controls.
- Archive and runtime byte readback, resource contracts and dependency closure.
  Historical artifacts retain their immutable byte proof; current shared-table
  and Root-file contracts are checked for the active runtime and its pinned
  dependencies. Reviewed replacements need not keep obsolete origin contracts live.
- Activated reviewed Source/workflow revision lineage back to the original
  guarded handoff. Missing parents, cycles and unrelated installs fail closed.
- The original Live row/release identity, source hashes, bundle hash and recomputed
  preflight evidence digest. The receipt alone is insufficient.

Two provenance variants keep that proof honest for transitions that no receipt
can describe (#1122):

- A receipt coherently bound to a predecessor Live release (both release
  identity fields differ together) still proves the handoff while its
  coverage, verified source paths and source hashes match the current
  release's governed bytes. One mismatched identity field is tampering and
  fails closed, as do initial-install, adoption and detached-revision roots.
- When the active deployment chain carries no reviewed lineage marker at all,
  the key is verified directly against that closure-validated active
  deployment: existence, active pointer, storage identity, runtime bytes,
  contracts, dependencies, registration set, inherited identity and exposure.
  A solution-managed key with no active covering deployment, an unknown key,
  or any drift in those checks still raises `UnprovenLiveHandoff`.

The original governed source files stay in the next loose snapshot because
remaining loose workflows may depend on their helpers. Only certified workflow
bindings leave its registration set. Solution updates use reviewed Solution
delivery; selecting a handed-off entry for loose delivery is refused.

Activation repeats registration and handoff proof under the existing global
release fence and native Solution row locks. A prepared candidate cannot reclaim
an omitted binding. Existing loose registration and complete-cohort checks remain.

## Cutover verification

Publish and deploy the reviewed platform repair through the repository's protected
image and deployment lanes. Record the authoritative API/worker/client image
identities, SDK tuple and schema readback before asking a workflow owner to refresh
its preview. A merged PR or local test does not establish deployed behavior.

The Microsoft writer/install/mapping sequence belongs to the GDAP cleanup thread.
Its owner refreshes workspace main, pointers, dependency bytes and preview receipts
against the verified runtime before prepare/CAS activation and independent runtime
readback. This repair changes no Solution pointer, registration ownership, Source
obligation or Live retirement state. Retiring Live still requires complete live
consumer and obligation evidence.

Rollback the platform image through its reviewed lane if readback fails. Keep
Solution ownership and Live history intact; do not reassign a Solution workflow or
manually settle Source debt to make preview pass.
