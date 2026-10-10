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
    sealed_runtime = json.loads((out / "sealed-execution.json").read_text())
    assert sealed_runtime["sealed_executable_sha256"] == digest(out / "workflow")
    assert sealed_runtime["sealed_executable_sha256"] == "160917deeb94275f31ca9ddee2dacae00fb079fdf54cb60e206c8def261f9c34"
    assert sealed_runtime["missing_keys_observation"] == ["alpha", "zeta"]
    assert len(sealed_runtime["samples"]) == 20 and sealed_runtime["same_artifact_runs"] == 22
    warm_runtime = json.loads((out / "warm-execution.json").read_text())
    assert runtime["missing_keys_observation"] == ["alpha", "zeta"]
    assert warm_runtime["missing_keys_observation"] == ["zeta", "alpha"]
    samples = runtime["samples"]
    metrics = {}
    for key in ("startup_to_sdk_ms", "execution_ms", "max_rss_kib"):
        values = sorted(s[key] for s in samples)
        metrics[key] = {"median": statistics.median(values), "p95": values[-2], "n": len(values)}
    tests = [json.loads(line) for line in (out / "tests.jsonl").read_text().splitlines() if line.startswith("{")]
    assert not any(t["Action"] == "fail" for t in tests), "test failure"
    artifact = out / "workflow"
    changed = out / "workflow-warm-edit"
    scanner, _ = json.JSONDecoder().raw_decode((out / "govulncheck.jsonl").read_text())
    assert digest(artifact) != digest(changed), "source edit did not change artifact"
    info = {
        "runtime": "go-native/v1", "profile": "local-authoring-only",
        "source_sha256": source_digest, "source_files": files,
        "artifact_sha256": digest(artifact), "artifact_size_bytes": artifact.stat().st_size,
        "local_probe_sha256": digest(out / "probe"),
        "native_adapter_sha256": digest(out / "adapter"),
        "common_protocol_probe_sha256": digest(out / "protocolprobe"),
        "native_adapter_status": "candidate executable; live Rust guardian integration and runtime acceptance pending",
        "warm_artifact_sha256": digest(changed),
        "behavior_edit_observation": {"original_missing_keys": runtime["missing_keys_observation"],
                                      "edited_missing_keys": warm_runtime["missing_keys_observation"]},
        "go_version": "go1.27.1", "goos": "linux", "goarch": "amd64", "cgo": False,
        "dependency_inputs_sha256": hashlib.sha256(modules.encode()).hexdigest(),
        "module_graph_sha256": digest(out / "module-graph.txt"),
        "module_graph_evidence": "module-graph.txt and cold.txt (go list -m -json all)",
        "warm_source_sha256": hashlib.sha256((out / "warm-source.json").read_text().strip().encode()).hexdigest(),
        "manifest_sha256": digest(root / "manifest.json"),
        "input_schema_sha256": digest(root / "schemas/input.json"),
        "output_schema_sha256": digest(root / "schemas/output.json"),
        "sdk_version": runtime["sdk_version"], "recipe_version": "go-native-spike/v1",
        "build_recipe_sha256": digest(root / "scripts/measure.sh"),
        "builder_image": image, "scanner_image": (out / "scanner-image.txt").read_text().strip(),
        "scanner_configuration": scanner["config"],
        "entrypoint": "workflow", "build_entrypoint": "./cmd/workflow",
        "build_flags": ["-mod=readonly", "-trimpath", "-buildvcs=false"],
        "reviewed_deployment": False,
        "source_commit": subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
        "builder_identity": {"kind": "hosted-CI" if os.environ.get("GITHUB_ACTIONS") == "true" else "supported-VM-required",
                             "run_id": os.environ.get("GITHUB_RUN_ID"), "kernel": platform.release(),
                             "cpu_count": os.cpu_count(), "container_cpu_limit": 2, "container_memory_gib": 3},
        "validation": {"go_test": "pass", "go_vet": "pass", "gofmt": "pass", "govulncheck": "pass",
                       "individual_tests_passed": sum(t["Action"] == "pass" and "Test" in t for t in tests)},
        "attestation": "experimental ephemeral Ed25519 descriptor signature; no production trust anchor or admission",
        "reproducibility": "two independent cold compiler caches and restored warm inputs produced equal bytes under this pinned recipe; no cross-environment claim",
        "rust_admission": False, "durable_projection": False,
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "timings_ms": {"module_download": timing(out / "module-download.txt", "module_download_ns"),
                       "cold_compile_warm_modules": timing(out / "cold.txt", "compile_ns"),
                       "independent_cold_compile": timing(out / "independent-cold.txt", "compile_ns"),
                       "warm_source_edit_compile": timing(out / "warm-edit.txt", "compile_ns"),
                       "restored_warm_compile": timing(out / "restored-warm.txt", "compile_ns"),
                       "edit_to_artifact": timing(out / "edit-loop.txt", "edit_to_artifact_ns")},
        "execution_summary": metrics, "first_execution_ms": samples[0]["execution_ms"], "cancellation_ms": runtime["cancellation_ms"],
        "sealed_native_fixture": {"evidence": "sealed-execution.json", "evidence_sha256": digest(out / "sealed-execution.json"),
                                  "artifact_sha256": sealed_runtime["sealed_executable_sha256"],
                                  "materialization_ms": sealed_runtime["sealed_materialization_ms"],
                                  "execution_median_ms": statistics.median(s["execution_ms"] for s in sealed_runtime["samples"]),
                                  "cancellation_ms": sealed_runtime["cancellation_ms"], "runtime_acceptance": False},
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
