"""Local build/developer CLI; no production admission or runtime credentials."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid

from describe import scanner_configuration
from inspect_bundle import expected_digest, inspect
from native_bundle import MAX_ARCHIVE, ORDER, assemble, digest, verify_archive

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "golang:1.27.2-bookworm@sha256:5cf287a799e6b94384bad13d16b14904c531f51ba65792237e122ce42b392f61"
RECIPE = "go-developer-cli/v1"
ENV = [
    "PATH=/usr/local/go/bin:/usr/bin:/bin",
    "HOME=/tmp",
    "GOTOOLCHAIN=local",
    "GOWORK=off",
    "CGO_ENABLED=0",
    "GOOS=linux",
    "GOARCH=amd64",
    "GOFLAGS=-mod=readonly",
    "GOMODCACHE=/modules",
    "GOCACHE=/compiler",
]


class Denied(ValueError):
    pass


def write_json(path, value):
    with Path(path).open("x") as output:
        json.dump(value, output, indent=2)
        output.write("\n")


def private_directory(path):
    path = Path(path).absolute()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
    ):
        raise Denied("private developer directory required")
    return path.resolve()


def outside(path, source):
    if path == source or source in path.parents:
        raise Denied("output and cache must be outside source")


def snapshot(source, target):
    files, total = [], 0
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if path.is_symlink():
            raise Denied("source symlink requires explicit materialization")
        if path.is_dir():
            (target / relative).mkdir(parents=True, exist_ok=True)
            continue
        if not path.is_file():
            raise Denied("regular source files required")
        if path.stat().st_size > 128 * 1024 * 1024 - total:
            raise Denied("source snapshot exceeds developer recipe")
        with path.open("rb") as stream:
            raw = stream.read(128 * 1024 * 1024 - total + 1)
        total += len(raw)
        if total > 128 * 1024 * 1024 or len(files) >= 4096:
            raise Denied("source snapshot exceeds developer recipe")
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        files.append({"path": str(relative), "sha256": digest(raw)})
    return digest(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ), files


class Docker:
    def __init__(self, output):
        self.output = output
        self.label = "bifrost.go.developer=" + uuid.uuid4().hex
        self.names = []
        self.elapsed = {}
        self.images = {}

    def command(self, args, log=None):
        started = time.monotonic()
        if log:
            with (self.output / log).open("xb") as stream:
                subprocess.run(
                    ["docker", *args],
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=600,
                )
            self.elapsed[log] = (time.monotonic() - started) * 1000
            return ""
        return subprocess.check_output(
            ["docker", *args], text=True, timeout=600
        ).strip()

    def run(
        self, image, mounts, command, log, *, network=False, workdir="/src", env=ENV
    ):
        name = "bifrost-go-dev-" + uuid.uuid4().hex
        self.names.append(name)
        args = [
            "run",
            "--rm",
            "--name",
            name,
            "--label",
            self.label,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--pids-limit",
            "64" if env is None else "256",
            "--memory",
            "128m" if env is None else "3g",
            "--cpus",
            "2",
            "--tmpfs",
            "/tmp:rw,exec,nosuid,nodev,size=1g",
            "--workdir",
            workdir,
        ]
        if not network:
            args += ["--network", "none"]
        for host, guest, writable in mounts:
            if "," in str(host):
                raise Denied("Docker mount path contains a comma")
            args += [
                "--mount",
                f"type=bind,src={host},dst={guest}" + ("" if writable else ",readonly"),
            ]
        args += (
            [image, "env", "-i", *env, *command]
            if env is not None
            else ["--entrypoint", command[0], image, *command[1:]]
        )
        self.command(args, log)

    def close(self):
        # Each name was created by this invocation; never prune shared resources.
        remaining = self.command(
            ["ps", "-a", "--filter", "label=" + self.label, "--format", "{{.Names}}"]
        )
        if remaining:
            for name in remaining.splitlines():
                if name not in self.names:
                    raise Denied("unexpected developer container identity")
                self.command(["rm", "-f", name])
        inventory = self.command(
            ["ps", "-a", "--filter", "label=" + self.label, "--format", "{{.ID}}"]
        )
        write_json(
            self.output / "cleanup.json",
            {
                "container_label": self.label,
                "remaining_containers": inventory.splitlines(),
                "docker_volumes_created": False,
                "networks_created": False,
                "cache": "explicit developer-owned cache retained",
                "cache_images": self.images,
            },
        )
        if inventory:
            raise Denied("developer containers remain")


def cached_image(docker, cache, kind, dockerfile):
    key = digest(dockerfile.encode())
    tag = f"bifrost-go-developer-{kind}:{key[:24]}"
    registry = cache / "image-references.json"
    references = json.loads(registry.read_text()) if registry.exists() else {}
    created = False
    try:
        image = docker.command(["image", "inspect", tag, "--format", "{{.Id}}"])
    except subprocess.CalledProcessError:
        with tempfile.TemporaryDirectory(prefix="image-", dir=cache) as directory:
            Path(directory, "Dockerfile").write_text(dockerfile)
            docker.command(
                ["build", "--tag", tag, directory], f"{kind}-image-build.txt"
            )
        image = docker.command(["image", "inspect", tag, "--format", "{{.Id}}"])
        created = True
    docker.images[tag] = image
    if tag in references and references[tag] != image:
        raise Denied("developer cache image identity changed")
    if created:
        references[tag] = image
        registry.write_text(json.dumps(references, indent=2) + "\n")
    # An existing tag absent from this cache's registry is borrowed, not owned.
    return image


def json_stream(raw):
    decoder = json.JSONDecoder()
    result = []
    while raw.strip():
        item, end = decoder.raw_decode(raw.lstrip())
        result.append(item)
        raw = raw.lstrip()[end:]
    return result


def prepare_cache(source, output, cache):
    source = source.resolve(strict=True)
    outside(Path(output).resolve(), source)
    outside(Path(cache).resolve(), source)
    output = private_directory(output)
    cache = private_directory(cache)
    if output == cache or output in cache.parents or cache in output.parents:
        raise Denied("output and cache must be separate")
    if any(output.iterdir()):
        raise Denied("use an empty output directory")
    if os.getuid() == 0:
        raise Denied("build/test/run require the nonroot supported Docker lane")
    return source, output, cache


def build_or_test(args):
    source, output, cache = prepare_cache(args.source, args.output, args.cache)
    started = time.monotonic()
    # Single cache owner, no symlink lock replacement or test-written cache reuse.
    lock = os.open(cache / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock, "w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        docker = Docker(output)
        try:
            with tempfile.TemporaryDirectory(prefix="bifrost-go-build-") as directory:
                temporary = Path(directory)
                frozen = temporary / "source"
                frozen.mkdir()
                source_digest, files = snapshot(source, frozen)
                key = digest(
                    (
                        IMAGE
                        + RECIPE
                        + (frozen / "go.mod").read_text()
                        + (frozen / "go.sum").read_text()
                    ).encode()
                )
                state = cache / key
                modules, compiler = state / "modules", state / "compiler"
                modules.mkdir(parents=True, exist_ok=True)
                compiler.mkdir(exist_ok=True)
                warmed = any(compiler.iterdir())
                mount = [(frozen, "/src", False), (modules, "/modules", True)]
                docker.run(
                    IMAGE,
                    mount,
                    ["sh", "-ec", "go mod download; go mod verify"],
                    "modules.txt",
                    network=True,
                    env=[
                        *ENV,
                        "GOCACHE=/tmp/cache",
                        "GOPROXY=https://proxy.golang.org",
                        "GOSUMDB=sum.golang.org",
                        "GONOPROXY=none",
                        "GONOSUMDB=none",
                    ],
                )
                objects = temporary / "objects"
                objects.mkdir()
                mounts = [
                    (frozen, "/src", False),
                    (modules, "/modules", False),
                    (compiler, "/compiler", True),
                    (objects, "/out", True),
                ]
                # Time workflow compilation before vet/test compilation can warm
                # a fresh cache. No artifact is published before all gates pass.
                commands = 'test -z "$(gofmt -l .)"'
                if args.command == "build":
                    commands += "; start=$(date +%s%N); go build -trimpath -buildvcs=false -o /out/workflow ./cmd/workflow; end=$(date +%s%N); echo $((end-start)) > /out/workflow-compile-ns.txt"
                    for name in ("adapter", "protocolprobe", "schema"):
                        commands += f"; go build -trimpath -buildvcs=false -o /out/{name} ./cmd/{name}"
                    commands += "; go mod graph > /out/module-graph.txt; go version > /out/toolchain.txt"
                # Compiler/vet only; go test -c never runs application initializers.
                commands += '; go vet ./...; for package in $(go list ./...); do go test -c -o /tmp/compiled.test "$package"; done'
                docker.run(
                    IMAGE,
                    mounts,
                    ["sh", "-ec", commands],
                    "compiler.txt",
                    env=[*ENV, "GOPROXY=off", "GOSUMDB=off"],
                )
                private_cache = temporary / "test-cache"
                shutil.copytree(compiler, private_cache)
                test_mounts = [
                    (frozen, "/src", False),
                    (modules, "/modules", False),
                    (private_cache, "/compiler", True),
                ]
                docker.run(
                    IMAGE,
                    test_mounts,
                    ["go", "test", "-count=1", "-json", "./..."],
                    "tests.jsonl",
                    env=[*ENV, "GOPROXY=off", "GOSUMDB=off"],
                )
                events = json_stream((output / "tests.jsonl").read_text())
                if any(event.get("Action") == "fail" for event in events):
                    raise Denied("Go tests failed")
                validation = {
                    "go_test": "pass",
                    "go_vet": "pass",
                    "gofmt": "pass",
                    "individual_tests_passed": sum(
                        event.get("Action") == "pass" and "Test" in event
                        for event in events
                    ),
                }
                report = {
                    "recipe": RECIPE,
                    "source_sha256": source_digest,
                    "cache_key": key,
                    "warm_compiler_cache": warmed,
                    "validation": validation,
                    "timings_ms": docker.elapsed,
                }
                if args.command == "build":
                    scanner = cached_image(
                        docker,
                        cache,
                        "scanner",
                        (ROOT / "builder.Dockerfile").read_text(),
                    )
                    docker.run(
                        scanner,
                        mounts[:3],
                        ["/opt/tools/govulncheck", "-json", "./..."],
                        "govulncheck.jsonl",
                        network=True,
                        env=[*ENV, "GOPROXY=off", "GOSUMDB=off"],
                    )
                    scanner_config = scanner_configuration(
                        (output / "govulncheck.jsonl").read_text()
                    )
                    validation["govulncheck"] = "pass"
                    runtime_image = cached_image(
                        docker, cache, "runtime", "FROM scratch\n"
                    )
                    try:
                        commit = subprocess.check_output(
                            ["git", "-C", str(source), "rev-parse", "HEAD"],
                            text=True,
                            stderr=subprocess.DEVNULL,
                        ).strip()
                    except subprocess.CalledProcessError:
                        commit = None  # Exact snapshot digest still identifies a nongit edit.

                    evidence = {
                        "runtime": "go-native/v1",
                        "source_commit": commit,
                        "source_sha256": source_digest,
                        "source_files": files,
                        "go_version": (objects / "toolchain.txt").read_text().strip(),
                        "goos": "linux",
                        "goarch": "amd64",
                        "cgo": False,
                        "entrypoint": "workflow",
                        "sdk_version": "0.0.0-spike.2",
                        "build_flags": [
                            "-mod=readonly",
                            "-trimpath",
                            "-buildvcs=false",
                        ],
                        "validation": validation,
                        "scanner_configuration": scanner_config,
                        "builder_identity": {
                            "kind": "isolated-local-developer",
                            "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
                            "image": IMAGE,
                        },
                        "recipe_version": RECIPE,
                        "manifest_sha256": digest(
                            (frozen / "manifest.json").read_bytes()
                        ),
                    }
                    for name in (
                        "workflow",
                        "adapter",
                        "protocolprobe",
                        "schema",
                        "module-graph.txt",
                    ):
                        shutil.copyfile(objects / name, output / name)
                        if name != "module-graph.txt":
                            (output / name).chmod(0o500)
                    for field, path in {
                        "artifact_sha256": output / "workflow",
                        "native_adapter_sha256": output / "adapter",
                        "module_graph_sha256": output / "module-graph.txt",
                        "input_schema_sha256": frozen / "schemas/input.json",
                        "output_schema_sha256": frozen / "schemas/output.json",
                    }.items():
                        evidence[field] = digest(path.read_bytes())
                    if len(json.dumps(evidence).encode()) > 2 * 1024 * 1024:
                        raise Denied("build evidence exceeds adapter bound")
                    write_json(output / "descriptor.json", evidence)
                    bundle = assemble(frozen, output)
                    shutil.copyfile(
                        frozen / "executionprofile/testdata/structural-vectors.json",
                        output / "vectors.json",
                    )
                    write_json(
                        output / "developer-tools.json",
                        {
                            "protocolprobe_sha256": digest(
                                (output / "protocolprobe").read_bytes()
                            ),
                            "vectors_sha256": digest(
                                (output / "vectors.json").read_bytes()
                            ),
                            "runtime_image": runtime_image,
                            "recipe": RECIPE,
                        },
                    )
                    report.update(
                        artifact_id=bundle["artifact"]["artifact_id"],
                        workflow_sha256=evidence["artifact_sha256"],
                        workflow_compile_ms=int(
                            (objects / "workflow-compile-ns.txt").read_text()
                        )
                        / 1e6,
                    )
                report["total_ms"] = (time.monotonic() - started) * 1000
                write_json(output / "developer-build.json", report)
                return report
        finally:
            docker.close()


def run_bundle(args):
    if os.getuid() == 0:
        raise Denied("run requires the nonroot supported Docker lane")
    output = private_directory(args.output)
    if any(output.iterdir()):
        raise Denied("use an empty run output directory")
    summary = inspect(args.bundle, args.sha256)
    with args.bundle.open("rb") as stream:
        raw = stream.read(MAX_ARCHIVE + 1)
    entries = verify_archive(raw, args.sha256)
    tools = args.bundle.resolve().parent
    config = json.loads((tools / "developer-tools.json").read_text())
    helper_bytes = {}
    for name in ("protocolprobe", "vectors"):
        path = tools / (name + ".json" if name == "vectors" else name)
        with path.open("rb") as stream:
            helper_bytes[name] = stream.read(32 * 1024 * 1024 + 1)
        if (
            len(helper_bytes[name]) > 32 * 1024 * 1024
            or digest(helper_bytes[name]) != config[name + "_sha256"]
        ):
            raise Denied("local supervisor identity mismatch")
    docker = Docker(output)
    try:
        image = config["runtime_image"]
        if (
            not isinstance(image, str)
            or not image.startswith("sha256:")
            or len(image) != 71
        ):
            raise Denied("pinned runtime image required")
        if docker.command(["image", "inspect", image, "--format", "{{.Id}}"]) != image:
            raise Denied("runtime image unavailable; build it in the developer plane")
        with tempfile.TemporaryDirectory(prefix="bifrost-go-run-") as directory:
            stage = Path(directory) / "bundle"
            stage.mkdir()
            for name in ORDER:
                (stage / name).write_bytes(entries[name])
                (stage / name).chmod(
                    0o500 if name in ("adapter", "workflow") else 0o400
                )
            supervisor, vectors = (
                Path(directory) / "protocolprobe",
                Path(directory) / "vectors.json",
            )
            supervisor.write_bytes(helper_bytes["protocolprobe"])
            supervisor.chmod(0o500)
            vectors.write_bytes(helper_bytes["vectors"])
            vectors.chmod(0o400)
            mounts = [
                (stage, "/bundle", False),
                (supervisor, "/protocolprobe", False),
                (vectors, "/vectors.json", False),
                (output, "/out", True),
            ]
            command = [
                "/protocolprobe",
                "--adapter",
                "/bundle/adapter",
                "--workflow",
                "/bundle/workflow",
                "--workflow-sha256",
                summary["workflow_sha256"],
                "--artifact-id",
                summary["artifact_id"],
                "--evidence",
                "/bundle/build-evidence.json",
                "--graph",
                "/bundle/module-graph.txt",
                "--input-schema",
                "/bundle/input-schema.json",
                "--output-schema",
                "/bundle/output-schema.json",
                "--vectors",
                "/vectors.json",
                "--output",
                "/out/execution.json",
            ]
            if args.descending:
                command.append("--descending")
            docker.run(
                image, mounts, command, "local-run.txt", workdir="/tmp", env=None
            )
        result = json.loads((output / "execution.json").read_text())
        if result["failed_case"] or len(result["observations"]) != 5:
            raise Denied("local protocol execution failed")
        report = {
            "artifact_id": summary["artifact_id"],
            "workflow_sha256": summary["workflow_sha256"],
            "synthetic_sdk": True,
            "durable_projection": False,
            "compiler_present": False,
            "observations": result["observations"],
            "timings_ms": docker.elapsed,
        }
        write_json(output / "developer-run.json", report)
        return report
    finally:
        docker.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("test", "build"):
        command = commands.add_parser(name)
        command.add_argument("--source", type=Path, default=ROOT)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument(
            "--cache", type=Path, default=Path.home() / ".cache/bifrost-go"
        )
    for name in ("inspect", "run"):
        command = commands.add_parser(name)
        command.add_argument("bundle", type=Path)
        command.add_argument("--sha256", type=expected_digest, required=True)
        if name == "run":
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--descending", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            result = inspect(args.bundle, args.sha256)
        elif args.command == "run":
            result = run_bundle(args)
        else:
            result = build_or_test(args)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        print(
            "Go developer command failed; inspect its output directory for diagnostics",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
