"""App publication evidence for accounting; no Solution identity or effects.

The pure pin validator follows actual storage readback. It does not prove a
browser business journey, settle a ledger or authorize publication by itself.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from uuid import UUID

from bifrost.workspace_release import canonical_digest

from src.core.solution_delivery_policy import delivery_path

APP_RUNTIME_PIN_SCHEMA = "bifrost.inline-app-runtime-pin/v1"
SOURCE_SCHEMA = "bifrost.inline-app-git-source/v1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("App manifest contains ambiguous JSON keys")
        result[key] = value
    return result


def _hash_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or not 1 <= len(value) <= 1024:
        raise ValueError("App evidence requires a complete bounded hash map")
    for path, digest in value.items():
        if not isinstance(path, str) or not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise ValueError("App evidence path/hash is invalid")
        delivery_path(path)
    return value


def app_publication_runtime_pin(
    *, application_id: UUID, publication_job_id: UUID, organization_id: UUID | None,
    intent: dict[str, Any], manifest_bytes: bytes, protected_git: dict[str, Any],
) -> dict[str, Any]:
    """Bind original admission to captured Source and the exact compiled runtime.

    Caller separately verifies every output byte and current installed controls.
    CI/producer evidence refers to original admission, never a newer request.
    """
    artifact_hashes = _hash_map(intent.get("artifact_hashes"))
    if (intent.get("application_id") != str(application_id)
            or artifact_hashes.get("manifest.json") != "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
            or not isinstance(intent.get("controls_hash"), str)
            or not _DIGEST.fullmatch(intent["controls_hash"])
            or intent["controls_hash"] != protected_git.get("expected_controls_hash")):
        raise ValueError("App runtime manifest or installed controls differ from original intent")
    manifest = json.loads(manifest_bytes, object_pairs_hook=_unique_json)
    if not isinstance(manifest, dict):
        raise ValueError("App runtime manifest is invalid")
    source = manifest.get("git_source_evidence")
    if (not isinstance(source, dict) or source.get("schema_version") != SOURCE_SCHEMA
            or source.get("application_id") != str(application_id)
            or source.get("organization_id") != (str(organization_id) if organization_id else None)
            or source.get("metadata_mode") != "preserve_installed_controls"):
        raise ValueError("App source identity/scope or control-preservation evidence differs")
    admission_keys = {"artifact_digest", "ci_run_id", "ci_run_attempt", "producer_run_id", "producer_run_attempt"}
    if any(source.get(key) != protected_git.get(key) for key in admission_keys | {"source_commit_sha"}):
        raise ValueError("App runtime source differs from original protected admission")
    authored = {key: value for key, value in source.items() if key not in admission_keys}
    if canonical_digest(authored) != protected_git.get("artifact_digest"):
        raise ValueError("App complete source evidence differs from the bound producer artifact")
    authored_hashes = _hash_map(source.get("source_hashes"))
    modes = source.get("file_modes")
    if ("app.yaml" not in authored_hashes or not isinstance(modes, dict)
            or set(modes) != set(authored_hashes) or any(mode not in {"100644", "100755"} for mode in modes.values())):
        raise ValueError("App complete authored source inventory is invalid")
    capture = manifest.get("source_snapshot_evidence")
    build = manifest.get("build_evidence")
    if (not isinstance(capture, dict) or capture.get("schema_version") != "bifrost.inline-app-source-snapshot/v1"
            or capture.get("authored_source_hashes") != authored_hashes
            or capture.get("metadata_not_applied") != ["app.yaml"]
            or not isinstance(build, dict) or build.get("schema_version") != "bifrost.inline-app-build/v1"):
        raise ValueError("App capture/build evidence is absent or differs from protected Source")
    compiler = _hash_map(capture.get("compiler_source_hashes"))
    migrated = capture.get("migration_changed_paths")
    if (set(compiler) != set(authored_hashes) - {"app.yaml"}
            or build.get("materialized_source_hashes") != compiler
            or not isinstance(migrated, list) or any(not isinstance(path, str) for path in migrated)
            or len(migrated) != len(set(migrated)) or not set(migrated).issubset(compiler)
            or any(compiler[path] != digest for path, digest in authored_hashes.items()
                if path != "app.yaml" and path not in migrated)):
        raise ValueError("App compiler inputs differ outside the recorded migration")
    output_hashes = {path: digest for path, digest in artifact_hashes.items() if path != "manifest.json"}
    outputs = manifest.get("outputs")
    if (build.get("output_hashes") != output_hashes or not isinstance(outputs, list)
            or any(not isinstance(path, str) for path in outputs)
            or len(outputs) != len(set(outputs)) or set(outputs) != set(output_hashes)
            or manifest.get("entry") not in output_hashes
            or manifest.get("css") is not None and manifest["css"] not in output_hashes):
        raise ValueError("App compiled runtime inventory differs from publication intent")
    evidence = {"schema_version": APP_RUNTIME_PIN_SCHEMA,
        "application_id": str(application_id), "publication_job_id": str(publication_job_id),
        "organization_id": str(organization_id) if organization_id else None,
        "manifest_hash": artifact_hashes["manifest.json"], "output_hashes": output_hashes,
        "controls_hash": intent["controls_hash"], "source": source,
        "compiler_source_hashes": compiler, "migration_changed_paths": migrated,
        "dependencies": manifest.get("dependencies")}
    evidence["runtime_pin_hash"] = canonical_digest(evidence)
    return evidence


async def read_app_publication_runtime_pin(storage: Any, **identity: Any) -> dict[str, Any]:
    """Read the actual live manifest after the caller's full output verification."""
    raw = await storage.read_file(str(identity["application_id"]), "live", "manifest.json")
    return app_publication_runtime_pin(manifest_bytes=raw, **identity)
