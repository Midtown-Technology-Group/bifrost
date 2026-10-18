---
type: operations-guide
title: Versioning, Packaging, and Release Process
description: Explains MTG fork version calculation, reviewed release preparation, immutable package publication, and the separate deployment authority.
tags: [release, versioning, packaging, ci, provenance]
sources:
  - id: openwiki-source-7cacdfbab734dce3208e66d2
    resource: repo://.github/workflows/release-draft.yml
  - id: openwiki-source-9956d5e26bb8ccda5a071263
    resource: repo://.github/workflows/release-tag.yml
  - id: openwiki-source-3ad7b1ca36d715d0f89f4ad1
    resource: repo://docs/VERSIONING.md
  - id: openwiki-source-8a473cb55a1f37afa08f1a7d
    resource: repo://scripts/next-version.py
  - id: openwiki-source-ba8351f4b5251e582339aa49
    resource: repo://scripts/release/automatic-release.py
generated: { by: "bifrost-atlas", at: "2026-10-18T02:34:56.337Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T02:51:00.673Z
---

# Versioning, Packaging, and Release Process

The MTG fork has an independent semantic-version line. Release automation calculates and packages candidate versions, while production rollout belongs to a separately protected infrastructure lane. A published release is immutable; packaging acceptance does not establish deployment readiness. See [deployment topology](deployment-and-infrastructure.md), [development and verification](development-and-verification.md), and [Git and ecosystem integration](../integrations/git-and-ecosystem.md).

## Version calculation and review

Version numbers describe MTG fork behavior, not upstream releases. Major/minor/patch labels take precedence, otherwise conventional commit titles determine the bump; the highest bump across the interval wins. A changed server/CLI contract imposes a major floor because existing CLIs reject mismatches. Version calculation requires a complete PR inventory matched to the exact first-parent main range; ambiguous attribution must be reconciled rather than guessed (`docs/VERSIONING.md`).

On main, the Release PR workflow prepares or updates the single `automation/release` candidate with plugin versions, candidate interval, and release notes. Automation prepares a signed PR but does not approve, enqueue, or merge it. A human maintainer approves the exact head and an administrator uses the native merge queue. If main advances outside the retained interval, refresh and review the candidate again before merge (`.github/workflows/release-draft.yml`, `docs/VERSIONING.md`).

## Candidate integrity and publication checks

`automatic-release.py` selects the latest stable `vX.Y.Z` tag on the candidate's first-parent history and builds the release interval from every first-parent main commit. Each interval commit must map to a merged main PR; missing or duplicate attribution fails closed. The highest PR bump wins, with a server/CLI contract change imposing its major floor ([stable-tag baseline and interval inventory](../../scripts/release/automatic-release.py#L117-L171), [candidate calculation](../../scripts/release/automatic-release.py#L174-L199), [contract floor](../../scripts/next-version.py#L92-L130)).

Preparation requires a clean checkout of the exact source and pauses while an earlier merged release candidate still lacks its matching tag or immutable package. Before publication, validation recomputes the candidate from the retained source commit and PR inventory, limits intervening changes to release metadata/manifests, checks that each plugin manifest changed only its version, and requires the release-note sections ([prepare and pending-candidate guard](../../scripts/release/automatic-release.py#L249-L326), [candidate validation](../../scripts/release/automatic-release.py#L207-L245)).

The publish gate reads back the exact completed successful `main` push CI run and verifies that it belongs to this repository and workflow. It then checks that the source is the merged `automation/release` PR, all PR commits and the merge commit are signature-verified, and a human maintainer with write-level permission approved that exact PR head ([trusted CI and signed PR checks](../../scripts/release/automatic-release.py#L376-L418), [exact-head human approval](../../scripts/release/automatic-release.py#L419-L440)).

Before dispatching tag CI, publication refuses to move a conflicting tag and reads the created or existing tag back to confirm its peeled commit matches the approved source. It does not stack duplicate tag publishers; an existing tag CI run is inspected and a failed run is recovered by rerunning that same run. An already-published release must be immutable and contain the required signed source assets or the process stops ([tag identity and one-time dispatch](../../scripts/release/automatic-release.py#L443-L518)).

## Tag, package, and provenance flow

After the release PR is merged and exact main CI succeeds, the Release tag workflow validates the approved candidate and source interval, creates an immutable tag, then explicitly dispatches CI on that tag. The explicit dispatch matters because token-created tag pushes do not trigger downstream workflows. Tag CI tests and builds signed packages and publishes an immutable GitHub Release (`.github/workflows/release-tag.yml`, `docs/VERSIONING.md`).

Stable package generation uses final API/client/worker image digests and reads contracts and migrations from the exact tag commit. Content acceptance checks inspect those exact Linux/amd64 image digests for baked versions, server/client contracts, bundled CLI version, and migration heads. This is bounded content evidence; it does not execute migrations or prove service health, production rollout, or arm64 behavior. CI attests the release manifest and publishes the manifest, checksum, and verification bundle; consumers verify repository/workflow/ref/source and all component digests before promotion (`docs/VERSIONING.md`).

## Deployment boundary and recovery

Publishing packages does not deploy production. The protected `bifrost-infra` lane owns preview, migration, drain, canary, and runtime readback. The source workflow guard and repository versioning guidance describe intended policy, not evidence that a release has happened or that images are deployed (`docs/VERSIONING.md`, `.github/workflows/release-draft.yml`).

For stale preparation, rerun release preparation and review the refreshed exact head. For tag/dispatch failures, inspect the existing tag's peeled SHA and CI evidence before retrying. For packaging failures, retry only the existing failed job where possible; if source must change, prepare a new release version. Never move a tag or replace an immutable published release. Recovery cases and bounded acceptance semantics are documented in the repository's versioning guide (`docs/VERSIONING.md`).
