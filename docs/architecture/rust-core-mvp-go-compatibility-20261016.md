# Go compatibility and reusable components for the common runtime

G-COMPAT response to COMMON-RUNTIME-RECONCILE-20261016-01. 2026-10-16.
Status: source/evidence mapping complete; no code, migration, credential, profile,
runtime acceptance or release change. The common owner edits the canonical
record; this artifact supplies the Go-side input. Engineering-flow 2026-10-06.1,
handoff package 2026-10-09.1.

## Exact comparison inputs

- Canonical record: `/home/thomas/src/bifrost-rust-mvp-verification-disposition/docs/architecture/rust-core-mvp-common-runtime-reconciliation.md`,
  commit `5d760c3e49952e28387756fc4f2d48935c1baae2`, branch
  `docs/rust-mvp-verification-disposition`.
- Platform `origin/main`, freshly fetched: `e42999395231ebc536711e5015422edfb75613a7`.
- CRED-P1 source: `/home/thomas/src/bifrost-runtime-sdk-ingress`,
  `3918d608a2d497d1ca8424fd4229d191323e2d7a`.
- AUTH/Python comparison: `/home/thomas/src/bifrost-agent-caller-authority`,
  `39245315e193e1d04886044677bf7a75bb8c6f1f`.
- Accepted Go code: `56d1fb006d220b9dbb0747f1cca4def18677f918`;
  frozen evidence HEAD `a47586732ef1b2523b9119b5056d83574ccdd779`,
  `/home/thomas/src/bifrost-go-first-class-spike`, SDK `0.0.0-spike.2`.
- Workflow binary `5f9c912ec6055882e0d443378cb93bb87b58eb7ab2a49eb5e14d338d72865edd`;
  archive `sha256:9ac79cd99e41b10bd07613ff54aca5e50a2c7f5d2326da83064bedf9060a8e5a`.
- Workspace `0b70c082bfa1a3c65b1ead8661774dd5d4ae36a1` and its 2,449-file
  boundary are reported by the common owner; this map does not repeat that audit.

All Go-relative paths below resolve in the frozen Go checkout; CRED-relative
paths are explicitly marked. Historical package READMEs accumulate intermediate
"not yet proved" statements: the final proof and later bounded acceptance govern
what was demonstrated, while source governs current behavior. Neither changes
production status. No whole branch import or direct migration concatenation is safe.

## Reuse and adapter impact

| Seam | Source / implemented mechanism | Reuse and precise gap | Go impact after shared decision |
| --- | --- | --- | --- |
| Ordinary authoring | `workflow.go:Workflow`, `ClientFromContext`; `internal/readiness/readiness.go`; `cmd/workflow/main.go` | Preserve typed handler, ordinary errors/context, consumer interfaces and standard Go tests. Workload imports no Rust/DB internals. | Keep workload API independent of SQL/schema reconciliation. No DSL or Go lifecycle API. |
| Native build/artifact | `scripts/measure.sh`, `scripts/describe.py`, `scripts/native_bundle.py`, `builder.Dockerfile`; `internal/nativeadapter:VerifyBundle`, `PrepareNative`, `SealExecutable` | Reuse readonly dependency inputs, pinned toolchain, source/binary/archive separation, explicit schema/bundle digests and immutable sealed executable. This Linux/amd64 CGO0 recipe is bounded fixture proof, not hostile multi-tenant production custody. | Common artifact contract must identify Python interpreter/dependency/materialization evidence separately from Go module graph and ELF. Do not force Python through `go-module-graph/v1`, memfd or native archive assumptions. |
| Execution adapter | `internal/nativeadapter:PrepareTransport`, `RunInheritedSession`, `NativeFrontier`, `DeliveryReader.Read`; `cmd/adapter`, `cmd/launcher` | Reuse independent public frames, inert preparation, launch only after matching Start/Provision and released private material; no tenant import/init before launch. Private `runtime-private-delivery/v1` and bundle index are fixture delivery formats, not public lifecycle additions. | Reconcile selected common profile and delivery/custody interfaces before edits. Retain strict unknown-key/duplicate/Unicode/bounds/sequence/correlation validation and P0 vectors. |
| Local supervisor | `cmd/protocolprobe/main.go`, `runtimecontrol/`, `executionprofile/` | Non-Rust probe executes same adapter and workflow using common frames. Decisions/SDK fixtures are synthetic; it owns no real durable state. | Extend common vectors/probe only for agreed contract changes; do not grow it into a second production coordinator. |
| Result | `internal/nativeadapter/report.go:SuccessReport`, `FailureReport`, `ResultReport.ObserveReceipt`; `owner-rs/src/result.rs:accept_result` | Reuse exact raw-payload hash and receipt/message/session correlation, Result-vs-Cancel serialization and commit-before-receipt. | Python logs/usage/resources/summary/native publication semantics need common projection mapping; readiness result proof does not qualify all Python projections. No public Go finalization method. |
| Cancellation and drain | `RunInheritedSession`, `owner-rs/src/guardian.rs:DrainedSession::retire_resources`, `Guardian::verify_original_owner_loss`, `Guardian::retire_closed_session`; `resources.rs:settle_retired_session` | Go direct-child Wait is not descendant/source retirement. Rust consumes opaque retirement capability after original physical/source closure. | Keep cancellation in context/process contract and finalization in common owner. Never use Go process exit or advisory Stopped as source-release authority. |

## One schema and one issuer: exact collisions and deltas

Actual ORM comparison used AST inspection without imports or DB access:

| Record | CRED-P1 | Accepted Go | Difference to resolve |
| --- | --- | --- | --- |
| `workflow_runtime_sdk_grants` | 35 mapped columns in `api/src/models/orm/runtime_sdk_grants.py` | Same 35 names plus 3 in `api/src/models/orm/runtime_execution.py:WorkflowRuntimeSDKGrant` | Adds `owner_incarnation_id`, `committed_start_id`, `start_message_id`; no CRED column removed. Matching names/version alone do not prove semantic migration compatibility. |
| `workflow_runtime_sdk_grant_operations` | 8 mapped columns | Same 8 names | Same operation enum (`integration-get`, `mapping-get`), two ordinal slots, unique operation per grant, default/global/organization scope constraints. Constraint/FK lifecycle differs. |

Both preserve `cred-p1/v1`, `solution-deployment`, attempt/session uniqueness,
caller/effective identity fields and digests, timeout/deadline shape, flags,
revocation shape and digest syntax. Go adds relational enforcement, not a new
public capability namespace:

- CRED typed-attempt and source FKs use CASCADE; Go uses RESTRICT plus exact
  `(workflow_attempt_id, execution_id, attempt_number)` and source/install bindings.
- Go ties grant to owner + caller digest, session + execution + owner + attempt,
  claim digest + worker + supervisor, committed Start + message + clock, and
  execution/workflow/deployment. Composite admission identity also binds
  operations digest and initial expiry. Preserve the security relationships
  when redesigning native live links; do not merely drop FKs to fix deletion.
- Go requires `issued_at >= started_at`, finite initial expiry after issuance;
  close permanently revokes session grants. Go operation rows use RESTRICT
  and install association, whereas CRED operation-to-grant uses CASCADE.
- ORM comparison is structural evidence only. Actual migration triggers, writer
  guards, custody, issuer and ingress checks must move together under the common
  owner's reviewed integration. Copying just model declarations is insufficient.

Migration files (revision ID is distinct from filename):

- CRED `api/alembic/versions/20261001_runtime_sdk_grants.py`:
  `20261001_runtime_sdk_grants` -> parent `20261001_solution_src_account`.
- Go `20261009_runtime_deployment_artifacts.py`: revision
  `20261009_runtime_artifacts` -> parent `20261006_mtg_upstream_sdk`.
- Then Go `20261010_runtime_owner_sessions.py` (`20261010_runtime_sessions`),
  `20261010_runtime_starts_receipts.py` (`20261010_runtime_receipts`),
  `20261010_runtime_sdk_grants.py` (`20261010_runtime_grants`),
  `20261010_runtime_admissions.py`, `20261010_runtime_identity_fences.py`
  (`20261010_runtime_fences`), `20261011_runtime_resource_settlements.py`
  (`20261011_runtime_resources`). Each follows its preceding Go revision.
- Main's separate `20261012_work_delivery_started` branch from
  `20261006_mtg_upstream_sdk` is recorded by the common owner. No combined
  installed revision history was inspected or qualified by this mapping.

Common owner must choose one integrated Alembic/issuer path against actual
installed history, preserving old immutable refs and unknown-ACK semantics.
Go supplies consumer/conformance changes afterward; no migration patch here.

## Credential policy and actual SDK surface

CRED `api/src/services/runtime_sdk_grants.py:renew_workflow_runtime_sdk_token`,
`load_runtime_sdk_ingress_authority`, `admit_runtime_sdk_renewal` implement a broader
approved reference than the accepted finite readiness slice. In particular,
finite grants reject renewal; eligible unlimited-duration policy uses bounded
access windows and non-upgrading renewal. This is not authority to add renewal
to the current Go slice.

Go `client.go:IntegrationService.Get` only calls
`POST /api/sdk/integrations/get`; sends `name`, supervisor-provided `scope`
(null if organization absent) and optional `solution`. `GetIntegrationOptions`
is empty. It exposes no arbitrary org/entity/OAuth selectors, mapping-get,
automatic retry or renewal. It uses HTTPS with certificate/hostname verification,
refuses redirects, has a 10s client timeout and bounded response body; API errors
expose status, not response bodies. Config remains `json.RawMessage`, preserving
numeric bytes. Normal context cancellation reaches the HTTP request.

The selected trusted adapter is stricter than public `NewClient` alone:
`internal/nativeadapter/delivery.go:decodeDelivery` requires workflow binding,
organization scope matching the provision org, matching Solution, an unexpired
grant and exactly one capability `integration-get`; private material has exactly
endpoint/bearer/organization_id/solution_id/test_ca_pem. Therefore do not claim
that Go's accepted execution supports CRED global/default/null/OAuth/mapping
positives merely because a model enum or client constructor can represent them.

Required shared decision/Go follow-up: publish the selected typed capability and
scope contract, live-session and provision/renewal delivery semantics; preserve
original caller vs effective org and accepted installation/source binding.
Only then map any newly authorized mapping-get/default/global/OAuth/renewal into
idiomatic Go. Keep ordinary-route/admin/lifecycle rejection and no authority
upgrade. No second issuer, broad service token or ambient identity fallback.

## Deletion, retries and locks

| Common concern | Accepted implementation / evidence | Concrete unresolved integration work | Go adapter consequence |
| --- | --- | --- | --- |
| Ancestor unlink and retained identity | `runtime_execution.py:RuntimeExecutionOwner`, `RuntimeSession`, `RuntimeDeploymentArtifact` and grant composite FKs use immutable associations and RESTRICT, including reviewer/user references. | Common revised deletion contract preserves form purge OLD->NULL-only unlink, user/CLI/workflow SET NULL and native cascades; distinguish historical identity from live FK links, decide sensitive payload/tombstone disposal and installed maintenance closure. Fixture writer negatives do not prove every current main ancestor positive. | No workload/SDK source change required for SQL links. Update common admission/source bindings and consumer tests if public contract changes; never reconstruct accepted caller from a deleted live user. |
| Native NEW-attempt retry | `RuntimeSession` has unique workflow_attempt_id and permanent close. Owner-loss cases are stop-only; no workflow replay or logical-owner reassignment. | One session per typed attempt can coexist with a new native attempt after proved pre-Start no-effects/trusted reap/current retry eligibility. Accepted Go does not demonstrate that retry path. Missing Start/ACK/heartbeat/process alone proves no such eligibility. | Each admitted new attempt gets fresh session/provision; authored handler unchanged. Add conformance only when common operation/receipt identities are agreed; no same-session replay fallback. |
| Mixed lock orders | `owner-rs/src/lib.rs:lock_session_state`: shared `bifrost:workspace-release` advisory -> typed attempt -> NOWAIT owner/root/deployment/Solution -> session -> Start/ordered admission/grant/operation/receipt/resource rows. | Combine with native execution-first advisory/poison/recovery and CRED attempt-first + SHARE NOWAIT. Include FK/index/trigger/table/advisory waits, absent-grant close races and ancillary unlink; selected NOWAIT does not establish global deadlock freedom. | No wire-level Rust lock details exposed. Keep public rejection/uncertain outcomes and no replay; retest changed common writer paths under actual restricted principals. |
| Writer principal | `api/tests/e2e/platform/fixtures/runtime_writer_guards.sql`, `test_runtime_writer_guards.py` cover authenticated distinct fixture users, OLD/NEW dependent/root/effect guards and Python-owned positives. | Installed role/ACL/catalog, finite unlink/read/ANY-table-lock policy and external maintenance are common-owner decisions. Production privileges are not qualified by fixture grants. | Runtime child continues to receive SDK material only, never SQL or lifecycle helpers. |

## Exact demonstrated cases and limits

Source56 final proof and same-run native/live38132471738,
writer/security38132471756, foundation38132471702 remain the evidence authority.
`api/tests/e2e/platform/test_runtime_live_go_slice.py` executes real Rust owner,
SQL and authenticated existing Execution API. It proves nine completed unchanged
binary cases: readiness success; missing-key success; cancel during held admitted
SDK request; Result COMMIT ACK loss; closed Result owner death; release COMMIT ACK
loss without material/SDK; closed cancel owner death; resource COMMIT ACK loss
with exact read-only reconciliation; open-session owner death after real SDK.
Living-owner denial, same logical owner and no frame/effect replay are retained.

The separate tenth negative proves partial source retirement failure after real
SDK/Cancel/Stopped and process drain: CleanupRequired, no RetiredSession capability,
no settlement fact, original owner death, denied recovery, API Cancelling/null,
source accounting still pinned even after exact inert fixture obstruction disposal.
`guardian.rs:DrainedSession::retire_resources` constructs RetiredSession only after
final source directory retirement succeeds; `resources.rs:settle_retired_session`
consumes that capability. Missing files and test teardown cannot mint it.
This is adequate fail-closed evidence, not eventual cleanup or arbitrary unlink-
phase crash recovery. General resumable retirement belongs to the common owner.

Actual Docker create-response loss is a separate physical case: daemon201,
one create, withheld reply, fresh create/Start denied, exact container drain;
no tenant Start/SDK/finalization is claimed for that component.

Retained counts: 540 Go tests, 13,898 backend unit, 126 baseline, 248 guard cases
and 248 post-repeat, scan zero findings. Producer/native source identities and
cleanup were independently verified previously, then reviewed by Theo within
his stated limits. This mapping performs source/AST/file inspection only; it
reruns no fixture, scanner, build, benchmark or archived acceptance campaign.
It does not transfer green statuses to any extracted or integrated code.

## Blocking dependencies and next implementation order

1. Common owner resolves the duplicate grant tables/migration graph and keeps
   the approved credential policy plus exact owner/session/Start/caller/source
   enforcement. This blocks common integration, not ordinary Go authoring.
2. Common owner settles live-link/ancestor unlink, retry admission and combined
   lock/writer policy against native Python behavior and installed catalog.
3. Common owner returns one versioned selected protocol/provision/credential
   interface and concrete reviewed changes. Keep P0 unknown-field rejection and
   independent cross-language vectors; bounded acceptance is no profile freeze.
4. Go owner proposes only affected adapter/delivery/client/probe/conformance
   changes against that interface, then qualifies the changed source in existing
   supported lanes. Build/local CLI/registration improvements remain language-
   facing work; source execution for metadata discovery remains forbidden.

No new implementation release is requested by this map. No duplicate code,
common migration, issuer or lifecycle state machine was produced. The accepted
packet remains fixed. Common decisions remain with the Rust owner; Go's next
code step is gated on those concrete shared inputs rather than invented defaults.
