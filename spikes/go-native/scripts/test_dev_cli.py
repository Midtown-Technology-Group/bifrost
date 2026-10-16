"""Developer-plane boundary tests; Docker work is exercised in hosted CI."""

import argparse
import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import dev_cli
from native_bundle import canonical_archive, digest
from test_native_bundle import fixture


class DeveloperBoundaryTests(unittest.TestCase):
    def test_snapshot_binds_an_edit_and_refuses_source_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source, first, second = root / "source", root / "one", root / "two"
            for path in (source, first, second):
                path.mkdir()
            (source / "input.go").write_text(
                "package p; type Input struct { Name string }"
            )
            one, _ = dev_cli.snapshot(source, first)
            (source / "input.go").write_text(
                "package p; type Input struct { Ready bool }"
            )
            two, _ = dev_cli.snapshot(source, second)
            self.assertNotEqual(one, two)
            self.assertNotEqual(
                (first / "input.go").read_bytes(), (second / "input.go").read_bytes()
            )
            (source / "escape").symlink_to(root / "private")
            with self.assertRaises(dev_cli.Denied):
                dev_cli.snapshot(source, root / "third")

    def test_output_and_cache_are_rejected_before_source_is_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "source"
            source.mkdir()
            with self.assertRaises(dev_cli.Denied):
                dev_cli.prepare_cache(source, source / "output", root / "cache")
            self.assertEqual(list(source.iterdir()), [])

    def test_runtime_plan_has_no_compiler_or_source_cache_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            docker = dev_cli.Docker(pathlib.Path(directory))
            with mock.patch.object(docker, "command") as call:
                docker.run(
                    "sha256:" + "a" * 64,
                    [
                        ("/private/bundle", "/bundle", False),
                        ("/private/output", "/out", True),
                    ],
                    ["/protocolprobe", "--workflow", "/bundle/workflow"],
                    "run.txt",
                    workdir="/tmp",
                    env=None,
                )
            argv = call.call_args.args[0]
            self.assertIn("--network", argv)
            self.assertEqual(argv[argv.index("--network") + 1], "none")
            self.assertIn("type=bind,src=/private/bundle,dst=/bundle,readonly", argv)
            self.assertNotIn("/compiler", " ".join(argv))
            self.assertNotIn("/modules", " ".join(argv))
            self.assertNotIn("go build", " ".join(argv))
            self.assertNotIn("env", argv)
            self.assertEqual(argv[argv.index("--entrypoint") + 1], "/protocolprobe")

    def test_wrong_archive_pin_is_denied_before_any_runtime_docker_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            bundle, output = root / "bundle.tar", root / "output"
            bundle.write_bytes(canonical_archive(fixture()))
            output.mkdir()
            args = argparse.Namespace(
                bundle=bundle, output=output, sha256="f" * 64, descending=False
            )
            with (
                mock.patch.object(dev_cli.os, "getuid", return_value=1000),
                mock.patch.object(dev_cli, "private_directory", return_value=output),
                mock.patch.object(dev_cli, "Docker") as docker,
            ):
                with self.assertRaises(ValueError):
                    dev_cli.run_bundle(args)
                docker.assert_not_called()

    def test_inspect_command_needs_no_docker_and_claims_no_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = pathlib.Path(directory) / "bundle.tar"
            raw = canonical_archive(fixture())
            bundle.write_bytes(raw)
            stdout = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                mock.patch.object(dev_cli, "Docker") as docker,
            ):
                self.assertEqual(
                    dev_cli.main(["inspect", str(bundle), "--sha256", digest(raw)]), 0
                )
                docker.assert_not_called()
            self.assertEqual(
                json.loads(stdout.getvalue())["deployment_admission"], "not evaluated"
            )


if __name__ == "__main__":
    unittest.main()
