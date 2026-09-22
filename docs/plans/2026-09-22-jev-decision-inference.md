# Jev-style decision inference — phased delivery plan

Issue: #806. Goal: a provider-neutral `DecisionEngine` for bounded decisions
(`boolean` / `choice` / `score` / `rank`) over small open-weight models, with
shadow mode first, deterministic authorization kept outside the model, and a
benchmark program that isolates inference-method effects from model-quality
effects.

This plan splits #806 into PR-sized phases. Each phase is independently
mergeable and leaves main in a working, gated state.

## Design invariants (all phases)

- **Provider-neutral primitives.** Callers use `boolean` / `choice` / `score` /
  `rank`; they never know which provider, model, or runtime served the call.
- **Deterministic authorization and policy stay outside the model.**
  `api/shared/policies/` remains the only authority for authz/enforcement;
  decision results are advisory inputs unless an explicit, separate policy gate
  allows an action.
- **Shadow before enforce.** No model-backed decision alters production
  behavior until shadow-mode recordings have been reviewed and a threshold is
  chosen per decision category.
- **Metadata is mandatory.** Every result carries provider, model, inference
  method, model revision, input hash, latency, raw option scores, and an
  optional calibrated probability slot (calibration itself comes later, only
  after held-out outcomes exist).
- **Scores are model preference, not probability.** Calibration is a separate
  layer, never an alias for raw softmax/logit scores.
- **Gating.** Experimental surface is gated by the tri-state Settings pattern
  (`off|shadow|enforce`, precedent:
  `workspace_promotion_diagnostics_mode` in `api/src/config.py`). When the
  runtime feature-flag service (`docs/superpowers/specs/2026-09-18-runtime-feature-flags-design.md`)
  lands, migrate to it for per-org canary/kill-switch needs.

## Phases

### Phase 1 — Core abstraction (this work)

- `api/src/services/decision/` package:
  - `base.py`: `DecisionProvider` ABC, `ProviderDecision`, `DecisionResult`,
    `DecisionMeta`, error types.
  - `rule_provider.py`: deterministic `RuleProvider` (baseline + test double).
  - `factory.py`: `create_decision_engine(...)` reading the mode from
    `Settings.decision_inference_mode`.
  - `__init__.py`: public exports (mirrors `services/llm/`).
- `Settings.decision_inference_mode: off|shadow|enforce` (default `off`):
  - `off` → engine raises `DecisionEngineDisabled`; the surface does not exist.
  - `shadow` → compute results, stamp `authoritative=False`; callers must not
    act on them.
  - `enforce` → compute results, stamp `authoritative=True` (still subject to
    all deterministic policy gates).
- Unit tests covering primitives, validation fail-closed behavior, metadata
  (hash stability, latency presence), and mode semantics.
- **No** HTTP endpoint, persistence, or production call-site wiring yet.

### Phase 2 — Fixtures + benchmark harness

- BiFrost-specific decision fixture set (state, question, options, gold answer,
  acceptable alternatives, risk weight, provenance) modeled on
  `api/tests/e2e/fixtures/`, env-gated live-model execution modeled on
  `llm_setup.py`.
- Reproducible runner reporting accuracy/macro-F1, Brier, ECE, option-order
  flip rate, p50/p95 latency; `RuleProvider` is the deterministic baseline row.
- Wire public benchmark suites (JevBench, decision-model-benchmark,
  Bonsai/OpenJev) where license/automation permits.

### Phase 3 — Managed Foundry adapter

- Verify which Foundry endpoints expose usable logprobs before assuming parity;
  record findings in the issue.
- `FoundryProvider` behind the same ABC: constrained/structured generation
  path first; direct-logit only if the endpoint actually exposes logits.
- Same-model/revision comparison harness (generative vs constrained vs
  direct-logit) as specified in #806.

### Phase 4 — Self-hosted direct-logit adapter

- `SelfHostedLogitProvider` against a logit-capable runtime (vLLM / llama.cpp;
  decision: measure, don't assume).
- Shared-prefix/KV-reuse bundle tests (5/10/20 decisions over one state).

### Phase 5 — Runtime matrix benchmarks (CPU / OpenVINO / NPU)

- CPU-first scenarios and latency targets from #806; OpenVINO CPU vs llama.cpp
  on the same host; iGPU/NPU where hardware exists.
- Report separates model-quality limits from runtime/kernel limits.

### Phase 6 — Shadow integration + calibration + recommendation

- First shadow-mode call site (leading candidate: `agent_router.py`
  route-vs-DIRECT), durable decision recording (design: likely platform-job or
  lightweight decision-log; decide then, not now).
- Calibration layer once held-out outcomes exist; per-category confidence
  thresholds and false-autonomy analysis.
- Recommendation doc: viable for production shadowing or not; preferred
  provider/runtime/model; which categories stay advisory-only.

## Phase 1 acceptance (maps to #806)

- [x] Provider-neutral decision API behind an experimental gate.
- [x] Deterministic `RuleProvider` adapter exercises all four primitives.
- [x] Metadata envelope (provider, model, inference method, revision, input
      hash, latency, raw scores, optional confidence) on every result.
- [x] Shadow mode computes without granting authority; `off` fails closed.
- [ ] Open-weight 2–4B direct-logit path — Phase 4.
- [ ] Managed Foundry benchmark row — Phase 3.
- [ ] Fixture set, public runners, full metrics — Phase 2/5.
- [ ] Recommendation — Phase 6.

## Open questions carried from #806

- Foundry logprob availability per candidate model (verify in Phase 3).
- Best prefix-cache runtime for multi-question bundles over one state (Phase 4).
- Whether 2B suffices as default decision worker (Phase 2/5 results).
- Per-category autonomy thresholds and always-advisory classes (Phase 6).
