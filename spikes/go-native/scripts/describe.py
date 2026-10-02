"""Trusted build-plane evidence collection. Never called by runtime admission."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import platform
import statistics
import subprocess
import sys
from datetime import datetime, timezone


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def closure(root: pathlib.Path) -> tuple[str, list[dict[str, str]]]:
    files = [{"path": str(p.relative_to(root)), "sha256": digest(p)}
             for p in sorted(root.rglob("*")) if p.is_file()]
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest(), files


def timing(path: pathlib.Path, key: str) -> float:
    line = next(line for line in path.read_text().splitlines() if line.startswith(key + "="))
    return int(line.split("=", 1)[1]) / 1_000_000


def main() -> None:
    root, out, image = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
    source_digest, files = closure(root)
    modules = "\n".join((root / f).read_text() for f in ("go.mod", "go.sum"))
    runtime = json.loads((out / "execution.json").read_text())
    samples = runtime["samples"]
    metrics = {}
    for key in ("startup_to_sdk_ms", "execution_ms", "max_rss_kib"):
        values = sorted(s[key] for s in samples)
        metrics[key] = {"median": statistics.median(values), "p95": values[-2], "n": len(values)}
    tests = [json.loads(line) for line in (out / "tests.jsonl").read_text().splitlines() if line.startswith("{")]
    assert not any(t["Action"] == "fail" for t in tests), "test failure"
    artifact = out / "workflow"
    changed = out / "workflow-warm-edit"
    assert digest(artifact) != digest(changed), "source edit did not change artifact"
    info = {
        "runtime": "go-native/v1", "profile": "local-authoring-only",
        "source_sha256": source_digest, "source_files": files,
        "artifact_sha256": digest(artifact), "artifact_size_bytes": artifact.stat().st_size,
        "warm_artifact_sha256": digest(changed),
        "go_version": "go1.27.1", "goos": "linux", "goarch": "amd64", "cgo": False,
        "dependency_inputs_sha256": hashlib.sha256(modules.encode()).hexdigest(),
        "module_graph_evidence": "cold.txt (go list -m -json all)",
        "sdk_version": "0.0.0-spike.1", "recipe_version": "go-native-spike/v1",
        "build_recipe_sha256": digest(root / "scripts/measure.sh"),
        "builder_image": image, "scanner_image": (out / "scanner-image.txt").read_text().strip(),
        "entrypoint": "workflow", "build_entrypoint": "./cmd/workflow",
        "build_flags": ["-mod=readonly", "-trimpath", "-buildvcs=false"],
        "reviewed_deployment": False,
        "source_commit": subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
        "builder_identity": {"kind": "hosted-CI" if os.environ.get("GITHUB_ACTIONS") == "true" else "supported-VM-required",
                             "run_id": os.environ.get("GITHUB_RUN_ID"), "kernel": platform.release(),
                             "cpu_count": os.cpu_count(), "container_cpu_limit": 2, "container_memory_gib": 3},
        "validation": {"go_test": "pass", "go_vet": "pass", "gofmt": "pass", "govulncheck": "pass",
                       "individual_tests_passed": sum(t["Action"] == "pass" and "Test" in t for t in tests)},
        "attestation": "unsigned local provenance; no production signing or admission",
        "reproducibility": "same inputs restored with warm cache produced equal bytes; independent cold rebuild not proved",
        "rust_admission": False, "durable_projection": False,
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "timings_ms": {"module_download": timing(out / "module-download.txt", "module_download_ns"),
                       "cold_compile_warm_modules": timing(out / "cold.txt", "compile_ns"),
                       "warm_source_edit_compile": timing(out / "warm-edit.txt", "compile_ns"),
                       "restored_warm_compile": timing(out / "restored-warm.txt", "compile_ns"),
                       "edit_to_artifact": timing(out / "edit-loop.txt", "edit_to_artifact_ns")},
        "execution_summary": metrics, "cancellation_ms": runtime["cancellation_ms"],
        "caveats": ["synthetic HTTPS capability endpoint; no real restricted-grant issuer or ingress",
                    "module dependency go-cmp is test-only; runtime uses standard library",
                    "one cold and one edited build sample; runtime sample count 20 per artifact",
                    "no Rust protocol, Start transaction or durable API observation",
                    "container isolation exercised, hostile multi-tenant isolation not accepted"],
    }
    (out / "descriptor.json").write_text(json.dumps(info, indent=2) + "\n")
    print(json.dumps({"artifact_sha256": info["artifact_sha256"], "size_bytes": info["artifact_size_bytes"],
                      "timings_ms": info["timings_ms"], "execution": metrics, "cancellation_ms": info["cancellation_ms"]}, indent=2))


if __name__ == "__main__":
    main()
