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
