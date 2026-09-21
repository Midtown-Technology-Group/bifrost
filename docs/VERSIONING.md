# MTG Bifrost Versioning

`Midtown-Technology-Group/bifrost` uses an **independent semver line** owned by Midtown Technology
Group. Version numbers describe **MTG fork behavior**, not upstream release numbers.

## Semver policy

| Component | Meaning on this fork |
|-----------|----------------------|
| **MAJOR** | Breaking platform or operator-facing behavior changes |
| **MINOR** | Backward-compatible features or substantial improvements |
| **PATCH** | Backward-compatible fixes, security patches, small changes |

Pre-release dev builds use `<next-patch>-dev.<commits-since-tag>` (see
[dev-version-format runbook](runbooks/dev-version-format.md)).

## Deciding the next version

The next release version is computed automatically from the pull requests
merged since the last stable tag:

1. A `semver:major` / `semver:minor` / `semver:patch` label on the PR wins.
2. Otherwise the PR title's conventional-commit type decides — `feat` is a
   minor, `fix`/`perf`/`refactor`/`chore`/… are patches, and `!` or `BREAKING`
   is a major.
3. A PR with neither falls back to a patch.
4. The highest bump across all merged PRs wins.

`scripts/release/next-release-version.sh` prints `vX.Y.Z` (or exits non-zero
when nothing has merged since the last tag); the pure decision lives in
`scripts/next-version.py`. On every push to `main`, the **Release draft**
workflow opens or updates one `Release vX.Y.Z` issue with the computed version
and a tagging checklist, so no one has to ask for a release. Tagging remains a
human gate.

## First release baseline

- **First MTG semver tag:** `v1.0.0`
- **Upstream relationship:** documented here and in GitHub Release notes — **not**
  encoded in the version number.
- **Upstream lineage at `v1.0.0`:** includes platform work through upstream
  `v0.9.1` plus MTG fork-specific changes (badges, security policy, CI/registry
  ownership, workspace integration, and related operator tooling).

Future releases increment MTG semver only. When useful, release notes may mention
which upstream tag or commit range the MTG release incorporated.

## Tags and releases

1. Cut semver tags on `Midtown-Technology-Group/bifrost` only (`vMAJOR.MINOR.PATCH`).
2. Do **not** mirror upstream tags into this repo for dev-version computation.
3. Fork-local prerelease tags such as `v0.9.1-mtg.1` are ignored by
   `scripts/compute-dev-version.sh`.
4. Before the first tag, dev versions bootstrap as `1.0.0-dev.N` from commit
   count on `main`.
5. Run `./scripts/release-check.sh vX.Y.Z` before tagging — or run it with no
   argument to use the computed next version.
6. Push the tag; CI builds signed images and opens a GitHub Release draft.
7. Edit the release notes to include a **Fixed vulnerabilities** section (OpenSSF
   Passing requirement) and an upstream baseline note when relevant.

## Related files

- `scripts/compute-dev-version.sh` — dev version on every `main` build
- `scripts/next-version.py` — label / conventional-commit bump decision
- `scripts/release/next-release-version.sh` — next version from merged PRs
- `scripts/release-check.sh` — pre-tag checks
- `.github/workflows/release-draft.yml` — opens the `Release vNEXT` issue
- `.claude/skills/bifrost-release/SKILL.md` — operator release flow
- `.claude-plugin/plugin.json` — plugin manifest version (updated at tag time)
