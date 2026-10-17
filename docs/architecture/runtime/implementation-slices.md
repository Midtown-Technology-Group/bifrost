# Runtime foundation implementation slices

Issue-ready plan; no tracker issues created. Detailed decisions live in
[contract](contract.md) and [review](review.md). This map separates implemented
static documents from unimplemented runtime behavior and remaining acceptance.

## Slice 1: Honest artifact evidence across Python and Go

Scope: the closed artifact union and shared input/result/log/error documents,
implemented first as offline schemas on current MTG main. Add Rust/Python
native types and codecs only against the accepted Rust contract baseline;
do not replace its existing crate or coerce Go into P0 interpreter fields.

Acceptance criteria: Python, Go, Deno and .NET deployed artifact classes validate;
unknown classes/authority fields and contradictory success/error fail. Actual
Python/Go encoding interchange passes every incumbent P0 vector unchanged plus
new artifact/document negatives. Same binding structure across workload and
provision documents is mechanically checked.

Verification: offline check.py (implemented), existing supported Rust and Python
container codec suites and both encoder-to-peer-decoder directions (remaining).

Depends on: selected reviewed Rust baseline and explicit full-profile negotiation.

Stop/expand gate: static vectors do not authorize runtime execution. Do not
import the unmerged runtime stack wholesale into main to satisfy a path target.

## Slice 2: Python and Go obey one execution-permission boundary

Scope: trusted adapters consume accepted deployments and launch/evaluate tenant
code only after durable Start and valid same-session capability delivery.
No language-specific lifecycle states, broad credentials or mutable restoration.

Acceptance criteria: malicious Python imports, Go variable/package initializers,
wrong deployment/session/attempt/caller/scope, expired grants, cancelled sessions
and failed provision delivery cannot cause pre-permission effects. Actual
capability enforcement denies lifecycle/control-plane writes. Cancellation
propagates through SDK waits and custody covers all descendants.

Verification: supported isolated VM/CI actual adapter/process/HTTP tests with
retained attempt/session/deployment correlation and secret-disclosure negatives.

Depends on: Slice 1 reviewed codec, #1011 owner/source/provision interfaces and
mechanical writer exclusion. Mechanism tests cannot substitute for owner proof.

Stop/expand gate: no production credentials, active routing, deployment or C2/C3
acceptance from source-only checks. Existing stopped reference runs stay stopped.

## Slice 3: One behavioral oracle accepts Python and Go

Scope: import actual workspace compatibility fixtures and executable workloads;
complete the case envelope with ordered HTTP exchanges, integration bindings,
secret references, schema/input/output identities and no-network preflight.

Acceptance criteria: exact result/error and ordered interaction equivalence for
both implementations. Run every required coverage-roster scenario, including
capability denial, malformed responses, cancellation, deadlines, stale/wrong
sessions and secret nondisclosure. Retain raw observation versus durable owner
projection distinctly; do not normalize per-language differences away.

Verification: same cases against both real adapters, deliberate output/security/
interaction drift proves red, corrected implementations prove green.

Depends on: Slice 2 and accepted fixture oracle/schema extensions. Current
required-scenarios.json is a roster; structural case examples are synthetic.

Stop/expand gate: no portability claim from schemas or scenario counts alone.

## Slice 4: Result and cancellation have one durable outcome

Scope: reviewed owner integration for receipt correlation, result acceptance,
cancellation serialization, fences, deadlines and replay decisions.

Acceptance criteria: one durable final projection under concurrent Cancel/Result;
late/closed/wrong-binding results never restore authority. Conflicting duplicates
fail; ACK loss remains unknown and cannot replay effects. Stopped/process loss
never claims durable completion. Mixed writers are mechanically excluded.

Verification: supported real transaction/race/ambiguous-commit tests, owner
identity evidence, effect oracle and cleanup inventory; all repo release gates.

Depends on: Slices 1–3 and remaining #1011 owner/SQL/exclusion acceptance.

Stop/expand gate: preserve the existing lifecycle authority and release approvals;
reviewed source is not deployed or nominal workflow acceptance.

## Slice 5: Independent runtimes validate the frozen foundation

Scope: Deno first, .NET second, after Python/Go contract acceptance. Each uses
idiomatic authoring and accepted immutable dependencies. .NET Native AOT follows
native bytes; managed .NET follows its managed deployment descriptor.

Acceptance criteria: same lifecycle state machine, capability semantics and
behavioral oracle without language-specific authority. Deno permissions are
additional containment; no runtime builds/restores or compiler installation.

Verification: independent adapters run the same positive/negative cases and
preserve Python/Go conformance. Changes requested to lifecycle/provisioning reopen
the common contract review rather than creating runtime exceptions.

Depends on: reviewed freeze following Slices 1–4.

Stop/expand gate: no implementation or production adoption of Deno/.NET is
included in the initial foundation package. Ninja/Sopdet remains separate.
