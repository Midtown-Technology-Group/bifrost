"""Read-only static evidence reconciliation; never an admission/runtime tool.

Run with an exact Git contract commit and the downloaded spike evidence directory.
Reports partial field coverage, not a valid/accepted native artifact document.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract_commit")
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.contract_commit):
        parser.error("contract_commit must be a full immutable Git SHA")
    repo = Path(__file__).resolve().parents[3]
    schema_path = "contracts/runtime/v1/language-neutral/artifact.schema.json"
    raw = subprocess.check_output(
        ["git", "-C", str(repo), "show", f"{args.contract_commit}:{schema_path}"]
    )
    schema = json.loads(raw)
    native = next(v for v in schema["oneOf"]
                  if v["properties"]["kind"]["const"] == "native-executable/v1")
    descriptor_bytes = (args.evidence / "descriptor.json").read_bytes()
    descriptor = json.loads(descriptor_bytes)
    for filename, key in (("workflow", "artifact_sha256"),
                          ("module-graph.txt", "module_graph_sha256")):
        if sha((args.evidence / filename).read_bytes()) != descriptor[key]:
            raise ValueError(f"retained {filename} digest differs from descriptor")
    if (args.evidence / "workflow").stat().st_size != descriptor["artifact_size_bytes"]:
        raise ValueError("retained workflow size differs from descriptor")
    mapped = {
        "kind": "native-executable/v1",
        "executable_sha256": descriptor["artifact_sha256"],
        "build_evidence_sha256": sha(descriptor_bytes),
        "platform": {"os": descriptor["goos"], "architecture": descriptor["goarch"]},
        "sdk": {"distribution": "bifrost-go", "version": descriptor["sdk_version"]},
        "toolchain": {"implementation": "go", "version": descriptor["go_version"]},
        "dependencies": {"kind": "go-module-graph",
                         "digest": "sha256:" + descriptor["module_graph_sha256"]},
        "runtime_protocol": "bifrost.runtime/v1",
    }
    missing = {
        "artifact_id": "No accepted deployment bundle; executable digest is not bundle identity.",
        "adapter_sha256": "No trusted staged adapter binary; the local probe is not that adapter.",
        "image_digest": "Builder/scanner images are not an accepted runtime image; null requires an explicit native launch choice.",
    }
    if set(native["required"]) != set(mapped) | set(missing):
        raise ValueError("shared native field roster changed; review mapping before reuse")
    if set(native["properties"]) != set(native["required"]) or native["additionalProperties"] is not False:
        raise ValueError("shared native document closure changed")
    print(json.dumps({
        "status": "partial-evidence-not-native-registration",
        "contract_commit": args.contract_commit,
        "schema_path": schema_path,
        "schema_sha256": sha(raw),
        "producer_source": descriptor["source_commit"],
        "producer_run": descriptor["builder_identity"]["run_id"],
        "descriptor_sha256": sha(descriptor_bytes),
        "verified_retained_files": ["workflow", "module-graph.txt"],
        "mapped_document_fields": mapped,
        "missing_document_fields": missing,
        "limitations": [
            "Field reconciliation is not general JSON Schema validation or a negotiated wire codec.",
            "Toolchain/SDK claims retain their producer evidence; these bytes are not re-executed.",
            "Module graph evidence does not close all bundle/build/adapter/image inputs.",
            "runtime_protocol names a proposed document family, not an implemented workload profile.",
            "No accepted deployment, real runtime grant, Rust Start or durable Result is established.",
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
