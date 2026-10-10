"""Recipe boundary tests, run in the existing hosted build plane."""

import io
import json
import pathlib
import tarfile
import tempfile
import unittest

from native_bundle import (
    ORDER,
    Rejected,
    assemble,
    canonical_archive,
    digest,
    verify_archive,
)


def fixture():
    entries = {name: (name + "-fixture").encode() for name in ORDER}
    entries["input-schema.json"] = entries["output-schema.json"] = b'{"type":"object"}'
    evidence = {
        "runtime": "go-native/v1",
        "goos": "linux",
        "goarch": "amd64",
        "cgo": False,
        "entrypoint": "workflow",
        "sdk_version": "0.0.0-spike.2",
        "go_version": "go1.27.1",
        "build_flags": ["-mod=readonly", "-trimpath", "-buildvcs=false"],
        "validation": {
            key: "pass" for key in ("go_test", "go_vet", "gofmt", "govulncheck")
        },
        "source_commit": "a" * 40,
        "source_sha256": "b" * 64,
        "builder_identity": {"run_id": "synthetic"},
    }
    for field, name in (
        ("artifact_sha256", "workflow"),
        ("native_adapter_sha256", "adapter"),
        ("module_graph_sha256", "module-graph.txt"),
        ("input_schema_sha256", "input-schema.json"),
        ("output_schema_sha256", "output-schema.json"),
    ):
        evidence[field] = digest(entries[name])
    entries["build-evidence.json"] = json.dumps(evidence).encode()
    return entries


def alternate_archive(entries, *, order=ORDER, change=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in order:
            header = tarfile.TarInfo(name)
            header.size = len(entries[name])
            header.mode = 0o500 if name in ("adapter", "workflow") else 0o400
            if change:
                change(header)
            archive.addfile(header, io.BytesIO(entries[name]))
    return output.getvalue()


class BundleRecipeTests(unittest.TestCase):
    def test_roundtrip_preserves_all_bytes_and_canonical_headers(self):
        entries = fixture()
        blob = canonical_archive(entries)
        self.assertEqual(verify_archive(blob, digest(blob)), entries)
        self.assertEqual(blob, canonical_archive(dict(reversed(list(entries.items())))))
        self.assertEqual(len(blob) % 10240, 0)
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as archive:
            for header in archive:
                self.assertEqual(header.uid, 0)
                self.assertEqual(header.gid, 0)
                self.assertEqual(header.mtime, 0)
                self.assertEqual(header.uname, "")
                self.assertEqual(header.gname, "")
                self.assertTrue(header.isreg())

    def test_changed_bytes_and_wrong_pin_are_denied(self):
        blob = canonical_archive(fixture())
        with self.assertRaises(Rejected):
            verify_archive(blob, "f" * 64)
        changed = bytearray(blob)
        changed[512] ^= 1
        with self.assertRaises(Rejected):
            verify_archive(bytes(changed), digest(blob))

    def test_even_a_rehashed_archive_cannot_change_header_or_hide_data(self):
        entries = fixture()
        alternatives = [
            alternate_archive(entries, order=tuple(reversed(ORDER))),
            alternate_archive(entries, change=lambda h: setattr(h, "mtime", 1)),
            alternate_archive(entries, change=lambda h: setattr(h, "mode", 0o777)),
            alternate_archive(entries, change=lambda h: setattr(h, "uid", 1000)),
            alternate_archive(
                entries, change=lambda h: setattr(h, "name", "../" + h.name)
            ),
            alternate_archive(
                entries, change=lambda h: setattr(h, "type", tarfile.SYMTYPE)
            ),
            canonical_archive(entries) + b"hidden trailing data",
            canonical_archive(entries)[:-512],
        ]
        for blob in alternatives:
            with self.subTest(sha=digest(blob)), self.assertRaises(Rejected):
                verify_archive(blob, digest(blob))

    def test_closed_entry_set_and_bounds(self):
        for change in ("extra", "missing", "empty", "oversize"):
            entries = fixture()
            if change == "extra":
                entries["credentials"] = b"forbidden"
            elif change == "missing":
                del entries["module-graph.txt"]
            elif change == "empty":
                entries["workflow"] = b""
            else:
                entries["input-schema.json"] = b" " * (1024 * 1024 + 1)
            with self.subTest(change=change), self.assertRaises(Rejected):
                canonical_archive(entries)

    def test_changed_payloads_fail_retained_evidence_even_with_new_archive_pin(self):
        for name in (
            "workflow",
            "adapter",
            "input-schema.json",
            "output-schema.json",
            "module-graph.txt",
        ):
            entries = fixture()
            entries[name] += b"changed"
            blob = canonical_archive(entries)
            with self.subTest(name=name), self.assertRaises(Rejected):
                verify_archive(blob, digest(blob))

    def test_duplicate_metadata_nonfinite_and_failed_validation_are_denied(self):
        entries = fixture()
        originals = entries["build-evidence.json"]
        evidence = json.loads(originals)
        evidence["validation"]["govulncheck"] = "failure"
        for raw in (
            b'{"runtime":"go-native/v1",' + originals[1:],
            b'{"runtime":NaN}',
            json.dumps(evidence).encode(),
        ):
            entries["build-evidence.json"] = raw
            blob = canonical_archive(entries)
            with self.subTest(raw_digest=digest(raw)), self.assertRaises(Rejected):
                verify_archive(blob, digest(blob))

    def test_assembly_binds_external_descriptor_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            source, output = base / "source", base / "output"
            (source / "schemas").mkdir(parents=True)
            output.mkdir()
            for name, raw in fixture().items():
                path = (
                    source / "schemas" / name.replace("-schema", "")
                    if name.endswith("-schema.json")
                    else output / name
                )
                if name == "build-evidence.json":
                    path = output / "descriptor.json"
                path.write_bytes(raw)
            descriptor = assemble(source, output)
            blob = (output / "native-bundle.tar").read_bytes()
            self.assertEqual(
                descriptor["artifact"]["artifact_id"], "sha256:" + digest(blob)
            )
            self.assertEqual(
                descriptor["artifact"]["executable_sha256"],
                digest(fixture()["workflow"]),
            )
            self.assertFalse(descriptor["runtime_acceptance"])
            with self.assertRaises(FileExistsError):
                assemble(source, output)
            (output / "native-bundle.tar").unlink()
            (output / "adapter").unlink()
            (output / "adapter").symlink_to(output / "workflow")
            with self.assertRaises(Rejected):
                assemble(source, output)


if __name__ == "__main__":
    unittest.main()
