#!/usr/bin/env python3
"""Append tag-specific artifact instructions to retained release notes."""

import os
import re
import sys

version = sys.argv[1]
if not re.fullmatch(r"v\d+\.\d+\.\d+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?", version):
    raise SystemExit("invalid release tag")
repository = os.environ["GITHUB_REPOSITORY"]
if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
    raise SystemExit("invalid repository")
registry = repository.lower()
print("\n## Docker images\n")
for component in ("api", "client", "worker"):
    print(f"- `{component}`: `ghcr.io/{registry}-{component}:{version[1:]}`")
print(f"""
## Type stubs and signed artifacts

Download `bifrost.pyi` when available; see `DISTRIBUTION.md` for IDE setup.
The source archive and available stubs have matching `.sigstore` bundles.
Verify the source archive with cosign >= v2.4:

```bash
cosign verify-blob \\
  --bundle bifrost-{version}-source.tar.gz.sigstore \\
  --certificate-identity-regexp 'https://github\\.com/{repository}/.*' \\
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \\
  bifrost-{version}-source.tar.gz

gh attestation verify bifrost-{version}-source.tar.gz --owner {repository.split("/")[0]}
```

Images are signed and carry build provenance. MTG production deployment uses
reviewed digest pins and its separately authorized protected infrastructure lane.
""")


if repository == "Midtown-Technology-Group/bifrost" and "-" not in version:
    print(f"""
## Stable package identity

`release-manifest.json` records the exact tag source, all three immutable image
digests, CLI contracts, required database heads and rollback limits. Its
`release-manifest.json.sigstore.json` bundle attests that identity. Verify it
before selecting images; a checksum alone is not provenance:

```bash
sha256sum --check release-manifest.json.sha256
gh attestation verify release-manifest.json \\
  --bundle release-manifest.json.sigstore.json \\
  --repo {repository} \\
  --signer-workflow {repository}/.github/workflows/ci.yml \\
  --source-ref refs/tags/{version} \\
  --source-digest <verified-tag-commit>
```

Packaging acceptance executes read-only content checks on these exact
`linux/amd64` digests. It does not claim database migration, live-service,
production readiness or arm64 proof. Promote these accepted digests unchanged;
never relabel a dev/RC image as a stable release.
""")
