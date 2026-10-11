# Bazel evaluation spike

Tracking issue: #952

## Purpose

Evaluate whether Bazel should become Bifrost's authoritative **structural dependency graph** and build/test orchestration layer.

This spike is intentionally additive. It must not require Bazel for normal contributors, alter production deployment, or remove the existing affected-test planner until we have evidence that the Bazel graph is both useful and maintainable.

## Current state

Bifrost already performs repository-aware dependency analysis in `api/scripts/plan_affected_tests.py`.

That planner currently:

- parses Python imports
- parses TypeScript imports
- builds forward and reverse dependency relationships
- identifies impacted source
- selects affected unit and E2E tests
- models FastAPI route/request relationships
- recognizes MCP and browser-integration boundaries
- broadens validation when the graph is uncertain

CI consumes the planner output before running test lanes.

This makes Bifrost a good candidate for a Bazel experiment because we can compare Bazel's declared graph against a working production heuristic rather than evaluating Bazel in the abstract.

## Proposed responsibility boundary

### Bazel

Bazel should be evaluated as the source of truth for relationships that are fundamentally structural:

- Python package/module targets
- TypeScript/React targets
- unit-test targets
- generated-code targets
- external package dependencies
- cross-component source relationships
- deployable artifact composition
- target visibility / architectural boundaries
- forward dependency queries
- reverse dependency / blast-radius queries

### Existing Bifrost planner

The existing planner should retain responsibility for semantic or runtime relationships that Bazel cannot safely infer from ordinary build dependencies:

- FastAPI route-to-E2E ownership
- literal HTTP request-path relationships
- MCP conformance behavior
- runtime/dynamic wiring
- fail-closed behavior where dynamic imports or graph uncertainty exist

A successful result may allow us to delete part of the custom structural import analysis while keeping these Bifrost-specific rules.

## Package-resolution policy

Do **not** make Bazel the package-version authority during this spike.

Python remains:

```text
pyproject.toml
    |
    v
pip-compile
    |
    v
requirements.lock
```

Client remains:

```text
client/package.json
    |
    v
npm
    |
    v
client/package-lock.json
```

Dependabot remains responsible for proposing dependency updates.

The experiment should consume these existing dependency declarations/locks into Bazel where practical rather than establishing a parallel version-resolution workflow.

## Initial vertical slice

Prefer a small but meaningful slice with all of the following:

1. one backend Python package/service target
2. its direct internal dependencies
3. its external Python dependencies
4. its unit tests
5. one API/router or semantic boundary that the existing planner understands
6. one downstream deployable or generated artifact when practical

Avoid starting with a broad `glob()` over the entire backend. The experiment is meant to measure whether explicit targets provide useful architectural information.

## Cross-component candidate

The embedded client SDK is a particularly useful test case.

The API image currently copies files from `client/src/lib/app-sdk/` plus `sdk-contract.json` into the Python SDK package used by the backend image.

Model this dependency explicitly if the initial slice reaches it:

```text
client app-sdk target
        |
        v
embedded SDK package
        |
        +--> API image
        |
        +--> worker image
```

A change to the client SDK should then have an inspectable path to the affected backend artifact(s).

## Questions the spike must answer

### Dependency graph

Can Bazel accurately answer:

```bash
bazel query 'deps(<target>)'
```

and:

```bash
bazel query 'rdeps(//..., <target>)'
```

for useful Bifrost components?

### Architectural policy

Can we encode at least one meaningful dependency boundary using Bazel visibility or equivalent mechanisms and demonstrate that a forbidden dependency fails?

### Affected tests

For a representative set of changes, compare:

- current `plan_affected_tests.py` output
- Bazel structural reverse dependencies
- selected tests
- missed dependencies
- unnecessary tests

### Maintenance burden

Record:

- number of BUILD targets/files required
- whether target metadata can be generated safely
- how much manual dependency declaration is necessary
- behavior when source files are added or moved
- agent ergonomics when adding dependencies

### Build/reproducibility value

Determine whether Bazel adds practical value for:

- local repeatability
- CI caching
- container/image construction
- generated artifacts
- future remote caching/execution

Do not count theoretical capability as a success unless the prototype demonstrates it on Bifrost.

## Suggested historical comparison cases

Use real repository history where practical:

- isolated backend leaf change
- shared backend utility/service change
- FastAPI router/API-contract change
- client-only change
- shared client SDK change
- dependency lockfile change
- dynamic-import/uncertain case

For each case capture something like:

| Case | Existing planner | Bazel structural impact | Correct? | Notes |
| --- | --- | --- | --- | --- |
| backend leaf | | | | |
| shared helper | | | | |
| router contract | | | | |
| client only | | | | |
| app SDK | | | | |
| lockfile | | | | |
| dynamic/uncertain | | | | |

## CI strategy

The first implementation should run **in parallel** with existing CI.

Do not replace `affected-test-plan` yet.

A reasonable first CI lane is diagnostic-only:

1. bootstrap Bazel
2. validate the Bazel graph
3. run the selected spike targets/tests
4. emit dependency/impact information as an artifact or job summary
5. compare against the existing planner

The existing planner remains authoritative during the experiment.

## Agent ergonomics

One explicit goal is to determine whether coding agents benefit from a declared graph.

An agent should eventually be able to answer questions such as:

```text
What depends on this target?
Which tests cover its structural dependents?
Which deployable artifacts contain it?
Is this dependency edge allowed?
```

without implementing its own repository-wide import analysis.

If Bazel does not materially improve those workflows, that is evidence against broader adoption.

## Exit criteria

### Continue toward incremental adoption if

Bazel demonstrates meaningful improvement in several of:

- dependency/blast-radius accuracy
- architectural enforcement
- affected-test selection
- cross-language/cross-component modeling
- agent-facing repository queries
- reproducibility
- CI/local caching

and the BUILD metadata burden is acceptable.

### Stop the experiment if

- BUILD metadata becomes a second source of truth that routinely drifts
- Python/TypeScript developer ergonomics materially worsen
- affected-test accuracy is no better than the existing planner
- semantic/runtime relationships dominate enough of the graph that Bazel adds little
- the practical benefits do not justify another required toolchain

## Non-goals

This draft does not propose:

- full-repository Bazel migration
- replacing pip-compile
- replacing npm
- replacing Dependabot
- deleting `plan_affected_tests.py`
- changing production deployment
- making Bazel mandatory for contributors

Those decisions should follow the measured results of this spike.
