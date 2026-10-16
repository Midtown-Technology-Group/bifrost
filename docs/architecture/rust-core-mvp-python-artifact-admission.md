# Python artifact admission: source-derived common input

2026-10-16, platform `e42999395231ebc536711e5015422edfb75613a7`.
[Source hashes](rust-core-mvp-python-artifact-source.json) bind this inspection.
Design input to CP-CONSOLIDATE, not an accepted runtime artifact, new public SDK,
negotiated profile or authorization to run another experiment.

## Reuse the interpreted descriptor already proposed in main

`contracts/runtime/v1/language-neutral/artifact.schema.json` already has a closed
`interpreted-runtime/v1` branch. Do not invent a parallel Python descriptor kind
or add fields to its closed object. Its README explicitly labels the package a
static document proposal, not a negotiated wire profile or runtime acceptance.
The Go SQL candidate's native-only discriminator is narrower than this existing
document foundation; the common migration must account for that difference.

The common artifact association should discriminate interpreted/native payloads
and retain each branch's exact schema validation. Its SQL header can enforce
kind/protocol/artifact association while the trusted admission validates complete
closed descriptors against the pinned schema and recomputed identities. Do not
replace the native check with arbitrary JSON acceptance. A successful JSON
schema check is structural, not proof of provenance, installed bytes or custody.

| Existing interpreted field | Source-derived evidence requirement |
| --- | --- |
| kind | Exactly interpreted-runtime/v1, not native-executable/v1 with renamed contents. |
| artifact_id | Immutable identity recomputed under the selected profile's explicit canonicalization; no hash of a mutable directory listing or invented artifact from counts. |
| adapter_sha256 | Exact trusted Python adapter bytes/build identity, independent of authored workflow source. Current Python backend module paths are coupling to extract, not public imports to expose to workspace. |
| image_digest | Actual supported runtime image digest. The schema permits NULL structurally; lack of real artifact evidence remains a qualification failure. A base-image digest in Dockerfile is not the digest of the built worker image. |
| sdk | Distribution/version from the actual selected runtime artifact. Source pyproject names bifrost-sdk and supports Python >=3.11; that does not establish the installed distribution version. |
| runtime | Actual implementation/version observation from the selected image/interpreter. Dockerfile currently uses a pinned Python 3.14 base and copies its site-packages, but that source line alone is not a measured runtime receipt. |
| source_bundle_sha256 | Exact materialized accepted source bytes, tied to deployment closure below. A deployment bundle hash is not automatically this exact bundle serialization. |
| dependencies.kind/digest | A specified, immutable dependency-environment evidence document and digest; requirements text or number of installed names is insufficient to identify installed code. Select its versioned kind/canonicalization in the profile review. |
| runtime_protocol | Existing bifrost.runtime/v1 identifier, with separately negotiated complete execution profile; do not claim P0 alone supplies the missing caller/Start/limits authority. |

The interpreted descriptor has no build_evidence_sha256 field. The Go artifact
SQL currently binds that field inside native JSON; preserve it for native
artifacts, not as an impossible interpreted-field requirement. Python build and
materialization evidence belongs in a separately bound common admission record
under the reviewed profile, without widening this closed descriptor. The exact
common column/receipt transformation remains part of the schema review.

## Current deployment pins are useful but do not pin the environment

`PinnedWorkflowRuntime.queue_evidence()` already records deployment/Solution,
bundle and compiled-manifest hashes, source path/function/hash, storage prefix,
git SHA (nullable), timeout, execution mode/type, cache TTL, organization/global
repo permission and full deployment source hashes. Runtime bounds and parameters
schema are included when present. `verify_runtime_evidence` requires queued,
durable and authoritative documents to agree and checks the durable hash.
Preserve that exact contract. Do not replace accepted source identity with
interpreter identity or infer an environment pin from the source closure.

Those fields do not provide an installed dependency closure, adapter digest,
actual final image digest or SDK distribution observation. Python authored code
therefore cannot yet be admitted safely merely by inserting an owner row and
reusing the worker's current mutable environment. This is a concrete remaining
extraction prerequisite, not evidence that Rust must import Python metadata.

## Requirements setup and Prepare are different phases

`ProcessPoolManager.start()` installs requirements in a short-lived setup helper
before starting templates. Recycling repeats setup and drains templates. The
helper reads requirements through existing Redis/S3 behavior, runs pip, and
reports attempted/installed/failed packages plus installed/total counts.
`simple_worker.install_requirements()` permits pip options, tries a batch and
falls back to individual packages. The heartbeat status uses package names,
not a cryptographic installed-byte inventory; failures are reported resiliently.
Do not reinterpret this as a reproducible locked environment or successful
all-or-nothing dependency build.

Consequences for the bounded runtime boundary:

1. Preserve existing Python-owned requirements behavior during coexistence.
   Do not change its global install, fallback or public authoring semantics as
   incidental common-schema work.
2. The trusted artifact preparation/build stage must resolve and retain the
   exact selected dependency environment before an execution can claim a
   no-effect Prepare. Installing arbitrary source packages can run build hooks;
   pip execution is not a credential-free metadata inspection step.
3. Runtime Prepare verifies pre-admitted bytes and observations only. No tenant
   import, dependency installation, entry-point hooks or provider effects. A
   dependency image/materialization proposal needs its own exact build inputs,
   artifact provenance and supported-image qualification; none is synthesized
   here and no additional campaign is started.
4. The selected Python workflow need not change imports or SDK calls to satisfy
   this. If environment parity needs workspace accommodation or silent removal
   of dependency behavior, stop at that actual incompatibility.

`template_process` loads execution infrastructure, ensures user site-packages
is on sys.path, installs virtual imports, scrubs startup credentials and forks
children. The child installs the SDK socket before invoking simple_worker.
`VirtualModuleLoader.exec_module()` compiles and executes source; that action is
execution, not metadata discovery. Extract the trusted adapter and sanitized
context separately from Python control-plane services; no PyO3 embedding or
callback loop to Python backend internals.

## Admission and evidence sequence

- Reviewed recipe/deployment gives registration and immutable source closure;
  Rust validates language-neutral metadata without importing authored Python.
- Separately approved artifact preparation supplies final runtime image,
  adapter, SDK and dependency-environment identities and exact source bytes.
- Trusted coordinator validates descriptor, provenance and closure under the
  accepted caller/source/profile. This does not issue runtime SDK authority.
- Prepare and Prepared bind byte observations to the exact session/process and
  preparation digest; descriptor equality alone cannot substitute for custody.
- Committed Start and common grant/provision/release gates admit execution.
  Only then may Python import workflow code and use the closed SDK operations.
- Result/Cancel, trusted stop and resource settlement remain common lifecycle
  facts; interpreter exit does not release source by itself.

Minimum future qualification must detect wrong image/interpreter/SDK/adapter,
changed installed dependency bytes with unchanged requirements text, source
closure mutation, cross-install virtual-import contamination, a pre-Start
import/hook/network effect, and an absent observation presented as a valid pin.
Retain existing source/context/timeout/no-timeout/error behavior. Current source
inspection and static schemas pass none of these runtime gates by themselves.

Next common-owned work is the exact dependency/materialization evidence shape
and its binding to the existing interpreted descriptor and owner preparation
receipt. Existing profile/role/payload review holds remain. This input requires
no Go consumer change or peer acknowledgement.
