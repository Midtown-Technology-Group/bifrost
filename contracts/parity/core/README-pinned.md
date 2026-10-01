# R1 Python pinned Scenario A

This extends the retained R0 and device reference with a separate `deployment-v1`
scenario. Procedure: mtg-engineering-flow **2026-09-30.1**. It does not complete
C1-R acceptance or prove Rust authority, model quality or vendor compatibility.
[Provenance](pinned-source.json) pins the unchanged workspace source and commit.
Read-only reconciliation against current workspace main
970f3030ef66d20abe83fd7ef0e400cd114da80d found an empty diff for all selected
A/B source files; the original 83c1cb source provenance remains exact.

`PinnedEnvironment` creates an owned Solution through public HTTP. Candidate,
preflight and activation use the existing reviewed initial-workflow contract,
with exact authored bytes and source commit. Real object storage holds the
archive, runtime source and compiled manifest; their readback must agree.
Activation must register exactly one tenant workflow with one explicit owned
role, disabled endpoints, immutable runtime bounds and no execution side effect.
The setup admin is separate from the normal tenant execution caller.

The real public workflow run must return `ready: true`, emit actual successful
SDK integration/config/mapping requests and commit `deployment-v1` execution,
workflow attempt, generic attempt, delivery and events. Nominal Success with
caught SDK errors fails. The Solution/deployment/source evidence and hashes must
agree across immutable storage, committed execution, dispatch and delivery.

`PinnedTransportEvidence` adds `source_requests` to the existing separate
transport plane. The observer allows only the registered canonical deployment
source GET, empty body/query, signature-verified caller/org/Solution and a
matching committed active execution-attempt fence. It permits the closed
CachedModule response fields from the current source contract. Source inspection
confirms the immutable cold path constructs `content`, `path` and `hash`, omitting
`generation` and `storage_path`; the guard accepts that exact three-field shape.
This is contract inspection, not a claim of a successful runtime fetch. Source bytes,
path and hash must match the exact retained blob and durable pin. Raw synthetic
source and safe claims remain observable; bearer JWTs and raw fence tokens never
enter the transport records. Normal R0 registries still reject source GETs.
Integration get permits only its exact owned `solution` field; mapping requests
retain their original exact shape. Extra methods, source paths, query bytes,
response fields and unknown module-resolution capabilities are refused.

The nominal test requires an **actual observed source fetch**. If supported
installation prewarms cache and no GET occurs, the test fails for review of a
positive cache/source proof; it never invents a source request. Only explicit
owned cache keys/index members are removed for the source-corruption self-test
and cleanup. No global cache sweep or workspace generation mutation occurs.

`PinnedProfile` reuses the Core profile and preserves the device default. It
validates raw manifest/resolution/source/bundle/preflight/runtime hash relations
before mapping only enumerated installation identities, paths and derived
hashes. Source bytes, git SHA, schema, scoping, role grants, controls, result
values and opaque payloads compare exactly. Bounded installation clocks and
runtime measurements follow the existing explicit policy. Raw observations
remain intact. No baseline vector is fabricated.

The matrix in `tests/parity/test_core_pinned_reference.py` includes real pinned
readiness, two independent Python installations, normal-caller foreign-org
admission denial, stale preflight evidence rejection, actual owned runtime-object
corruption rejected by real preflight, supplemental source/request guard vectors, and eight profile mutants from
copies of actual captured backend observations (source/storage identity,
derived hashes, caller/fence and workflow identity). The latter prove comparator
rejection and do not represent actual wire/database mutation; the owned storage
corruption case supplies independent actual backend drift. Corruption is deliberate test-only drift, never changed source behavior.
The evidence report is `/tmp/bifrost/core-pinned-reference-readiness.json`, written
only after strict nominal assertions. It contains canonical observations,
separate transport and raw numeric/time measurements, with no credential dump.

Cleanup requires every allocated owned execution present and terminal with a
completion time, no active workflow attempt, and every owned delivery settled
before removing only owned execution,
workflow, Solution/deployment rows and the three known artifact/runtime object
keys. Unexpected external dependencies stop cleanup and retain diagnosis scope.
R0's unique loose-source cleanup path is restored before its principal/config
cleanup; no fixed authored-path deletion is performed against other installs.
Missing or nonterminal authority retains the entire fixture before any deletion.

Root owns Compose, test.sh and CI. Use the existing isolated PostgreSQL lane with
`CORE_REFERENCE_ISOLATED=1`, real API/worker/object storage/Redis and the SDK
observer, then supported selection:

```
./test.sh tests/parity/test_core_pinned_reference.py -v
./test.sh quality api
./test.sh pre-pr
```

The selection depends on root-owned environment wiring; no host pytest is
permitted. Preserve all R0/device selections. This candidate has scoped Ruff,
compilation, JSON/provenance and whitespace proof; actual Docker/CI behavior and
clean-candidate quality/pre-PR proof remain pending. Stop on unsupported source
closure/materialization, new module transport needs, unobservable source/events,
or unsafe fixture isolation. R2 remains unauthorized.
