# Protected-main producer contract (proposal)

This is an additive producer contract for owner-approved ordinary production
selection. It does not activate an infrastructure consumer or authorize deployment.
Formal stable releases retain `release-manifest.json` schema v1, their real baked
stable versions, and their current publication path. No existing v1 consumer is
made to interpret a dev binary as a stable release.

The existing `Build Dev Images` job in `.github/workflows/ci.yml` promotes and
signs exact candidate digests without rebuilding. After all three component
promotions and their existing v1 attestations succeed, it assembles
`production-manifest.json`, described by
[production-manifest.schema.json](../production-manifest.schema.json).

The v2 discriminator is `schema_version: 2`, `channel: protected-main`, and
`tag: null`. `version` is the exact baked `X.Y.Z-dev.N` identity validated in the
candidate labels; it is never replaced with a stable version. Source includes
repository, protected-main commit, exact tree, first parent and candidate commit.
All three components must have the same candidate commit/tree/version. The
complete API/client/worker tuple contains immutable repository/index digests and
actual Linux architecture child digests (API/client AMD64; worker AMD64+ARM64).
`build` binds one main-push workflow run and attempt. Output run/attempt fields
come directly from each promotion step in the same job; partial reruns cannot
supply an older component output. Existing image-promotion/v1 predicates are
unchanged.

Compatibility reuses release-manifest v1's CLI contract, migration heads,
upgrade-required and unsupported automatic rollback warning. Its comparison
baseline is explicitly `source.parent`, **not the currently deployed version**.
A deployment consumer must compare the selected receipt against its actual
installed receipt/schema and retained retirement evidence. A revert has a new
source commit/run identity even if it restores a previous tree. Equal trees or
baked versions do not authorize substituting receipts between merges.

## Signature and retrieval

The producer writes one complete JSON file only after validation of every
component, writes its SHA256 checksum, and attests the bytes with the existing
GitHub build-provenance action. The download artifact is
`production-manifest-<full-source-sha>-attempt-<N>` in that exact CI run. It
contains the JSON, `.sha256`, and `.sigstore.json` verification bundle; artifact
retention is 90 days.

For durable registry evidence, the same JSON predicate is cosign-attested on
**each exact component index digest** with predicate type
`https://bifrost.midtowntg.com/attestations/production-manifest/v2`. The existing
GHCR packages, credential and OIDC permissions are reused. Registry attestations
persist with the digest independently of downloadable artifact retention; normal
package deletion/retention policy still applies. The consumer must retain its
selected manifest/bundle for rollback audit and not depend on a mutable tag.

## Consumer admission and activation decisions

The infra consumer owner, coordinated by Eddie, must explicitly support v2
before ordinary production activation. Existing v1 consumers remain unchanged.
The consumer must:

1. Verify the exact repository and signer
   `https://github.com/Midtown-Technology-Group/bifrost/.github/workflows/ci.yml@refs/heads/main`,
   issuer `https://token.actions.githubusercontent.com`, subject digest and
   source commit in the SLSA envelope. An allowed PR signer is not a main signer.
2. Qualify that exact main-push CI **run and attempt** as completed successfully,
   including its applicable Sonar evidence. A signed receipt from a partial,
   in-progress, failed or superseded attempt is insufficient. All required
   code-PR/queue checks and normal deployment approvals remain in force.
3. Validate the strict v2 schema and complete tuple, registry predicate signature
   and subject, candidate/promotion provenance, source/tree/run/attempt and actual
   architecture inventory. Never combine components across receipts or resolve
   mutable `dev`/version tags for selection. Rapid merges do not mean “latest run
   wins”; retain the exact chosen source and receipt digest through approval.
4. Compare actual deployed schema/contract and rollback state with the selected
   receipt. Preserve backups, migration controls, terminal-retirement evidence
   and the existing refusal of unsafe automatic downgrade.

Producer activation requires a reviewed merge of this draft, followed by a real
protected-main promotion that emits and verifies the tuple and all signatures.
This task performs neither merge nor production deployment. Consumer activation,
its ordinary-production selection policy and protected rollout remain with the
infra owner. Whether to disable automatic formal release-PR preparation is a
separate owner decision; this producer does not change release automation or
human approvals. A later formal SemVer milestone uses its existing stable build
path; promoting dev bytes does not make their baked version stable.
