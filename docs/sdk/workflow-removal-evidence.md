# Offline workflow removal evidence

Delivery-review v2 keeps recipe schema `bifrost.solution-workflow-delivery/v1`
unchanged. Both public reviewers accept keyword-only `workflow_removal_evidence`
and `workflow_removal_context`. Without evidence, removals still fail with the
original reconciliation error. The legacy source adapter cannot use this seam.

`bifrost.workflow_removal_evidence` owns the strict v1 receipt models,
`removal_binding`, `export_workflow_removal_evidence`, and offline verification.
The reviewer recomputes bindings from both complete recipes and their exact
source/resource bytes. Recipes are normalized through `ReviewedWorkflowRecipe`
then encoded as sorted, compact ASCII JSON; content maps encode every runtime
path and its SHA-256 byte digest. SHA-256 digests use the `sha256:` prefix.
Candidate content, rather than the commit containing the receipt, avoids a
self-referential commit hash. The base SHA is the full 40-character Git SHA.

The consumer must supply independently reviewed Solution identity, canonical
HTTPS instance origin, repository-relative recipe path, exact Git base SHA and
pinned trusted producer identities/public keys. It must obtain recipe and
source/resource inputs from the exact candidate and base Git blobs. The receipt
cannot select its target or establish its own trust. `max_age_seconds` defaults
to 900 and cannot exceed 3600; verification uses the actual UTC clock. Producer
issuer, key ID and build digest must match exactly one configured trust entry.
Do not load trust policy from an untrusted candidate receipt.

The narrow producer export seam accepts already collected post-reconciliation
observations and a raw Ed25519 private key, signs canonical unsigned receipt
bytes, and returns JSON with a base64 signature. There is no shipped key,
automatic issuer enrollment, live scanner, CLI command, or network access in
this seam. An authoritative observer service must own its signing key and scans;
calling the export function on operator-authored JSON does not establish live
reconciliation. Production integration of that service and trust provisioning
remain required before real receipts can be consumed.

Each removed ID requires one observation containing a single authoritative
snapshot/revision and four exhausted inventories: callers/dependencies, event
sources, subscriptions, and schedules. Inventories must include disabled objects,
be complete, exhaust pagination, and report no remaining or unresolved/dynamic
references. All inventory revisions must match the observation revision. Missing
inventories, extra/duplicate/missing workflow IDs, future/stale observations,
expired receipts, malformed models, mismatched content or target, unknown
producer builds/keys, and invalid signatures fail closed. Producers must classify
unresolvable callers as unresolved rather than omit them from an empty scan.

The result exposes `workflow_removal_evidence_verified`, `removed_workflow_ids`
and `workflow_removal_evidence_digest` (digest of the complete signed receipt).
`live_state_verified` and `runtime_verified` remain false: this verifies a bound
historical observation, not current live state or deployment authorization.
Retained-workflow identity, dependency closure, access and parameter checks still
apply. Evidence supplied without an actual removal baseline is rejected.

SDK publication, workspace SDK/CI-image pins, exact Git receipt lookup and the
workspace contract migration are subsequent steps; this platform change does
not perform them. Release still requires fresh authoritative reconciliation and
all existing publication gates.
