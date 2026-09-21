# Test Performance Notes

## Current test paths

`main` now uses the verb-style test runner and split CI jobs:

- `./test.sh stack up` starts the reusable test stack.
- `./test.sh unit` runs backend unit tests.
- `./test.sh e2e` runs backend E2E tests.
- `./test.sh client e2e` runs Playwright browser tests.
- `./test.sh ci` runs the isolated full local CI path, with backend unit and
  E2E suites in separate pytest processes before client unit and Playwright.

GitHub Actions runs Playwright on every pull request and merge-queue ref. The
client job is part of the required `E2E Tests` aggregate gate, uploads traces,
screenshots, videos, and the HTML report when it fails, and uses the baked
API/client/Playwright images from `main` unless an image input changed.
Playwright retries and skipped tests are prohibited so a green gate always
represents a clean first attempt.

PR117 is superseded by this smaller change because the previous branch also carried a stale OAuth fix and old `test.sh` edits. Current `main` already has the larger test runner refactor, so this PR keeps only the remaining low-risk speed knob.

## Playwright workers

Playwright worker count is controlled with `PLAYWRIGHT_WORKERS`.

- CI defaults to `2` workers.
- Local runs default to `4` workers.
- Set `PLAYWRIGHT_WORKERS=1` to restore the previous serialized CI behavior.
- Invalid values fall back to the default instead of failing config load.

When Playwright runs through Docker Compose, `playwright-runner` receives `PLAYWRIGHT_WORKERS` from the host and defaults to `2`.

## Benchmark commands

Use a clean stack for comparable timings:

```bash
Measure-Command { bash ./test.sh stack up }
Measure-Command { bash ./test.sh unit }
Measure-Command { bash ./test.sh e2e }
Measure-Command { bash ./test.sh client e2e }
```

Compare Playwright worker settings directly:

```bash
Measure-Command { $env:PLAYWRIGHT_WORKERS='1'; bash ./test.sh client e2e }
Measure-Command { $env:PLAYWRIGHT_WORKERS='2'; bash ./test.sh client e2e }
```

For a config-only check from `client/`:

```bash
PLAYWRIGHT_WORKERS=1 npx playwright test --list
PLAYWRIGHT_WORKERS=2 npx playwright test --list
PLAYWRIGHT_WORKERS=bogus npx playwright test --list
```


## Scoped CI fallback

Affected-test planning uses lane-level decisions. A backend uncertainty does
not by itself require every frontend unit test; missing browser ownership
requires broad browser coverage, not unrelated backend tests. Shared API
contracts remain conservative: backend and browser integration coverage expand
until compatibility can be proved. Migration, dependency, CI, and unmodelled
cross-cutting changes retain comprehensive validation. The plan artifact records
why each lane broadened. This does not bypass required checks or the nightly
full-suite backstop.

`./test.sh pre-pr` runs repository consistency, relevant quality and generated
API checks even when comprehensive unit/integration/browser lanes are deferred
to required CI. Affected lanes run their explicit targets; an empty target list
is an error. `--full` remains exhaustive. Regenerate the authoritative appendices
with `api/scripts/skill-truth/generate.py` in the supported Linux environment,
then run `scripts/sync-codex-skills.sh` and commit both before rerunning the clean
candidate gate. Do not submit one generated copy and wait for CI to identify
the other.
