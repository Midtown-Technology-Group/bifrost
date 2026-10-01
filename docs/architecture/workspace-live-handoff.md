# Guarded handoff from Workspace Live to a Solution

The active Workspace release owns source bytes and registration bindings for
loose workflows. Setting `Workflow.solution_id` through ordinary Solution
capture would make the next execution stop pinning that release immediately.
The current capture guard therefore remains in force.

`POST /api/solutions/{solution_id}/deployments/{deployment_id}/live-handoff/candidate`
stages a candidate for a new, configured, disconnected Solution with no
installed entities, schema declarations, or pending captures. An administrator
supplies a fresh deployment ID, the
expected Live release row, release ID, artifact ID, governed manifest ID,
registration fingerprint, and the workflow UUIDs. The server reads the verified
Live bytes, computes the static import and workflow-reference closure, rejects
unresolved or dynamic edges, and writes an exact source archive and runtime
files with create-only object keys. A retry accepts only identical bytes. The
builder registers the candidate as ready and returns the preflight evidence.
It does not move ownership or a runtime pointer. An orphaned immutable object
can remain if staging fails after an object write; it cannot affect execution.

The workflow-only candidate has no installed tables or file locations. Its
complete Python closure rejects imports of `bifrost.tables` unless the reviewed
candidate declares shared table bindings. Imports of `bifrost.files` remain
unsupported, including aliases and imports in helpers. Bare `import bifrost`
and wildcard SDK imports also fail because they conceal which SDK resources
the code uses. Import supported SDK APIs explicitly. The same check applies
during preflight and source revision, before activation. This conservative
check is an unsupported-resource boundary, not proof of arbitrary Python's
resource behavior. Review runtime dependencies and external effects separately.

## Reviewed shared Root tables

`POST /api/solutions/{solution_id}/deployments/shared-tables/preview` accepts
exact table UUIDs and returns read-only bindings by table name. It reads table
metadata, never documents. It supports global Root tables and Root tables in
the target Solution's exact organization, with inline row policies. Organization
bindings carry an explicit `organization_id`; a Global installation can use
one only for a signed execution in that organization. A named policy reference requires a separate immutable policy
contract and is rejected. The operator may explicitly request `read-write`
access after reviewing the callers and row policies.

Supply these bindings as `shared_tables` in candidate and preflight requests.
The immutable manifest, resolution map, bundle and inspection evidence bind
each table's UUID, name, scope, schema, normalized inline policies and access.
Candidate creation, preflight, activation, rollback and source revision reject
changed metadata. Source revisions preserve the exact bindings.

Document access requires a signed engine token for an active execution attempt.
The server validates the execution's durable deployment pin and evidence,
organization and signed Solution identity. It resolves bindings from that pin,
so already queued work keeps its reviewed contract after a successor or
rollback. Ordinary users and a caller-supplied Solution query do not receive
this grant. The existing organization and row policy checks still apply.
Read bindings cannot authorize writes. A shared metadata lock prevents a table
ownership or schema change from racing a checked document transaction.
Organization bindings require the signed execution's exact organization and
the installation's organization when the installation is scoped. An explicit
request for another organization fails. The offline recipe compiler requires
every workflow to be Global or in the table's organization;
live checks verify the installation and current table metadata.

An alias can carry bounded `additional_scopes` with independently reviewed
UUIDs, metadata hashes and access grants. Default lookup follows own organization
then Global; explicit Global lookup excludes organization variants. Every grant
is checked during staging and activation; runtime selects only a grant available
to the durable execution. Existing single-table bindings retain their canonical
bytes. See [reviewed workflow revisions](../dev/reviewed-workflow-revisions.md#aliases-with-more-than-one-reviewed-scope)
for the contract and required runtime proof.

Bindings preserve Root ownership, table UUIDs and documents. They allow Meraki
and existing Root callers to share their current tables without broad outbound
access. They do not grant table metadata mutation or file access. Before a
production handoff, verify actual table resolution and row policy behavior in
an isolated rehearsal, and review every caller and external effect.

## Handoff checks and activation

`POST /api/solutions/{solution_id}/deployments/{deployment_id}/live-handoff/preflight`
is a read-only inspection of that immutable Solution deployment. The service
checks that:

- the Solution is active, configured, empty, and sealed from mutable Workspace
  source;
- the candidate is ready on the expected Solution base and its DB manifest,
  stored manifest, source archive, and runtime files agree;
- every selected UUID is still loose, active, in the Solution scope, bound to
  the expected Live registration, and present with the same source, runtime
  metadata, and Live duration and output limits in the candidate;
- the candidate contains exactly the selected UUIDs;
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

`GET /api/solutions/{solution_id}/deployments/active` independently reads the
committed active deployment ID and runtime mode. After activation, compare that
pointer with the reviewed candidate, inspect its deployment record, and read
back the selected registry owners and triggers. After rollback, confirm the
empty pointer and loose owners. An activation response alone does not prove the
current installed state.

## Source revisions after handoff

`POST /api/solutions/{solution_id}/deployments/{deployment_id}/source-revision/candidate`
accepts the active deployment ID and manifest hash, a Git commit SHA, and the
exact Python file closure with base64 encoded contents. This is a source-only
path for adopted workflow identities. It keeps each workflow's registration,
runtime bounds, and UUID fixed. The server rejects paths outside the closure,
dynamic or unresolved imports, changed workflow identities, signatures,
decorators or metadata, and files larger than the bounded archive. It stages a
new source archive, runtime files, manifest, and ready deployment at create-only
keys. The old pointer continues serving production.

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
full-replace deploy and ordinary capture refuse an install with an active
immutable pointer. Either operation could change registration rows while
execution kept using the old closure. Capture reads and locks the current
database pointer, even when its caller loaded the Solution before activation.
Before production uses this handoff, the current Live history,
trigger, dependency, and obligation evidence must be reconciled. Do not use
direct SQL ownership updates or generic Solution capture as a substitute.

## Delivery from protected Git

`POST /api/solutions/{solution_id}/deployments/github-source` combines source
staging and activation for an explicitly configured GitHub Actions producer.
It is disabled until `BIFROST_SOLUTION_GIT_DELIVERY_POLICY` supplies the exact
repository name and immutable repository/owner IDs, organization UUID, delivery
workflow path, CI workflow path and ID, and installed Solution UUIDs mapped to
reviewed recipes under `config/solution-delivery/`. Configure this policy only
after reviewing the producer and rehearsing its deployment path.

An existing policy keeps its required `organization_id` as the scope for every
allowlisted install. To mix Global and organization installs, configure the
optional `solution_organization_ids` map with exactly the same UUID keys as
`solutions`. Each value is the install's exact organization UUID, or explicit
`null` for a Global install. Missing, extra and malformed entries are rejected;
an unknown install never resolves to Global. The scope applies to the initial
Solution check, every immutable deployment read, delivery evidence update and
independent pointer readback. A Global deployment uses an `IS NULL` predicate,
not a tenant wildcard. The saved delivery proof records this resolved scope.
This configuration grants no runtime table or file access and changes no
registration scope. Add actual installs only after their handoff and trusted
producer rehearsal pass.

This endpoint accepts a commit SHA, CI run ID and attempt, and artifact digest.
It accepts no uploaded source. The bearer token is a GitHub OIDC token whose
audience binds those values and the Solution UUID. Its claims must name the
configured main-branch workflow at that exact commit. `X-GitHub-Job-Token`
supplies an ephemeral GitHub token for read-only repository and Actions requests.
Neither token grants a Bifrost user role, and neither is stored in a receipt or
deployment. Existing administrator endpoints retain their own authentication.

The server independently verifies the repository identity, successful latest
CI attempt and protected current main. It fetches the complete Git tree, the
reviewed recipe and every regular Python blob, then verifies blob identity,
source hashes and the audience-bound artifact digest. A source-only recipe has exactly
`schema_version`, `solution_id` and `files`; its schema is
`bifrost.solution-source-delivery/v1`. `files` maps runtime paths to Git paths
for the full source closure. Superseded commits cannot activate. The server
rechecks CI and main after staging and before moving the pointer.

Delivery of source-only recipes uses the existing Solution write lock, immutable source artifacts and
source-revision checks. Registration, signatures, decorators, triggers, runtime
bounds and table contracts must still match. New or renamed workflows and
registration metadata changes require the workflow recipe adapter below.
A source-only recipe does not deliver those changes.

### Reviewed registration recipes

The same producer accepts `bifrost.solution-workflow-delivery/v1`. Its full
recipe contains `solution_id`, the complete Python `files` mapping, a complete
`workflows` list and optional `shared_tables`. Each workflow carries its
existing or Solution-CLI-minted UUID, runtime path, function name, organization,
finite runtime bounds and registration controls. The artifact digest binds the
entire reviewed recipe as well as the exact source commit, tree and hashes.
The server compiles names, types, descriptions, tags, effects and parameter
schemas from the carried AST without importing user code. Legacy decorator
`id` values must agree with the recipe. Dynamic declaration/default expressions,
additional wrappers, services and positional-only entrypoints fail explicitly.
Parameter defaults may also reference a single module-level literal scalar
constant defined before the function. Reassigned, imported, conditional,
mutable or computed constants fail; compilation never executes source.

This first registration adapter supports new UUIDs, changed literal argument
defaults, additional optional keyword arguments, descriptions/categories/tags,
reviewed finite bounds and shared-table contract revisions. Existing UUIDs,
paths, function and workflow names, types, org scope, access/roles, endpoints,
execution modes, cache settings and retry policies remain fixed. New workflows
require the install's org scope, role-based access, existing reviewed role IDs
and disabled endpoints. A recipe cannot claim an existing Root or other
Solution registration. It contains no instance API keys or secret values.

Stage and activation verify the exact full dependency closure and current
registration, trigger and pointer evidence. Activation repeats those checks
under native row locks and projects registrations and the pointer in one SQL
transaction. Post-commit readback verifies the complete installed registration
definition, archive and table contracts. New admissions also validate inputs
against the immutable schema selected for that execution, so earlier API
metadata cannot authorize incompatible arguments after a pointer switch.
Only new workflow recipes stamp `parameters_schema_contract` with
`bifrost.workflow-parameters-schema/v1` in the immutable definition. Older
deployments retain their original queue evidence even when they already carry
parameter metadata, so a platform rollout preserves accepted evidence hashes.
Accepted executions resolve their original active/superseded deployment even
if the current registration is inactive. Fresh dispatch still requires an
active registration; Solution status and ownership checks remain mandatory.

A new binding can replace a drifted Root-table metadata hash after the new
exact contract is reviewed. The deploy writer does not change table ownership,
schema, policy or document data. An old execution retains its old binding and
fails closed if that contract no longer matches live metadata.

Removal, renames, breaking input changes, type/scope/auth/endpoint/mode/cache/retry
changes, services and non-workflow resources remain blocked. Issue #984 stays
open for complete live caller/trigger reconciliation, dependency-safe removal,
old nested-call behavior, rollback of registration additions and the actual
GitHub-job/worker rehearsal. Revert compatible source/default/bound changes as
a new protected-main full recipe against the observed pointer. Reverting an
addition would remove a registration and is blocked until that adapter exists.
Neither this first adapter nor repository tests establish production readiness.

The canonical OperationReceipt identifies the producer run and attempt, CI
run and attempt, Solution, commit and digest. Pointer activation and receipt
completion commit in the same database transaction. A completed replay checks
the actual current pointer, source and registration again. An unresolved receipt
is not reclaimed: inspect the receipt and pointer, then use a fresh producer
attempt. The deterministic create-only candidate can resume after staging
without replacing stored artifacts. Queued executions keep their immutable
deployment pins.

The response reports verified source and independent pointer readback, with
`runtime_verified=false`. It does not execute workflows or settle source-release
obligations. Before enabling production delivery, prove the actual GitHub job,
runtime execution, trigger and dependency behavior in the isolated canary.
Production handoff still needs the live history and consumer evidence described
above. Global Live retirement remains a separate live-state decision.

## Immutable source resource reader

The optional `resources` maps in the manifest and resolution document identify
reviewed non-Python source bytes. Each entry pins a canonical path, deployment
object key, SHA-256 and positive size. The two maps must agree. A resource cannot
share a Python source path or point outside
`_solutions/<install>/<deployment>/_resources/`. The bounds are 2 MiB per
resource, 10 MiB total and 256 entries. Empty maps are omitted from canonical
documents, preserving existing deployment and accepted-execution hashes.

`await resources.read(path)` returns UTF-8 text; `read_bytes(path)` returns raw
bytes. The API derives the deployment from the signed execution's durable SQL
pin, verifies its active attempt, organization, owner, manifest and queue
evidence, and reads only the matching resource. A queued execution may read its
superseded deployment while its Solution remains active. Human administrators
and loose executions cannot use this execution endpoint. Query parameters
cannot choose another deployment. Missing or corrupt resources have no Root
fallback. Azure and S3 reads use a bounded byte range before buffering.

The protected-Git workflow recipe accepts a complete optional `resources` map
from runtime path to exact Git blob path. Only regular JSON and PowerShell
source blobs are accepted. Hidden paths and browser `storage-state.json` are
rejected. Git object sizes are bounded before concurrent blob reads. The full
recipe and union of Python and resource hashes bind the artifact digest,
candidate identity, receipt and current-state comparison.

Workflow delivery stores a separate create-only resource archive and runtime
objects. Inspection checks exact stored archive members, size, hash and actual
runtime bytes before activation. Completed delivery and replay independently
verify the resources and Python runtime objects. Source-only delivery still
rejects resource-bearing bases and candidates. Resource imports require the
workflow adapter and explicit `resources.read` or `read_bytes` calls with a
literal path or an unmodified module constant present in the reviewed map.
Dynamic paths, rebound namespaces and hidden method aliases fail before staging.

Native database tests cover resource-only updates, reviewed reverts, accepted
old execution pins, revoked attempts, canceled staging and corrupt replay
objects. The object store in those tests is synthetic. Actual GitHub producer
and isolated worker rehearsal remain required before production enablement.
Deployment source resources are for reviewed JSON evidence or executable
scripts. Current BSN
client authority, Meraki Config Vault state and browser authentication state
need their operational data contracts; packaging them as immutable source does
not establish those contracts. This reader has not been deployed or enabled
in production.

## Canonical offline delivery review

`bifrost.solution_delivery_review` carries the same recipe models, declaration
compiler, parameter inference, resource reference validation and protected
registration fields that the deployment adapter uses. Server wrappers still
produce the frozen deployment contracts and perform all database, ownership,
role, trigger, immutable-object and pointer checks. Configuration and migration
startup do not import the SDK. The SDK's normalized path helper and the startup
policy validator retain the same path rules.

`review_solution_recipe` checks a complete desired recipe and optional exact Git
baseline without importing or running their source. Workflow review rejects
unsupported removal, rename, type/signature/control changes and incomplete
resource/dependency closure. The legacy source adapter permits body changes
with identical explicit entrypoint signatures and decorators. Adapter changes
need a reconciled live baseline. Both return `live_state_verified=false` and
`runtime_verified=false`; an offline pass cannot establish live UUID ownership,
role existence, current table metadata, callers or triggers.

The installed CLI artifact includes the compiler and has a standalone test with
no `src` or SQLAlchemy import. Workspace CI must bake and pin this artifact
before enabling an installation. The new workspace gate runs for every CI scope,
reads exact Git bytes and fails if an enabled recipe lacks its compiler. Its
empty-registry path is inert and requires no SDK or live credentials. An
installation cannot bypass checks by deleting its registry entry or changing
targets. Reviewed live retirement/handoff remains necessary for those operations.

### Local resource iteration

`bifrost run` and `bifrost solution start` accept an explicit
`--resource-recipe <workflow-delivery.json>`. They reuse the same complete
workflow recipe's resource map to read current declared checkout files, including
dirty edits. No preview, prepare or activation is needed for those local bytes.

The recipe must be a bounded regular file inside a Git checkout, and the local
workflow file must appear in its source map. Resource paths keep the deployment
recipe's JSON/PowerShell and hidden/auth-state restrictions. Every read checks
regular-file containment, rejects symlinks, and enforces positive sizes, the
2 MiB individual bound and 10 MiB total bound. Recipe and resource changes are
read afresh for each local invocation; resource edits are visible on each read.

Both direct and browser-driven `bifrost run`, and each local function host call,
bind an isolated local resource context for imports and function bodies. Even a local run without
a recipe has an empty map. Missing, undeclared or invalid local bytes fail
locally and cannot fall back to an HTTP request. Context resets after success
or failure and concurrent local invocations keep separate maps. The function
host binds an empty map for source files outside the selected recipe, so
unrelated resource-free workflows keep running and undeclared resource reads
still fail without HTTP fallback. A direct CLI run with a recipe requires its
source file to be declared by that recipe.

Recipe UUIDs do not select a server install or replace the CLI's normal data
plane binding. Other SDK calls and vendor effects still use the developer's
configured credentials. This resolver supports source iteration; it is not a
sandbox or live runtime proof. Resource-backed local runs cannot emit loose
Workspace promotion evidence, which does not bind these bytes.

Worker execution does not bind a local resource context. Its signed attempt,
durable deployment pin and immutable resource verification remain required by
the API. Production execution and source activation continue to require their
reviewed deployment path.
