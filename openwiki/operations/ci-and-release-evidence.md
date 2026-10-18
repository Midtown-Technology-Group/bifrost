---
type: operations-guide
title: CI Selection, Quality Gates, and Release Evidence
description: Explains affected-test planning, the CI acceptance graph, staged Sonar evidence, enforcement-chain ownership, and release-package checks without conflating plans, test execution, or deployment.
tags: [ci, tests, quality-gates, release, provenance]
sources:
  - id: openwiki-source-164e2da859b5277df81c7d94
    resource: repo://.github/workflows/ci.yml
  - id: openwiki-source-99c58eb5db843cd56a291c2c
    resource: repo://.github/workflows/openwiki-refresh.yml
  - id: openwiki-source-9956d5e26bb8ccda5a071263
    resource: repo://.github/workflows/release-tag.yml
  - id: openwiki-source-9a0b9d52e61a1be00f5aa5fc
    resource: repo://.github/workflows/sonar-coverage.yml
  - id: openwiki-source-8037e2358a2c4f9b2c722a11
    resource: repo://AGENTS.md
  - id: openwiki-source-3c58c59d9fb34466e7323eba
    resource: repo://api/scripts/plan_affected_tests.py
  - id: openwiki-source-ad33f5f7987cd9809ea2c20e
    resource: repo://api/tests/unit/scripts/test_plan_affected_tests.py
  - id: openwiki-source-77d022d88d2044e192540b6a
    resource: repo://scripts/ci/enforce_closure.py
  - id: openwiki-source-5a11cbd768e254bddf348604
    resource: repo://scripts/release/accept_release_images.py
  - id: openwiki-source-2ef831697b2d4c996122cbe9
    resource: repo://scripts/release/release_manifest.py
  - id: openwiki-source-2b82f9c45eb5d5013568a030
    resource: repo://scripts/sonar/preflight.py
generated: { by: "bifrost-atlas", at: "2026-10-18T05:33:14.512Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T05:33:14.512Z
---

# CI Selection, Quality Gates, and Release Evidence

The CI workflow connects a source-diff plan to quality and test lanes, then uses distinct release workflows for reviewed version preparation, tag verification, package construction, and publication. A selection plan is not a test result; a source-coverage artifact is not a quality-gate result; and package acceptance does not prove a production deployment. See [development and verification](development-and-verification.md), [release process](release-process.md), and [data contracts](../architecture/data-and-contracts.md).

## Affected test plan

For pull requests, merge groups, and pushes with a usable base commit, `affected-test-plan` checks out full history and asks `api/scripts/plan_affected_tests.py` to compare that base with the exact candidate head. A missing or all-zero base forces comprehensive planning, and CI uploads the resulting JSON plan for review ([plan invocation and artifact](../../.github/workflows/ci.yml#L135-L171)). The planner models Python and TypeScript dependencies in both directions and emits lane modes, selected targets, and shard matrices; its summary records the selected scope and reasons ([planner contract](../../api/scripts/plan_affected_tests.py#L884-L967)).

The planner is conservative: an empty diff, deletion, high-risk or CI-owned path, or unmodelled path produces comprehensive scope. Its supported-path checks and fallback are in the implementation, not an informal assumption that an unfamiliar file is safe to ignore ([fail-closed selection](../../api/scripts/plan_affected_tests.py#L744-L774)). Delivery-lane guidance documents additional conservative fallback cases, including dependency/container inputs, migrations, contracts, mixed backend/client source, parse errors, excessive fan-out, and reverse dependency closures without graph-owned tests ([delivery-lane planner policy](../../docs/dev/delivery-lanes.md#L24-L41)). The plan artifact says what CI intends to run; only the completed lane and its retained results provide execution evidence.

The planner recognizes documentation-only changes in root `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `GOVERNANCE.md`, `CODE_OF_CONDUCT.md`, `README.md`, `SECURITY.md`, and `LICENSE`; all paths under `docs/`, `.agents/`, `.claude/`, `.codex/`, and `plugins/bifrost/skills/`; OpenWiki Markdown; JSON Claims under `openwiki/.claims/`; and exactly `openwiki/.last-update.json` and `openwiki/.page-manifest.json` ([documented roots](../../api/scripts/plan_affected_tests.py#L38-L54), [OpenWiki rules](../../api/scripts/plan_affected_tests.py#L251-L265)). A pure documentation diff produces `docs-only` scope with all test lanes marked `skip`; this is plan selection, not a successful test result ([scope construction](../../api/scripts/plan_affected_tests.py#L761-L815), [regression test](../../api/tests/unit/scripts/test_plan_affected_tests.py#L211-L223)).

Policy and unmodelled paths remain comprehensive: `.openwikiignore`, `openwiki/.run.json`, OpenWiki files outside the explicit Markdown, Claims JSON, and checkpoint allowlist, `.github/` changes, and edits to the planner itself do not qualify as documentation. Any deletion also forces comprehensive validation. When OpenWiki Markdown accompanies an API source change, the planner stays in `affected` scope and still selects dependency-backed tests; adding OpenWiki Markdown to a CI workflow change remains comprehensive ([comprehensive and fallback rules](../../api/scripts/plan_affected_tests.py#L58-L75), [change classification](../../api/scripts/plan_affected_tests.py#L761-L815), [failure and mixed-scope tests](../../api/tests/unit/scripts/test_plan_affected_tests.py#L226-L285)).

## Quality and test lanes

`Lint & Type Check` validates action pins and MTG CI boundaries, runs repository regression checks, and conditionally runs API Ruff/Pyright and client TypeScript/ESLint for the selected targets. Comprehensive mode widens those checks to the full surface; skip mode is reported explicitly when neither API nor client source lies in the affected closure ([lint and quality lanes](../../.github/workflows/ci.yml#L463-L612)). Client unit tests use Vitest, while API unit tests use `test.sh` and a Docker test stack; the comprehensive API unit lane preserves coverage XML as an artifact ([unit jobs](../../.github/workflows/ci.yml#L618-L749)).

API E2E and browser E2E use separate Docker stacks and sharded jobs. The required aggregate `E2E Tests` check verifies planning and prerequisite results, then accepts a skipped shard only when the corresponding plan explicitly selected `skip`; a failure, cancellation, or unexpected skip blocks the aggregate ([API E2E matrix](../../.github/workflows/ci.yml#L829-L850), [browser matrix](../../.github/workflows/ci.yml#L958-L1028), [aggregate gate](../../.github/workflows/ci.yml#L1062-L1111)). `docs/dev/ci-test-sharding.md` explains whole-file Playwright sharding, independent backend stacks, and that timing weights affect allocation rather than test selection ([sharding contract](../../docs/dev/ci-test-sharding.md#L1-L19)).

The test jobs prefer immutable CI images built from `main` and bind-mount the current checkout so the source under test remains the candidate tree; when image inputs change, the preparation helper rebuilds the relevant test images ([test image preparation](../../.github/workflows/ci.yml#L696-L712), [image input detection](../../scripts/ci/detect-test-image-inputs.sh#L1-L80)). On a full API E2E run the workflow also retains the OpenAPI document served by that exact test stack, which is separate evidence from generated client declarations ([served schema capture](../../.github/workflows/ci.yml#L875-L890)).

## Sonar evidence and enforcement ownership

The reusable Sonar coverage workflow binds a report run to exact head/base commits, gathers comprehensive Python, client, and repository Node coverage, validates source inventory, and retains a manifest plus diagnostics. The preflight labels unreported authored files and explicitly records that a validated report does not prove tests succeeded, full line coverage, scanner acceptance, or a Sonar quality gate ([exact-source context and coverage collection](../../.github/workflows/sonar-coverage.yml#L48-L105), [manifest limitations](../../scripts/sonar/preflight.py#L705-L720)). In `report-only` mode the workflow states that it did not submit analysis; actual scan submission is a separately activated path with its own eligibility conditions ([report-only disposition and scan condition](../../.github/workflows/sonar-coverage.yml#L245-L310)).

CI also audits whether the workflows, test planner, coverage producer, and scanner configuration are covered by CODEOWNERS. `scripts/ci/enforce_closure.py --mode report` is deliberately advisory: the script says `.github/CODEOWNERS` is empty until the P0 ownership boundary lands, while `--mode enforce` fails for missing ownership ([ownership-mode contract](../../scripts/ci/enforce_closure.py#L1-L19), [enforcement result](../../scripts/ci/enforce_closure.py#L219-L240)). The workflow runs report mode alongside regression tests ([CI slice checks](../../.github/workflows/ci.yml#L506-L513)); that report is not evidence that required owner review is enforced.

## Release preparation, tag gates, and artifact acceptance

The MTG Release PR workflow prepares a complete reviewed interval from `main`, updates release metadata and notes, and opens or refreshes a signed release PR. It requires the checkout to remain the current `origin/main` and verifies the generated PR commits are signed; a maintainer still reviews and merges it through the native queue ([release preparation](../../.github/workflows/release-draft.yml#L21-L65), [signature and checks](../../.github/workflows/release-draft.yml#L92-L123)). The Release tag workflow waits for successful exact-main CI, or an explicitly supplied successful main-push run for recovery, then calls `automatic-release.py publish` for that run's exact source SHA ([tag workflow source binding](../../.github/workflows/release-tag.yml#L24-L50)). It creates or reads back the expected tag and dispatches CI on that immutable tag; it does not choose a deployment target ([publish lifecycle](../../scripts/release/automatic-release.py#L443-L518)).

On a version tag, `Verify Release Manifest` runs only after unit, aggregate E2E, lint, and MCP conformance succeed; it checks strict version syntax, refuses to rebuild an already published immutable release, and requires plugin manifest versions to match the tag ([tag prerequisites](../../.github/workflows/ci.yml#L1548-L1616)). The three versioned API, worker, and client images are then built, and release creation waits for all of them ([release image gate](../../.github/workflows/ci.yml#L1618-L1628), [release dependencies](../../.github/workflows/ci.yml#L1900-L1914)).

For a stable MTG release, the workflow constructs a manifest from exact source, run identity, and image digests, then checks those exact image contents in a read-only, network-isolated container. The smoke verifies baked versions, matching server/CLI contract version, migration heads, bundled CLI metadata, and the client static entrypoint. The script explicitly describes this as bounded package acceptance, not production or canary readiness ([manifest and acceptance invocation](../../.github/workflows/ci.yml#L1986-L2014), [acceptance checks and boundary](../../scripts/release/accept_release_images.py#L1-L75)). It attests and uploads the accepted manifest, signs the source tarball, then verifies required assets before publishing the immutable GitHub Release ([release evidence artifacts](../../.github/workflows/ci.yml#L2016-L2043), [publication checks](../../.github/workflows/ci.yml#L2065-L2125)). Production deployment remains a separate infrastructure workflow and authorization boundary.

## Reading CI evidence

For a candidate, inspect the exact source SHA, affected-plan artifact and reasons, required aggregate checks, selected lane results, and their retained artifacts. Treat an explicit skip as scoped evidence only when the plan and aggregate gate agree. Do not infer broad test completion from a plan, source coverage from a successful report upload, a Sonar gate pass from staged evidence, or deployment readiness from accepted package contents.

## Ecosystem knowledge refresh notification

The committed `openwiki-refresh.yml` workflow runs on pushes to `main`; its `notify` job is eligible only when `github.repository` is exactly `Midtown-Technology-Group/bifrost` and the repository variable `OPENWIKI_MAINTENANCE_ENABLED` is exactly `true`. Until that opt-in is set, the automatic notification job stays disabled. The workflow requests read-only repository contents permission, serializes notifications without cancelling an in-flight run, and has a five-minute job limit ([trigger, activation gate, and job boundary](../../.github/workflows/openwiki-refresh.yml#L1-L21)). It compares the push's `before` and commit SHAs when `before` is a valid nonzero SHA. If that base is absent/invalid, it conservatively treats source as changed. With a comparison, any changed path outside `openwiki/` marks source as changed; a result containing 300 or more files is also treated conservatively because GitHub caps the comparison list ([classification](../../.github/workflows/openwiki-refresh.yml#L28-L38)).

When the activation gate is satisfied and the job runs, it requires the repository secret `OPENWIKI_DISPATCH_TOKEN`; if the secret is absent, the job fails with `OPENWIKI_DISPATCH_TOKEN is not enrolled; no source event was sent.` ([activation gate and missing-token behavior](../../.github/workflows/openwiki-refresh.yml#L17-L27)). Every accepted push dispatches `bifrost-docs-refresh` to the private Bifrost operations repository with member `bifrost` and the commit SHA. A push classified as source-changing also dispatches `openwiki-source-updated`; a wiki-only change therefore requests a documentation refresh without claiming a source update ([event selection and dispatch payload](../../.github/workflows/openwiki-refresh.yml#L39-L49)). This source does not prove that the repository variable or token is currently configured, that the job ran, or that a notification was accepted.

For unfamiliar cross-repository work, `AGENTS.md` points contributors to the private architecture atlas and focused local OpenWiki retrieval, and links a portable reader/maintenance guide that covers linked workspaces and CLI access across harnesses ([shared ecosystem guidance](../../AGENTS.md#L67-L69)). These are navigation pointers; claims about the private atlas, CLI implementation, or another repository require checking those sources directly.

**Status boundary:** this page documents committed workflow source and repository guidance. Automatic notifications remain disabled unless the exact repository identity and opt-in variable condition are met. Variable enrollment, token enrollment, successful dispatches, and receiver adoption remain pending verification; source presence is not evidence that the optional integration is active or adopted.
