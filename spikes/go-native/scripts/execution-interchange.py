"""Hosted test-only Go/Python-oracle exchange. No workload/session/owner execution."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys


class PeerMismatch(ValueError):
    pass


def canonical(value):
    # Preserve bool/integer/float differences; Python == would conflate them.
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":"))


def main():
    reference, binary, output = map(lambda arg: Path(arg).resolve(), sys.argv[1:4])
    scripts = Path(__file__).resolve().parent
    provenance = json.loads((scripts / "profile-peer-provenance.json").read_text())
    head = subprocess.check_output(["git", "-C", str(reference), "rev-parse", "HEAD"], text=True).strip()
    if head != provenance["proposal_head"]:
        raise ValueError("unreviewed reference head")
    for name, expected in provenance["reference_files"].items():
        if hashlib.sha256((reference / name).read_bytes()).hexdigest() != expected:
            raise ValueError("changed reference source")
    if hashlib.sha256((scripts / "profile-peer-requirements.lock").read_bytes()).hexdigest() != provenance["oracle_dependencies_sha256"]:
        raise ValueError("changed reference dependency closure")

    root = reference / "contracts/runtime/v1/execution-profile"
    sys.path.insert(0, str(root))
    # Exact existing proposal oracle. Its Session and injected authority events
    # are never called here; validator() loads only local reviewed schemas.
    from check import validator
    from oracle import decode, wire
    validate = validator()
    vectors = json.loads((root / "wire-vectors.json").read_text())
    golden = {}
    for vector in vectors:
        if vector["error"] is None and vector.get("hex"):
            frame = decode(bytes.fromhex(vector["hex"]), validate)
            if frame is None or vector["name"] in golden:
                raise ValueError("invalid golden inventory")
            golden[vector["name"]] = frame
    if len(golden) != 18:
        raise ValueError("changed golden inventory")

    profile = "bifrost.runtime/v1/execution_profile/v1"

    def validate_exchange(value):
        if set(value) != {"profile", "frames"} or value["profile"] != profile:
            raise PeerMismatch("wrong exchange envelope")
        if len(value["frames"]) != len(golden):
            raise PeerMismatch("wrong exchange count")
        seen = set()
        for entry in value["frames"]:
            if set(entry) != {"name", "frame_hex"} or entry["name"] not in golden or entry["name"] in seen:
                raise PeerMismatch("wrong vector identity")
            frame = decode(bytes.fromhex(entry["frame_hex"]), validate)
            if canonical(frame) != canonical(golden[entry["name"]]):
                raise PeerMismatch("decoded peer value differs")
            seen.add(entry["name"])

    # No ambient SDK/CI credentials are inherited by the Go diagnostic process.
    environment = {"PATH": "/nonexistent", "LANG": "C.UTF-8"}
    corpus = root / "wire-vectors.json"
    result = subprocess.run([str(binary), str(corpus), "emit"], env=environment,
                            capture_output=True, check=True, timeout=30)
    go_exchange = json.loads(result.stdout)
    validate_exchange(go_exchange)
    (output / "go-execution-exchange.json").write_bytes(result.stdout)
    python_exchange = {"profile": profile, "frames": [
        {"name": name, "frame_hex": wire(golden[name]).hex()}
        for name in sorted(golden)
    ]}
    python_path = output / "python-execution-exchange.json"
    python_path.write_text(json.dumps(python_exchange, indent=2) + "\n")
    subprocess.run([str(binary), str(corpus), "validate", str(python_path)],
                   env=environment, capture_output=True, check=True, timeout=30)

    # A shape-valid wrong result must fail semantic comparison in each peer.
    def altered(exchange):
        candidate = json.loads(json.dumps(exchange))
        entry = next(item for item in candidate["frames"] if item["name"] == "valid-Result")
        frame = decode(bytes.fromhex(entry["frame_hex"]), validate)
        frame["body"]["value"] = {"deliberate_peer_drift": True}
        entry["frame_hex"] = wire(frame).hex()
        decode(bytes.fromhex(entry["frame_hex"]), validate)  # still shape-valid
        return candidate

    try:
        validate_exchange(altered(go_exchange))
    except PeerMismatch:
        pass
    else:
        raise ValueError("Python accepted deliberate semantic drift")
    bad_path = output / "deliberate-drift-exchange.json"
    bad_path.write_text(json.dumps(altered(python_exchange)) + "\n")
    rejected = subprocess.run([str(binary), str(corpus), "validate", str(bad_path)],
                              env=environment, capture_output=True, timeout=30)
    if rejected.returncode != 1 or rejected.stderr != b"proposed profile exchange failed\n":
        raise ValueError("Go did not reject deliberate semantic drift")
    bad_path.unlink()
    receipt = {
        "status": "proposed-wire-peer-proof-not-runtime-authority",
        "reference_head": head,
        "go_encoder_to_python_oracle_decoder": len(golden),
        "python_oracle_encoder_to_go_decoder": len(golden),
        "message_types": sorted({frame["type"] for frame in golden.values()}),
        "deliberate_shape_valid_drift_rejected": ["Go", "Python oracle"],
        "python_version": sys.version.split()[0],
        "reference_dependencies": {name: importlib.metadata.version(name) for name in
                                   ("attrs", "jsonschema", "jsonschema-specifications", "referencing", "rpds-py", "typing-extensions")},
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "exchange_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in (output / "go-execution-exchange.json", python_path)},
        "limits": ["Python peer is the proposal's test-only oracle, not an extracted production runtime.",
                   "Rust execution-profile codec interchange remains unproved.",
                   "Frames are independent specimens, not a legal session or workflow execution.",
                   "No Session/release/commit/delivery event, SDK credential or durable store was used."],
    }
    (output / "execution-peer-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("PASS 18 encodings each direction, 13 message types; both semantic drift controls rejected")


if __name__ == "__main__":
    main()
