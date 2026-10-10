"""Trusted build-plane bundle recipe; no application import or execution.

This is an isolated candidate, not production registration or runtime acceptance.
Artifact identity hashes all immutable archive bytes, never its own descriptor.
"""

from __future__ import annotations

import hashlib
import io
import json
import pathlib
import sys
import tarfile

VERSION = "isolated-native-bundle/v1"
ORDER = (
    "adapter",
    "build-evidence.json",
    "input-schema.json",
    "module-graph.txt",
    "output-schema.json",
    "workflow",
)
LIMITS = {name: 1024 * 1024 for name in ORDER}
LIMITS.update(
    adapter=32 * 1024 * 1024,
    workflow=32 * 1024 * 1024,
    **{"build-evidence.json": 16 * 1024 * 1024},
)
MAX_ARCHIVE = 96 * 1024 * 1024


class Rejected(ValueError):
    pass


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def closed_json(raw: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Rejected("duplicate metadata field")
            result[key] = value
        return result

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                Rejected("nonfinite metadata")
            ),
        )
    except (ValueError, UnicodeError, RecursionError):
        raise Rejected("invalid metadata") from None


def canonical_archive(entries: dict[str, bytes]) -> bytes:
    if set(entries) != set(ORDER):
        raise Rejected("closed bundle entry set required")
    output = bytearray()
    for name in ORDER:
        content = entries[name]
        if type(content) is not bytes or not 0 < len(content) <= LIMITS[name]:
            raise Rejected("entry exceeds recipe bound")
        # Specify the exact header bytes instead of depending on a Python
        # tarfile release's serialization choices for regular device fields.
        header = bytearray(512)
        encoded_name = name.encode("ascii")
        header[: len(encoded_name)] = encoded_name
        mode = 0o500 if name in ("adapter", "workflow") else 0o400
        header[100:108] = f"{mode:07o}\0".encode("ascii")
        header[108:116] = header[116:124] = b"0000000\0"
        header[124:136] = f"{len(content):011o}\0".encode("ascii")
        header[136:148] = b"00000000000\0"
        header[148:156] = b"        "
        header[156] = ord("0")
        header[257:265] = b"ustar\x00" + b"00"
        header[148:156] = f"{sum(header):06o}\0 ".encode("ascii")
        output.extend(header)
        output.extend(content)
        output.extend(b"\0" * ((512 - len(content) % 512) % 512))
    length = ((len(output) + 1024 + 10239) // 10240) * 10240
    if length > MAX_ARCHIVE:
        raise Rejected("archive exceeds recipe bound")
    output.extend(b"\0" * (length - len(output)))
    return bytes(output)


def verify_archive(raw: bytes, accepted_sha256: str) -> dict[str, bytes]:
    """Read a fixed closed archive without extracting paths into a filesystem."""
    if (
        type(raw) is not bytes
        or not 0 < len(raw) <= MAX_ARCHIVE
        or digest(raw) != accepted_sha256
    ):
        raise Rejected("accepted archive identity mismatch")
    entries = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for expected in ORDER:
                item = archive.next()
                if (
                    item is None
                    or item.name != expected
                    or not item.isreg()
                    or item.type != tarfile.REGTYPE
                    or item.linkname
                    or item.pax_headers
                    or not 0 < item.size <= LIMITS[expected]
                ):
                    raise Rejected("invalid closed archive entry")
                stream = archive.extractfile(item)
                if stream is None:
                    raise Rejected("missing archive content")
                entries[expected] = stream.read(LIMITS[expected] + 1)
                if len(entries[expected]) != item.size:
                    raise Rejected("truncated archive content")
            if archive.next() is not None:
                raise Rejected("extra archive entry")
        # Also rejects reordered headers, changed modes/owner/time, PAX/GNU
        # extensions, alternate padding and hidden trailing archives or data.
        if canonical_archive(entries) != raw:
            raise Rejected("archive is not the canonical recipe")
        validate_evidence(entries)
        return entries
    except (tarfile.TarError, OSError, OverflowError):
        raise Rejected("invalid archive") from None


def validate_evidence(entries: dict[str, bytes]) -> dict:
    evidence = closed_json(entries["build-evidence.json"])
    if not isinstance(evidence, dict):
        raise Rejected("build evidence object required")
    bindings = {
        "artifact_sha256": "workflow",
        "native_adapter_sha256": "adapter",
        "module_graph_sha256": "module-graph.txt",
        "input_schema_sha256": "input-schema.json",
        "output_schema_sha256": "output-schema.json",
    }
    if (
        any(
            evidence.get(field) != digest(entries[name])
            for field, name in bindings.items()
        )
        or evidence.get("runtime") != "go-native/v1"
        or evidence.get("goos") != "linux"
        or evidence.get("goarch") != "amd64"
        or evidence.get("cgo") is not False
        or evidence.get("entrypoint") != "workflow"
        or evidence.get("sdk_version") != "0.0.0-spike.2"
        or evidence.get("build_flags")
        != ["-mod=readonly", "-trimpath", "-buildvcs=false"]
    ):
        raise Rejected("build inputs do not bind the native bundle")
    checks = evidence.get("validation")
    if not isinstance(checks, dict) or any(
        checks.get(k) != "pass" for k in ("go_test", "go_vet", "gofmt", "govulncheck")
    ):
        raise Rejected("missing producer validation")
    for name in ("input-schema.json", "output-schema.json"):
        if not isinstance(closed_json(entries[name]), dict):
            raise Rejected("schema object required")
    return evidence


def assemble(root: pathlib.Path, output: pathlib.Path) -> dict:
    """After isolated compilation/scanning, package the identified immutable bytes.

    The caller owns authenticated producer/signature verification and review;
    matching hashes and a claimed validation field are not trust or admission.
    """
    paths = {
        "adapter": output / "adapter",
        "workflow": output / "workflow",
        "build-evidence.json": output / "descriptor.json",
        "module-graph.txt": output / "module-graph.txt",
        "input-schema.json": root / "schemas/input.json",
        "output-schema.json": root / "schemas/output.json",
    }
    entries = {}
    for name, path in paths.items():
        if (
            path.is_symlink()
            or path.resolve(strict=True) != path.absolute()
            or not path.is_file()
            or not 0 < path.stat().st_size <= LIMITS[name]
        ):
            raise Rejected("invalid producer input")
        with path.open("rb") as stream:
            entries[name] = stream.read(LIMITS[name] + 1)
        if not 0 < len(entries[name]) <= LIMITS[name]:
            raise Rejected("producer input changed or exceeded bound")
    evidence = validate_evidence(entries)
    blob = canonical_archive(entries)
    archive_digest = digest(blob)
    verified = verify_archive(blob, archive_digest)
    if verified != entries:
        raise Rejected("bundle verification mismatch")
    descriptor = {
        "bundle_recipe": VERSION,
        "artifact": {
            "kind": "native-executable/v1",
            "artifact_id": "sha256:" + archive_digest,
            "runtime_protocol": "bifrost.runtime/v1",
            "image_digest": None,
            "adapter_sha256": digest(entries["adapter"]),
            "executable_sha256": digest(entries["workflow"]),
            "build_evidence_sha256": digest(entries["build-evidence.json"]),
            "sdk": {
                "distribution": "github.com/midtown-technology-group/bifrost-go",
                "version": evidence["sdk_version"],
            },
            "platform": {"os": "linux", "architecture": "amd64"},
            "toolchain": {"implementation": "go", "version": evidence["go_version"]},
            "dependencies": {
                "kind": "go-module-graph/v1",
                "digest": "sha256:" + digest(entries["module-graph.txt"]),
            },
        },
        "archive_size_bytes": len(blob),
        "entries": [
            {
                "path": name,
                "sha256": digest(entries[name]),
                "size_bytes": len(entries[name]),
            }
            for name in ORDER
        ],
        "source_commit": evidence["source_commit"],
        "source_sha256": evidence["source_sha256"],
        "producer_run_id": evidence["builder_identity"]["run_id"],
        "input_schema_sha256": digest(entries["input-schema.json"]),
        "output_schema_sha256": digest(entries["output-schema.json"]),
        "entrypoint": "workflow",
        "reviewed_deployment": False,
        "runtime_acceptance": False,
    }
    # Exclusive creation prevents replacing a prior output with another input.
    with (output / "native-bundle.tar").open("xb") as stream:
        stream.write(blob)
    with (output / "native-bundle-descriptor.json").open("x") as stream:
        stream.write(json.dumps(descriptor, indent=2) + "\n")
    return descriptor


if __name__ == "__main__":
    try:
        print(
            json.dumps(
                assemble(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])), indent=2
            )
        )
    except (Rejected, OSError, KeyError, IndexError, TypeError):
        raise SystemExit("native bundle rejected") from None
