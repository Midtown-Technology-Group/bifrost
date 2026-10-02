# Automatic fork releases

Status: implementation in the reviewed automation branch; no stable release has
been tagged or published by this work. Production deployment is separately authorized.

## Evidence and intended outcome

The fork owns independent SemVer: major for breaking operator/platform behavior,
minor for compatible features, patch for fixes. `docs/VERSIONING.md` gives
`semver:*` labels precedence over conventional PR titles, then defaults to patch.
Current stable release is `v2.2.0` (September 4); release issue #950 proposes
`v2.3.0`. Since that tag the CLI contract advanced from 11 to 13, including
removal of the bulk-upsert route and an enum older CLIs cannot parse. Existing
CLIs hard-block a contract mismatch; this is a major-version floor under the
fork's stated policy, even when labels would otherwise select a minor. Release preparation still requires a manual plugin bump and issue
closure. The release-tag workflow creates a ref with `GITHUB_TOKEN` and assumes
that starts tag CI, although token-created push events do not start another run.

HaloCLI main `dd7d206a29b78111495bdedb19f0ab63d82fc890` uses an explicit package
version, a human-readable changelog and tag-bound packaging. It does not choose
or create versions automatically. Its release tests package metadata and emits
an SBOM; its separate MSI build checks binary version equality and documents
explicit dispatch when a token-created release cannot trigger a downstream run.
Borrow those contracts rather than relying on event recursion.

The proposed Bifrost path automatically prepares one release PR, then tags and
publishes the checked release commit after that PR is reviewed and merged.
Production image selection and deployment remain in the protected infrastructure
lane. This plan does not authorize publishing the current accumulated release.

## Implementation stages

1. Reconcile version documentation with the actual candidate/main image path.
   `compute-dev-version.sh` normally uses a commit timestamp, but production
   candidate/main publication uses `--next-after` and a commits-since-tag count.
   Document this distinction accurately and preserve candidate/main equality.
   Enforce a major-version floor for changed CLI contracts. Fix the next-release
   wrapper's error/no-release propagation and fail closed
   when release inputs cannot be read.
2. Replace release issue preparation with an automatically updated release PR:
   compute the bump from the complete commit/PR interval, update all three plugin
   manifests and retained release metadata/notes together, preserve signed
   history, and allow normal required CI and human queue authorization. Do not
   create new credentials or bypass required reviews. Account for approval of
   workflows on PRs created with `GITHUB_TOKEN`, or use a separately configured
   repository-scoped GitHub App when explicitly installed.
3. After exact checked release-PR merge, validate version/manifests/source interval
   and tag using exact commit identity. Explicitly dispatch tag packaging rather
   than expecting a token-created push to trigger CI. Preserve signed images,
   artifact attestations and immutable release publication. Replace the static
   release-candidate notes with the retained notes for this release.

## Required proofs

- Empty range, discovery failure, partial pagination and unknown PR classification
  cannot silently produce a release. Bump label/title precedence is deterministic.
- All versioned plugin manifests and baked API/client/CLI release versions match
  the tag. Tags from upstream never set the fork's release baseline.
- Concurrent merges either appear in the reviewed release interval or invalidate
  that candidate; no unreviewed code is hidden behind a stale release plan.
- A failed build is recoverable for the same tag and source. An existing tag at a
  different commit is a hard failure. Replays never move tags or replace a
  published immutable release.
- Package artifact checks, hashes/provenance and notes are present before publish.
  Security notes identify verified fixes; automation does not invent CVEs or a
  claim that no vulnerabilities were fixed.
- Release publication cannot close maintenance, choose production images or
  dispatch a workflow execution. Actual deployments keep drain, CAS and runtime
  readback checks.

Thomas selected automatic release PR preparation and publication on merge. The
combined automation change still requires human review and current-head CI.
The release PR itself must receive an exact-head human maintainer approval and
normal administrator queue admission; choosing this cadence does not publish
the current accumulated release. Recovery is documented in `docs/VERSIONING.md`.
