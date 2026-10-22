# Pydantic-native bounded decisions — phased delivery plan

Source: [issue #806](https://github.com/Midtown-Technology-Group/bifrost/issues/806).
Triage: MIDT-129. Updated 2026-10-12 after dependency
[PR #968](https://github.com/Midtown-Technology-Group/bifrost/pull/968) merged.
This replaces the earlier custom `DecisionEngine` delivery plan.

## Approved first experiment

Thomas chose **advisory ticket routing with wholly synthetic fixtures only**
on 2026-10-12 in original T3 thread
`mcp:9822162c-1fb4-4deb-b4db-00bbcfa162bb`. Carry that decision forward.
The output is a resolver owner (`endpoint`, `identity`, `network`, `human`)
and a boolean `escalation_required`, evaluated together as a typed schema.

The initial corpus is [ticket-routing-synthetic-v1.json](fixtures/ticket-routing-synthetic-v1.json).
It contains invented tickets and organization policies, with no customer-derived
content. It is a contract/development corpus, not a held-out calibration set.
Verify the frozen bytes with `cd docs/plans/fixtures && sha256sum -c
ticket-routing-synthetic-v1.sha256`. Changes to cases or labels require a new
fixture version; do not relabel this development set as held-out evidence.
No real tickets, identifiers, secrets, telemetry or customer policy may be sent
to hosted providers under this approval. Existing provider access is a separate
prerequisite; this plan authorizes no credentials, spending or deployment.

## Architecture and boundaries

The canonical primitive is `await decision(state, output_type, policy)`.
Its inference boundary is Pydantic AI `DecisionModel`, exercised through
`Agent(model, output_type=...)`; do not introduce another provider ABC.
Workflows retain provider-independent definitions. Provider configuration belongs
in the existing platform configuration path.

BiFrost retains workflow execution, organization-scoped state retrieval,
deterministic branching, authorization, approvals, events, tables and tool grants.
The caller derives organization identity from its execution context and retrieves
only that organization's state. Caller-supplied identity cannot widen access.
Check tenant-scoped configuration and state before inference.

The proof is shadow/advisory only. Output neither invokes a handler nor changes
a ticket, dispatches an agent, emits an action-driving event or grants authority.
Failure leaves the ordinary deterministic workflow path intact; do not invent
an alternate provider or silently retry until successful. Convenience wrappers
(`get_truefalse`, `get_choice`, `get_score`, `get_decision`) use the same typed path.

The existing `api/src/services/decision/` experiment and
`Settings.decision_inference_mode` remain legacy source, not completed acceptance
for this design. Retain useful validation/hash/metadata tests, then replace or
retire the unused abstraction in the typed implementation change. Its `enforce`
mode does not authorize rollout. Do not add consumers to it.

## Source evidence and uncertainty

PR #968 merged at `b8e3e5a4fac0a0a7c70cd6b2a3b9394e0cbbac01`.
The inspected checkout `08255ad355a3ab63f0c04a214c3c230432552dfe` pins
`pydantic-ai-slim[...,typesafe]==2.53.0` in `pyproject.toml`.
The merged [contract spike](../../api/tests/unit/services/test_decision_model_contract.py)
contains injected fake-backend coverage for aggregate typed requests, nested
fields, metadata, unsupported schemas, malformed answers, backend errors and
forwarded settings. No native `get_score` entry point exists in that contract;
described enum rubrics provide score fields.

This is merged source evidence, not a live TypeSafe result or a fresh test run.
The issue's historical version-introduction claim remains unverified.
Forwarding a timeout setting does not prove an enforced wall-clock deadline.
Scores/confidence remain model preference until held-out calibration demonstrates
otherwise. Missing provider metadata stays absent.

## Ordered delivery gates

1. **MIDT-274 — dependency and contract spike.** Source prerequisite merged in
   #968. Preserve its boundary; verify live capability separately.
2. **MIDT-273 — smallest typed proof.** Implement one async service call using
   an injected `DecisionModel` and `TicketRoutingDecision`. Return validated
   output with decision/schema/policy versions, provider/model identity, latency,
   canonical input hash and available raw per-field metadata. Include organization
   and policy version in hash identity; do not persist raw state or treat a hash
   as anonymization. Bound execution time; cancellation/malformed output produce
   no successful advisory result. Exercise a synthetic reusable workflow in shadow.
3. **MIDT-272 — workflow/tool observation.** Add wrappers and expose the reusable
   workflow through existing granted-workflow agent tools. Use existing events
   and tables for explicitly advisory observations, excluding sensitive state.
   Verify organization isolation, tool grants, approvals and absence of actions.
   Do not add a separate dispatcher, orchestration engine or job system.
4. **MIDT-271 — compatibility.** Verify TypeSafe live behavior using synthetic
   inputs and existing access, then one Laya-compatible backend at the same
   Pydantic boundary. Record unsupported schemas/score semantics. Provider
   changes must not require editing workflow definitions.
5. **MIDT-270 — evaluation.** Compare the ordinary deterministic workflow baseline
   with model-backed inference on frozen, separately held-out cases. Platform
   Reviewer remains the review gate; benchmark ownership is not inferred from
   migration. Models stay advisory regardless of benchmark results.

## Fixture policy and evaluation

The baseline policy maps an explicit structured category to the organization's
resolver owner; absent category or missing mapping yields `human`. Suspected
compromise, organization-defined major impact, or a `human` owner requires
escalation. Rules inspect structured state and organization configuration only.
Free-text semantics are the modeled experiment: paraphrases, negation, ambiguity
and adversarial instructions must not become policy authority. Review conflicting
text/structured-state cases before expanding the policy; initial cases keep those
signals consistent.

Include identical ticket state under two synthetic organization policies to catch
global-policy assumptions. Every case has gold output, acceptable alternatives,
risk weight, provenance/version and `autonomy_eligible=false`. Gold labels are
authored from synthetic policy, not production outcomes. Risk weights are
priorities for diagnosis, not authorization or accepted false-autonomy limits.

Freeze a disjoint held-out set and checksum before calibration. Report per-field
and joint accuracy, macro F1, abstention, option-order flips, paraphrase stability
and error counts by risk tier. Compute Brier/ECE only for defined probability
targets with adequate observations; label unavailable metrics. Shadow fixtures
cannot establish an acceptable action-driving false-autonomy rate. Later evaluation
must define autonomy-positive labels, denominator, thresholds and uncertainty.

Benchmark Foundry and self-hosted bounded-choice paths with exact model/revision
parity where feasible. Verify each endpoint's logit/logprob capabilities. Record
revision, quantization, runtime/backend, hardware, context, threads, KV/cache
settings, cold/warm p50/p95/p99, throughput, resident memory and cost per 1k
choices. Missing measurements, billing inputs and hardware rows must be explicit.

CPU is first class: compare 1–2B and approximately 4B models, a conventional
transformer and a newer architecture on the same host. Compare llama.cpp and
OpenVINO CPU with matching artifacts where possible; measure 5/10/20 questions
with prefix reuse enabled/disabled. Add Intel iGPU/NPU only with compatible
hardware and verified model/quantization support. Separate semantic errors from
conversion/kernel/runtime failures. Public runners need pinned revisions,
licensed fixtures and reproducible commands. This tiny corpus is insufficient
for the benchmark acceptance criteria.

## Verification and continuation

This update is static preparation under delivery lane 1. The typed proof is a
lane 4 experiment and crosses execution/tenant boundaries requiring their gates.
Develop against the exact worktree's debug application, record host/worktree/
Compose ownership, exercise success/failure through its API/workflow, then run
focused `./test.sh` coverage and `./test.sh quality api`. Cover wall-clock timeout,
invalid output, aggregate request, provider substitution, organization isolation
and no action from advisory output. Hosted synthetic checks are opt-in and
separate from injected-backend tests.

Before publication, commit a clean candidate containing current `origin/main`
and run `./test.sh pre-pr`; preserve CI, review and merge-queue requirements.
Clean up only task-created resources with verified ownership.

On 2026-10-12 the required lane `pve-t340.netbird.cloud` still failed DNS lookup.
No test VM, runtime endpoint or live-provider proof was established in this
continuation. `codex-remote-02` already had two test Compose projects and nearly
full swap; do not start another heavy suite or alter those resources. Resume
runtime work when access to the prescribed test lane is restored.

Later human decisions: accepted per-category false-autonomy limits, real-data
use/hosted transfer, hardware/hosting spend and action-driving rollout. The first
category and synthetic-data choice are already settled.
