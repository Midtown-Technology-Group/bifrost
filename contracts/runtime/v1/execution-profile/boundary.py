"""Conservative source-change tripwire, not a safety certification or AST proof."""
import hashlib
from pathlib import Path

# A new Python file anywhere in api is included; no path-specific allowlist can hide it.
SOURCE_ROOT = "api"


def fingerprint(root):
    digest = hashlib.sha256()
    count = 0
    for path in sorted((root / SOURCE_ROOT).rglob("*.py")):
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix().encode()
        data = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big") + name)
        digest.update(len(data).to_bytes(8, "big") + data)
        count += 1
    return {"files": count, "sha256": digest.hexdigest()}


def verify(root, baseline):
    if fingerprint(Path(root)) != baseline:
        raise ValueError("SourceReviewRequired: Python source changed; reconcile adapter custody and compatibility tests")
