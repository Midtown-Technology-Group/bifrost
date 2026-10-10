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
5. A changed server/CLI contract compared with the last stable tag imposes a
   major-version floor: existing CLIs reject mismatched contracts. PR labels
   cannot lower that floor. Missing, non-literal or inconsistent server/CLI
   contract versions fail release calculation.

`scripts/release/next-release-version.sh` prints `vX.Y.Z` (exit 3 means no
release; other failures remain failures). The interval uses complete paginated
PR metadata matched to the exact main first-parent Git range. Unattributed or
ambiguous commits require reconciliation; timestamp search and a 1,000-PR cap
are not release authorities. Unknown PR titles use the documented patch default
and remain visible in the retained inventory.

On main pushes, **Release PR** updates the single `automation/release` PR with
all three plugin versions, `release/candidate.json` and `release/notes.md`.
Thomas chose **automatic release PR, publish on merge** on October 2, 2026.
Required CI, signed commits, human maintainer approval of the exact PR head and
administrator merge-queue admission remain required. Reviewers can edit the
release notes before approving; automation lists security-related titles without
inventing verified CVEs or asserting that none were fixed.

After the release PR merges and its exact main CI succeeds, **Release tag**
validates its source interval, signed source, approval and manifests. It creates
an immutable source tag and explicitly dispatches tag CI; token-created tag
pushes do not start downstream workflows. Tag CI tests, builds signed images,
uploads attested source assets and publishes the immutable GitHub Release.
This path publishes packages; selecting and deploying production image digests
requires the separate protected `bifrost-infra` lane.

See the [implementation and proof plan](plans/2026-10-02-fork-release-automation.md).
These source changes become operational only after their reviewed PR is merged.

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
5. Review the automatic release PR's version, complete source/PR inventory,
   plugin manifests, security notes and upgrade instructions. Approve its exact
   head and enqueue through the normal administrator-authorized native queue.
6. A concurrent source merge outside its retained interval invalidates the
   candidate. Rerun **Release PR**, review its updated head and merge that
   candidate; do not hide extra source under a stale release plan.
7. Packaging keeps its comprehensive release checks, signatures and immutable
   asset publication. A published version cannot be rebuilt or replaced.
8. Release notes must include **Fixed vulnerabilities**, upgrade notes,
   contributors and an upstream baseline note when relevant.

## Recovery

- **Stale preparation or unpublished candidate:** rerun `release-draft.yml` on
  main, then review the refreshed PR. A merged but unpublished release pauses
  further release preparation so its original candidate cannot be overwritten.
- **Tag creation or dispatch failed:** inspect the tag's peeled SHA and exact CI
  runs first. Rerun `release-tag.yml` on main with `main_ci_run` set to the
  successful main-push CI run of the merged release PR. It verifies that evidence
  again; existing matching tags are retained, conflicting tags fail closed.
- **Packaging failed:** rerun the existing tag CI run after fixing its external
  blocker. No second publication run is dispatched when one is already recorded.
  If a source fix is necessary, review a new release version; never move a tag.
- **Published immutable release:** verify its source assets and image provenance;
  automation will not rebuild it. A duplicate queued CI run stops before image
  publication if that version is already published.
- The old release issue-closing trigger is retired. Existing issue #950 is
  historical context, not tagging authority. Manual pre-releases remain a
  separately reviewed tag-bound packaging path.

## Related files

- `scripts/compute-dev-version.sh` — candidate/main dev build identity
- `scripts/next-version.py` — label/title bump and contract floor
- `scripts/release/automatic-release.py` — interval, preparation and publication guards
- `scripts/release/next-release-version.sh` — read-only version calculation
- `.github/workflows/release-draft.yml` — prepares the reviewed release PR
- `.github/workflows/release-tag.yml` — validates exact main CI and dispatches tag CI
- `.github/workflows/ci.yml` — signed packaging and immutable release publication
- `.claude/skills/bifrost-release/SKILL.md` — fork operator entry point


## Stable package manifest and acceptance

Stable MTG tag CI builds each final-version artifact once. It passes the actual
API, client and worker build digests to `scripts/release/release_manifest.py`.
The generator reads contracts and the complete migration graph from the exact
tag commit, not from the pre-release source interval or mutable image tags.
Malformed identities, inconsistent contracts, incomplete migration graphs and
candidate/tag mismatches fail closed.

Before publication, `accept_release_images.py` pulls and executes read-only
content checks against those exact digests on `linux/amd64`: baked server/client
version, server and bundled CLI contracts, bundled CLI version and migration
heads. The manifest's `acceptance.kind = container-content-smoke` records this
bounded evidence. It does **not** certify live-service readiness, migration
execution, production deployment or arm64. The protected infrastructure lane
still owns preview, migration, drain, canary and runtime readback.

After acceptance, CI attests `release-manifest.json` using GitHub SLSA provenance
and exports the verification bundle as `release-manifest.json.sigstore.json`.
The immutable release must contain the manifest, its `.sha256` checksum and its
bundle before publishing. The same three files are available for 90 days in the
exact tag CI run's `release-manifest-vX.Y.Z-attempt-N` artifact. The immutable release
assets are the durable copy. Attempt-qualified names preserve failed-attempt
evidence while allowing only the failed publication job to be retried without
rebuilding successful image jobs. Consumers must verify the bundle for this
repository, CI workflow, exact tag ref and source commit, then compare the
manifest's run/attempt and all three image digests before promotion.

The format is defined in [the JSON schema](release-manifest.schema.json).
`database.upgrade_required` compares the migration graph with the prior stable
release, not a specific deployment's database. `rollback.supported = false`
means automatic rollback has not been certified. Terminal workflow retirement
history must survive application rollback; its schema downgrade refuses once
retirement evidence exists. Release notes regenerate the actual CLI transition
and this warning from source whenever the release PR refreshes. Reviewers still
assess security findings and release-specific instructions on the final head.

Manual CI on a branch also runs the unchanged `test.sh pre-pr` clean-candidate
gate with read-only repository permissions. This supplies the required Docker
lane when a local test VM is unavailable. It retains exact-candidate stage/plan
logs and cleanup evidence; tags never run that preparation job. Required CI,
human exact-head approval and administrator queue admission remain mandatory.
