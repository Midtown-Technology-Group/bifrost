---
type: runtime-architecture
title: Model Inference, Decisions, and App Code Compilation
description: How Bifrost resolves configured model profiles, validates bounded decision outputs, generates embeddings and compiles app code, with the separate identity and policy boundary of the Codex Gateway.
tags: [ai, models, inference, embeddings, app-compiler, codex-gateway]
sources:
  - id: openwiki-source-025f03e2038de16ac723226a
    resource: repo://api/src/routers/llm_config.py
  - id: openwiki-source-553282f482dbedcb28844b67
    resource: repo://api/src/services/ai_model_service.py
  - id: openwiki-source-70ebc17e7ae994df95ac965f
    resource: repo://api/src/services/app_compiler/__init__.py
  - id: openwiki-source-f15b5c5edca79d522aae26b9
    resource: repo://api/src/services/app_compiler/compile.js
  - id: openwiki-source-88629b3ce46ef58c38910423
    resource: repo://api/src/services/codex_gateway/policy.py
  - id: openwiki-source-1aa006d7fdcf1dec190bb0fb
    resource: repo://api/src/services/codex_gateway/runtime.py
  - id: openwiki-source-030fb66f0d51534a34a1b37d
    resource: repo://api/src/services/decision/__init__.py
  - id: openwiki-source-f5a70868ba4ed0e50c1f0da9
    resource: repo://api/src/services/decision/base.py
  - id: openwiki-source-6fd91e0776f8ae96cbf4f2ba
    resource: repo://api/src/services/decision/engine.py
  - id: openwiki-source-a04d3c54a3659fcb62b3f64d
    resource: repo://api/src/services/decision/factory.py
  - id: openwiki-source-aab3f6b8f090b53678d282ef
    resource: repo://api/src/services/decision/rule_provider.py
  - id: openwiki-source-4c0951491f577e62c9ac7ce0
    resource: repo://api/src/services/editor/file_filter.py
  - id: openwiki-source-a58d23c4424ec5c1a8d3fe31
    resource: repo://api/src/services/editor/search.py
  - id: openwiki-source-f58551a0f79443c6a34076bf
    resource: repo://api/src/services/embeddings/openai_client.py
  - id: openwiki-source-842b19130456c1943fb9f3f7
    resource: repo://api/src/services/embeddings/url_safety.py
  - id: openwiki-source-8e9b3001f559db5ac30c964a
    resource: repo://api/src/services/llm/base.py
  - id: openwiki-source-01fbb390a14aa90b533959c2
    resource: repo://api/src/services/llm/factory.py
  - id: openwiki-source-6c14c82819f7cbd5d231ff4b
    resource: repo://api/src/services/llm/pydantic_client.py
  - id: openwiki-source-fa48f3f8118bf6b9ca1a2c1f
    resource: repo://api/tests/unit/services/decision/test_decision_engine.py
  - id: openwiki-source-94540a6e7e5513e32272b7ef
    resource: repo://api/tests/unit/services/test_codex_gateway_policy.py
  - id: openwiki-source-65446d609b8c8e81b25a0f21
    resource: repo://api/tests/unit/services/test_codex_gateway_runtime.py
  - id: openwiki-source-923719c8bce14f59a1f24f95
    resource: repo://api/tests/unit/services/test_editor_search.py
  - id: openwiki-source-a88afd3aa6979e205602e0cd
    resource: repo://api/tests/unit/services/test_embeddings_openai_client.py
  - id: openwiki-source-2d037380cd1df4f8d17add39
    resource: repo://api/tests/unit/services/test_embeddings_url_safety.py
  - id: openwiki-source-e3a467cb3e7a54b7974c0e65
    resource: repo://api/tests/unit/test_app_compiler.py
generated: { by: "bifrost-atlas", at: "2026-10-18T02:51:00.673Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T02:51:00.673Z
---

# Model Inference, Decisions, and App Code Compilation

This page covers several services that share AI-related configuration but have distinct trust and execution boundaries. Model profiles feed provider-neutral LLM clients; decision inference is a typed primitive surface; embeddings use an OpenAI-compatible client; the app compiler transforms source but does not execute it; and the Codex Gateway is a separately authenticated proxy for a user-linked upstream account. Follow [agent chat](agents-and-chat.md) for conversation orchestration, [knowledge and memory](knowledge-and-memory.md) for embedding consumers, [apps and SDK](../workflows/apps-and-sdk.md) for app authoring/publishing, and [security and audit](../operations/security-and-audit.md) for platform-wide controls.

## Provider connections and model profiles

The platform-admin AI settings API manages provider connections, reusable model profiles, and assignment keys. The API returns connection summaries with a boolean indicating whether a key is set; the service encrypts provider keys at rest and decrypts them only while building runtime configuration. The router is guarded by `RequirePlatformAdmin`. A connection verification request tests supplied connection data and returns a structured result; successful creation/update uses the service and commits, while invalid configuration is rolled back and returned as a client error. These endpoints describe configuration handling, not current provider availability. ([`ai_models.py`](../../api/src/routers/ai_models.py#L33-L37), [`ai_model_service.py`](../../api/src/services/ai_model_service.py#L85-L105))

Runtime callers resolve a profile by ID/name or through an assignment such as `primary`, `summarization`, `tuning`, or `chat_default`. `AIModelService.resolve_chain` follows profile failover pointers in order, detects cycles, and caps the chain at three profiles. Profiles that fail to resolve are skipped with a warning; if none resolve, the head profile's error is raised. `get_llm_client` wraps the resulting configs in the provider-neutral `PydanticAIClient`; fallback models use their own profile settings, while a per-call model override applies only to the primary provider. This keeps call sites on the existing `BaseLLMClient` contract while provider construction remains behind `agent_runtime.model_factory`. ([`ai_model_service.py`](../../api/src/services/ai_model_service.py#L54-L66), [`ai_model_service.py`](../../api/src/services/ai_model_service.py#L221-L234), [`ai_model_service.py`](../../api/src/services/ai_model_service.py#L236-L287), [`llm/factory.py`](../../api/src/services/llm/factory.py#L36-L105), [`pydantic_client.py`](../../api/src/services/llm/pydantic_client.py#L47-L93))

The chain bound also limits accumulated failover cost and latency: each fallback runs after the previous candidate exhausts its transport budget. Configuration can be resolved from explicit profile selectors or an assignment key; a missing assignment or invalid profile is an error rather than a hidden provider fallback. Focused factory tests cover profile selection, validation-error preservation, internal-error sanitization, and returning the provider-neutral adapter. ([`ai_model_service.py`](../../api/src/services/ai_model_service.py#L63-L66), [`ai_model_service.py`](../../api/src/services/ai_model_service.py#L289-L365), [`llm/test_factory.py`](../../api/tests/unit/services/llm/test_factory.py#L14-L127))

## Bounded decision inference

`DecisionEngine` exposes four typed operations—boolean, choice, score, and rank—over an injected `DecisionProvider`. It validates questions and option sets, then rejects malformed provider output: choices must be among the supplied options, scores and optional confidence must be in `[0, 1]`, and rankings must be a permutation of the options. Results include provider/method/model metadata, latency, and a stable hash of the decision inputs. The supported modes are `off`, `shadow`, and `enforce`: `off` raises `DecisionEngineDisabled`, only `enforce` marks a result authoritative, and shadow results must not alter production behavior. Deterministic authorization and policy enforcement remain outside this inference surface, including when a result is authoritative. ([`decision/__init__.py`](../../api/src/services/decision/__init__.py#L1-L8), [`decision/engine.py`](../../api/src/services/decision/engine.py#L68-L189), [`decision/base.py`](../../api/src/services/decision/base.py#L17-L30), [`decision/base.py`](../../api/src/services/decision/base.py#L69-L82))

The provider is an explicit extension seam. The factory defaults the mode from `Settings.decision_inference_mode`; the implementation identifies `RuleProvider` as the shipped Phase 1 provider. Rules are registered by question and primitive kind, and an unknown question or kind mismatch fails closed. This is deterministic decision behavior, not evidence that a model-backed decision provider is configured. The focused engine tests exercise all three modes, provenance, invalid inputs, and invalid provider outputs. ([`decision/factory.py`](../../api/src/services/decision/factory.py#L1-L27), [`rule_provider.py`](../../api/src/services/decision/rule_provider.py#L1-L30), [`rule_provider.py`](../../api/src/services/decision/rule_provider.py#L101-L135), [`test_decision_engine.py`](../../api/tests/unit/services/decision/test_decision_engine.py#L73-L120))

## Embedding configuration and requests

Embedding configuration is resolved from a saved AI connection and explicit embedding model settings. The selected connection must map to an OpenAI-compatible client; otherwise resolution fails. The embedding client lazily loads the OpenAI SDK, returns vectors in input order, and uses float encoding. Empty input avoids a request. Inputs larger than 2,048 are truncated by this client; if a provider returns an empty successful response, it recursively halves the batch and raises a typed error if a single-item request is still empty. This behavior is distinct from any embedding dimensionality contract enforced by callers or stored vectors. ([`ai_model_service.py`](../../api/src/services/ai_model_service.py#L1134-L1155), [`embeddings/factory.py`](../../api/src/services/embeddings/factory.py#L1-L34), [`openai_client.py`](../../api/src/services/embeddings/openai_client.py#L62-L129), [`test_embeddings_openai_client.py`](../../api/tests/unit/services/test_embeddings_openai_client.py#L51-L125))

Admin endpoints validate configured embedding endpoints before outbound tests or model discovery. The URL guard permits HTTP or HTTPS, requires a hostname, and rejects host resolutions containing private, loopback, link-local, reserved, multicast, or unspecified addresses. Exact hostnames listed in `EMBEDDING_ALLOWED_HOSTS` bypass the address-class check for explicitly trusted internal services. The validator returns a normalized URL, and the model-list helper uses that sanitized value before making the HTTP request. DNS is checked at validation time, so the source documents a validate/use time-of-check-to-time-of-use window; the allowlist is a deliberate trust exception. ([`llm_config.py`](../../api/src/routers/llm_config.py#L206-L226), [`llm_config.py`](../../api/src/routers/llm_config.py#L510-L525), [`llm_config.py`](../../api/src/routers/llm_config.py#L565-L585), [`url_safety.py`](../../api/src/services/embeddings/url_safety.py#L40-L101), [`test_embeddings_url_safety.py`](../../api/tests/unit/services/test_embeddings_url_safety.py#L25-L104))

## App source compilation and CSS generation

`AppCompilerService` sends one file or a batch to a Node subprocess and maps each returned item into a success/error result with compiled source and export metadata. The compiler preprocesses `bifrost` imports to the injected `$` runtime, strips relative imports for runtime resolution, maps external imports to the dependency object, then runs Babel's React and TypeScript transforms and rewrites exports into runtime metadata. It compiles source; this path does not execute the app. A batch can return independent successes and syntax errors, while a missing Node runtime or subprocess/protocol failure is surfaced as a failed result for affected files. ([`app_compiler/__init__.py`](../../api/src/services/app_compiler/__init__.py#L34-L103), [`compile.js`](../../api/src/services/app_compiler/compile.js#L11-L59), [`compile.js`](../../api/src/services/app_compiler/compile.js#L122-L142), [`test_app_compiler.py`](../../api/tests/unit/test_app_compiler.py#L11-L61))

Tailwind generation extracts candidate classes from source string literals. The full pipeline can also process app CSS directives such as `@apply`, `@layer`, and `@theme`; the compatibility `config_path` is ignored because loading per-app Tailwind config would execute server-side JavaScript. The subprocess returns generated CSS or `None` on empty work/failure. Tests protect import preprocessing, per-file batch errors, arbitrary Tailwind values, CSS-only inputs, and subprocess failures. ([`app_compiler/__init__.py`](../../api/src/services/app_compiler/__init__.py#L106-L142), [`app_compiler/__init__.py`](../../api/src/services/app_compiler/__init__.py#L144-L195), [`test_app_compiler.py`](../../api/tests/unit/test_app_compiler.py#L63-L80))

## Codex Gateway identity and policy boundary

The Codex Gateway accepts an OpenAI-compatible Responses request, authenticates a Bifrost gateway key, and selects an active ChatGPT/Codex account for that same Bifrost user. Missing, revoked, ambiguous, or mismatched identities fail closed; there is no cross-user account fallback. Key model allow/deny policy runs before upstream dispatch, and denials are returned as structured OpenAI-style errors and logged. Audit metadata includes request and identity metadata but sensitive prompt/output previews are included only when their explicit capture flags are enabled. ([`runtime.py`](../../api/src/services/codex_gateway/runtime.py#L103-L187), [`policy.py`](../../api/src/services/codex_gateway/policy.py#L36-L74), [`policy.py`](../../api/src/services/codex_gateway/policy.py#L76-L130), [`policy.py`](../../api/src/services/codex_gateway/policy.py#L150-L187))

On an allowed request the runtime decrypts the selected account token, dispatches through an injected upstream client, enforces a 30-second timeout, converts transport failure/timeout into gateway errors, and writes a request log with outcome and timing. The default upstream client is explicitly a placeholder that returns `upstream_not_configured`; an interface and dispatch path therefore do not establish that an upstream subscription transport is configured. OAuth-cache parsing accepts bounded token and identity fields and derives an opaque fallback subject when the cache has no subject; it is an onboarding parser, not token refresh logic. Focused tests cover key authentication ordering, same-user selection, policy denials, token decryption errors, upstream failure/timeout, and secret non-disclosure. ([`runtime.py`](../../api/src/services/codex_gateway/runtime.py#L53-L73), [`runtime.py`](../../api/src/services/codex_gateway/runtime.py#L228-L313), [`oauth.py`](../../api/src/services/codex_gateway/oauth.py#L23-L120), [`test_codex_gateway_runtime.py`](../../api/tests/unit/services/test_codex_gateway_runtime.py#L221-L330), [`test_codex_gateway_policy.py`](../../api/tests/unit/services/test_codex_gateway_policy.py#L20-L115))

## Provider-neutral LLM service contract

`BaseLLMClient` is the shared service contract for both assembled completion and streaming: inputs use provider-neutral message, binary-file, and tool dataclasses; responses expose text/tool calls, finish reason, token/cache usage, provider cost, and model; streams emit typed delta, tool-call, done, or error chunks ([message/response types](../../api/src/services/llm/base.py#L15-L103), [abstract client operations](../../api/src/services/llm/base.py#L192-L256)). `PydanticAIClient` implements that contract by converting provider events and tool calls into the shared chunk type; provider SDK imports stay behind the LLM factory and are loaded when a client is built ([stream adapter](../../api/src/services/llm/pydantic_client.py#L140-L190), [configured client construction](../../api/src/services/llm/factory.py#L99-L130)). Output token limits resolve in one place: explicit request/agent override, then profile default, then Anthropic's required default or the DeepSeek-family safety cap, otherwise the provider default ([token limit precedence](../../api/src/services/llm/base.py#L105-L120), [resolution function](../../api/src/services/llm/base.py#L160-L189)). These are service contracts, not evidence that a provider endpoint is currently reachable.

## Editor search and workspace file policy

The editor search service queries indexed workspace file content and can include an immutable workspace-release overlay. Regex mode rejects patterns over 512 characters and structurally risky nested quantifiers, then uses a timeout-capable regex engine with a 50 ms per-line timeout; it returns bounded result counts and identifies the source authority in the response ([search limits](../../api/src/services/editor/search.py#L27-L31), [regex validation](../../api/src/services/editor/search.py#L134-L143), [search result and authority](../../api/src/services/editor/search.py#L221-L253), [response construction](../../api/src/services/editor/search.py#L324-L340)). Focused tests cover invalid and risky regex inputs and the search result contract ([editor search tests](../../api/tests/unit/services/test_editor_search.py#L100-L135), [nested-quantifier cases](../../api/tests/unit/services/test_editor_search.py#L200-L230)).

Workspace file filtering is a separate shared policy used for file uploads, listing, writes, and frontend rule export. The rules exclude metadata/dependency/cache directories, selected secret/system files including `.env`, binary extensions, and AppleDouble prefixes; `get_exclusion_rules()` returns the canonical lists for consumers ([exclusion set](../../api/src/services/editor/file_filter.py#L15-L52), [path check and exported rules](../../api/src/services/editor/file_filter.py#L55-L90), [rule export](../../api/src/services/editor/file_filter.py#L108-L123)). For a change, follow its callers across file APIs and workspace operations rather than assuming this search service itself filters every indexed file.

## Verification and boundaries

For a change to profile resolution or provider construction, start with `api/tests/unit/services/llm/test_factory.py` and the relevant `AIModelService` tests. Decision contract changes belong with `api/tests/unit/services/decision/`; embedding changes with the client, factory, endpoint-safety, and reindex tests; compiler changes with `api/tests/unit/test_app_compiler.py` and `test_app_compiler_deps.py`; gateway policy or transport changes with the gateway policy/runtime/OAuth tests. These tests establish local contracts only: they do not certify an external provider, model endpoint, Codex transport, or production deployment. See [CLI and MCP](../architecture/cli-and-mcp.md) and [data and contracts](../architecture/data-and-contracts.md) when a change crosses the public platform contract boundary.
