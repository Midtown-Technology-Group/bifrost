# Dev Image Version Format

## Format

Dev builds (every push to `main`) produce images tagged with a semver
pre-release version of the form:

    <major>.<minor>.<next-patch>-dev.<commits-since-tag>

Example: latest release tag is `v0.8.0`, 47 commits since → `0.8.1-dev.47`.

When a release is cut (e.g. `v0.8.1`), the next merge to main produces
`0.8.2-dev.1`. The next dev cycle's floor is automatically whatever
release tag was last cut — no script changes required.

Both annotated and lightweight `vMAJOR.MINOR.PATCH` tags are valid. Dev versions
use **Midtown-Technology-Group/bifrost tags only** — see [VERSIONING.md](../VERSIONING.md).
Before the first stable MTG release (`v1.0.0`), the script bootstraps to
`1.0.0-dev.<commit-count>`.

## Where it's computed

- `scripts/compute-dev-version.sh --next-after <parent>` computes the candidate
  and main-publication version from commits since the stable tag plus one.
  Both image paths use this mode so the tested candidate and published images
  carry the same version.
- Invoking the script without arguments instead uses the exact HEAD committer
  timestamp as its dev sequence; this standalone mode is not the current
  candidate/main publication sequence. No stable tag bootstraps to
  `1.0.0-dev.<commit-count>`; malformed stable tags fail.
- `.github/workflows/ci.yml` candidate identity and "Compute candidate-compatible
  version" steps call the script and feed the output into
  `BIFROST_VERSION` and `VITE_BIFROST_VERSION` build args.
- Tested by `scripts/test-compute-dev-version.sh`.

## Tags pushed per main build

| Tag | Moves on each build? | Purpose |
|-----|---------------------|---------|
| `:0.8.1-dev.47` | No (immutable) | Versioned reference, sortable by Flux |
| `:dev` | Yes | Floating pointer for Keel + dev consumers |
| `:sha-1a2b3c4` | No (immutable) | Direct commit traceability |

Release builds on `v*` tags push semver tags (`:0.8.1`, `:0.8`, `:0`)
and `:latest` only for stable release tags. The `build-api`, `build-client`
and `build-worker` jobs in `ci.yml` own those tags.

## Where the version is consumed

- **API server**: `BIFROST_VERSION` env var → `/api/version` endpoint
  (`api/shared/version.py`).
- **Client bundle**: `VITE_BIFROST_VERSION` build arg, inlined into JS
  at build time (`client/src/lib/version.ts`).
- **CLI**: `BIFROST_VERSION` baked into the wheel at build time, exposed
  as `bifrost.__version__`.
- **CLI compatibility**: reported server and installed CLI contract versions
  must match. A different build version with a matching contract produces a
  deduplicated update notice, not a hard block. For migration operations use
  the authoritative SDK served by the selected instance.
- **Browser banner**: same — strict equality between `APP_VERSION` and
  `/api/version` poll response.

## Transition behavior (expected, not bugs)

After this change ships, in-flight users will see exactly one of:

1. **Browser tab open during cutover**: poll detects mismatch, banner
   appears, user reloads, gets new bundle. Normal path.
2. **CLI installed before cutover**: an incompatible contract blocks commands
   and requires an upgrade. A matching contract with different build bytes
   remains compatible and shows an update notice.
3. **Flux re-enable** (separate follow-up): old `0.8.0-N-g…` tags
   remain in GHCR; new `0.8.1-dev.N` tags outrank them by semver base
   comparison. Flux will only ever pick new-format tags.

## Debugging a wrong tag

**Symptom: dev version shows `1.0.0-dev.N` but you expected a higher base**

No stable `vMAJOR.MINOR.PATCH` tag exists on `Midtown-Technology-Group/bifrost` yet. Cut the
first release (for example `v1.0.0`) or verify `fetch-depth: 0` on checkout so
tag history is visible.

**Symptom: build fails with `compute-dev-version: latest tag 'X' is not vMAJOR.MINOR.PATCH`**

Someone tagged a non-semver release. Either delete the bad tag or
extend the regex in `scripts/compute-dev-version.sh`.

**Symptom: dev count seems wrong**

`git rev-list v<latest>..HEAD --count` is the source of truth. Run it
locally on the same SHA the CI built.

**Symptom: Flux picks an old image after re-enable**

Verify the `ImagePolicy` semver range is `>=0.0.0-0` (the trailing `-0`
is required to include pre-releases like `-dev.N`). Without it, Flux
silently excludes all dev builds.
