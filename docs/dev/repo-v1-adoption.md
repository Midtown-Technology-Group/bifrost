# Reviewed adoption of legacy Solution runtimes

Legacy Solution installs with a null deployment pointer are real source
consumers. They continue to block complete source accounting until migrated.
Adoption is distinct from first installation and must preserve installed rows.

The first adapter covers sealed, disconnected workflow/tool/data-provider
installs with owned tables, config and connection declarations, file metadata,
policies and triggers. Agents, forms, apps, supervised services and pending
captures require their own runtime adapters; their presence is a conflict.
They remain in the retirement inventory until those adapters are verified.

## Acceptance criteria

- Stage a create-only immutable artifact from reviewed source, resource bytes,
  registration recipe and Git identity; retain a digest of every installed
  workflow, including inactive rows, credentials and role assignments.
- Preserve UUIDs and all installed entity/control rows. Adoption changes only
  the Solution runtime discriminator and active pointer, plus candidate history.
- Read the current mutable Python source independently, with bounded file and
  byte counts. Bind its hashes and installed entity digests into stage evidence.
  Re-read them at preflight and activation; reject stale source or controls.
- The reviewed recipe contains exactly the active registrations. Its metadata,
  identity, scope and parameter contract must match installed registrations.
  Inactive registrations remain inactive and are retained in review evidence.
- At activation, take the Solution writer lock and the exclusive runtime
  admission fence before row locks. Refuse any accepted execution or unfinished
  workflow attempt for any owned UUID, including inactive workflows. Do not
  cancel work, repin it, or change its receipt to obtain a cutover.
- Use exact evidence and null-pointer CAS. A stale candidate, unsupported
  entity, broken closure, unbound Root resource or competing writer fails closed.
- Exercise real API/worker execution after cutover, including a warm legacy
  cache and a fresh worker. Runtime must read the immutable deployment prefix.
  Retain the old source prefix for historical evidence; never globally clear
  caches or delete old source to make the proof pass.
- Adoption is runtime migration, not Git source accounting completion. Enroll
  the resulting install in reviewed protected Source delivery, verify its
  repository mapping, runtime pin and obligation readback independently.

Human source review is recommended by
[delivery lane 3](delivery-lanes.md#lane-3-platform-or-live-operations), with
reviewers selected for the adoption and migration risk. Protected deployment
and explicit authorization for the runtime migration remain required. This
adapter alone does not authorize retirement of Workspace Live.

## Operator path and compatibility

Platform administrators use the `repo-workflow-adoption/candidate`, `preflight`
and `activate` operations below
`/api/solutions/{solution_id}/deployments/{deployment_id}`. Candidate input is
the existing reviewed initial-workflow contract; activation additionally
requires the exact evidence digest. GitHub producer credentials cannot invoke
these administrator operations. Routine changes after adoption use protected
Git Source delivery or the existing reviewed workflow revision path.

Legacy list-shaped parameter metadata stays in the registry during adoption.
The immutable entity attests its exact list digest after compiling and checking
the unchanged Python signature. Runtime input validation uses that complete
signature. A reviewed Source successor projects the full schema normally;
changing the legacy list before that successor fails closed. The optional
`legacy_parameters_schema_hash` field is additive and omitted when absent, so
existing manifest hashes and historical execution evidence remain unchanged.

Installed legacy descriptions and categories may differ from source-derived
strings, including an installed `null` description that the compiler cannot
express. Adoption seals exactly those two installed values and their digest in
`definition.legacy_descriptor_evidence`. The executable definition retains the
reviewed source descriptors; adoption never projects them into the registry.
Fresh preflight recompiles the complete artifact with independently read
installed descriptors. Descriptor drift, malformed evidence, source drift and
identity, scope, parameter or security changes still fail closed. A source-only
successor retains this evidence; a reviewed registration successor compiles its
normal descriptors and projects them under the existing controls. Historical
pins continue to reference the original immutable artifact.

Candidates staged by the earlier adoption implementation must be restaged with
a fresh candidate UUID to obtain the complete descriptor evidence. Existing
active deployments and their historical hashes are unchanged. No public DTO,
SDK field, database migration or metadata PATCH is added.

## Remaining cutover evidence

The 2026-10-03 production inventory read all 45 catalog entries and active
pointers, including inactive install states. It found 21 repo-v1 installs with
null pointers: 20 containing workflows and the Meraki app-only install. These
counts are metadata evidence, not migration readiness. Each workflow install
still needs a fresh source/registration/control inventory, reviewed dependency
closure and bindings, accepted-work drain, exact candidate activation,
independent worker pin and protected Source mapping/accounting readback.
Microsoft mutation coordination remains with its designated operator.

The app-only install remains a separate implementation stage. Standalone V2
serving reads `Application.active_deployment_id` and versioned app dist; a
Solution pointer alone cannot switch it. Its reviewed route must bind current
app identity, roles/config, immutable source and built dist, exact protected
Git path mapping, and Root workflow references. Existing SDK rebuild/guarded
app CAS provides build machinery, but does not provide that Source-accounting
proof. Keep unsupported apps blocked until this adapter and live readback exist.

After all consumers are verified, run complete consumer and obligation
readback, retire Live through its guarded operation, then remove obsolete lane
code and operational steps. Preserve stale-write, dependency, admission and
unexpected-live-execution checks in the remaining deployment paths.

## Installed caller names and authored declarations

Native adoption may retain an installed caller name that differs from the
literal name compiled from the reviewed source. The server reads the installed
name itself; requests cannot supply a replacement caller identity. The runtime
`definition.name` remains the installed name and
`definition.legacy_registration_name_evidence` separately seals both that name
and the source declaration with the versioned
`bifrost.solution-legacy-registration-name/v1` contract and canonical hash.

Candidate inspection recompiles the complete archive and reconstructs this
binding from fresh installed rows. Resealing a forged manifest cannot substitute
another installed or source name. Exact UUID, function, path, scope, type,
security controls, baseline digests and accepted-work/CAS checks still apply.
Activation does not rewrite registration names or descriptor metadata.

Source-only successors retain the sealed definition and require unchanged
signatures/decorators. Reviewed workflow successors preserve an existing name
binding only when the new source compiles to the same sealed declaration; an
unreviewed rename remains an error. New workflow identities do not inherit an
existing binding. Runtime pins validate the sealed binding and retain the installed caller name.
Export compares registrations with the immutable identity; older accepted pins
retain their own bytes.

This contract adds no aliases, live registration PATCH path or table grants.
Missing resources and incompatible execution controls require their own verified
resolution before a family can be adopted. Qualify the deployed contract before
submitting a fresh production candidate; a green source PR is not runtime proof.
