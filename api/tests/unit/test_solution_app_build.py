"""SolutionAppBuilder — server-side vite build → _apps/{app_id}/dist/ (criterion 12).

The builder turns a v2 app's transient src/ into a built dist/ and uploads it to
_apps/{app_id}/dist/. A bundle may instead ship a prebuilt dist/ (disconnected
fast-path), in which case the vite build is skipped entirely. src/ is NEVER
written under _solutions/.
"""
from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from contextlib import asynccontextmanager

import pytest

import src.services.sdk_package as sdkpkg
from src.services.solutions import app_build as ab
from src.services.solutions.app_build import SolutionAppBuilder
from src.services.solutions.deploy import SolutionDeployConflict


@pytest.mark.parametrize(("stderr", "code"), [
    (None, "unknown"),
    (b"", "unknown"),
    (b"npm error code ERESOLVE\ncustomer=HOSTILE_SECRET", "ERESOLVE"),
    ("npm ERR! code ECONNRESET\r\nAuthorization: HOSTILE_SECRET", "ECONNRESET"),
    (b"npm error code HOSTILE_SECRET", "unknown"),
    (b"npm error code ERESOLVE https://HOSTILE_SECRET", "unknown"),
    (b"prefix npm error code E401", "unknown"),
    (b"npm error code E401\nnpm error code E403", "unknown"),
    (b"\xffnpm error code E401", "unknown"),
    (b"x" * 8192 + b"\nnpm error code E401", "unknown"),
])
def test_npm_failure_observer_preserves_exception_privacy_and_cleanup(
    monkeypatch, caplog, stderr, code,
):
    builder = object.__new__(SolutionAppBuilder)
    workdirs = []
    calls = []
    error = subprocess.CalledProcessError(
        1, ["HOSTILE_SECRET"], output=b"stdout HOSTILE_SECRET", stderr=stderr,
    )

    def materialize(workdir, *_args):
        workdirs.append(workdir)
        (workdir / "fixture").write_text("HOSTILE_SECRET")

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[1] == "install":
            raise error
        return subprocess.CompletedProcess(argv, 0, b"v20.19.0\n" if argv[0] == "node" else b"10.9.3\n")

    monkeypatch.setattr(builder, "_materialize", materialize)
    monkeypatch.setattr(ab.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError) as caught:
        builder.compile_dist(uuid.uuid4(), {}, {})

    assert caught.value is error
    assert len(workdirs) == 1
    assert not Path(workdirs[0]).exists()
    assert calls[0][0] == ["npm", "install", "--no-audit", "--no-fund", "--package-lock=false"]
    assert calls[0][1] == {
        "cwd": str(workdirs[0]), "check": True, "capture_output": True,
        "timeout": ab._BUILD_STEP_TIMEOUT_S,
    }
    assert [argv for argv, _ in calls[1:]] == [["node", "--version"], ["npm", "--version"]]
    assert len(caplog.records) == 1
    assert caplog.records[0].getMessage() == (
        f"App build step=npm_install exit_status=1 npm_code={code} "
        "node_version=v20.19.0 npm_version=10.9.3 "
        + " ".join(f"{key}={str(value).lower() if isinstance(value, bool) else value}"
                   for key, value in ab._npm_stderr_structure(stderr).items() if key != "npm_code")
    )
    assert "HOSTILE_SECRET" not in caplog.text
    assert caplog.records[0].exc_info is None


@pytest.mark.parametrize(("stderr", "state", "kind", "format_class", "code", "truncated", "exit_handler"), [
    (None, "absent", "none", "unmatched", "unknown", False, False),
    (b"", "empty", "none", "unmatched", "unknown", False, False),
    (b"npm error code ERESOLVE\r\n", "present", "allowlisted", "plain", "ERESOLVE", False, False),
    (b"\x1b[1mnpm\x1b[22m \x1b[31merror\x1b[39m code E401", "present", "allowlisted", "sgr", "E401", False, False),
    (b"HOSTILE_SECRET error code E401", "present", "allowlisted", "alternate_heading", "E401", False, False),
    (b"npm error code 1", "present", "numeric", "plain", "unknown", False, False),
    (b"npm error code HOSTILE_SECRET", "present", "unlisted", "plain", "unknown", False, False),
    (b"npm error code E401\nnpm error code E403", "present", "conflicting", "plain", "unknown", False, False),
    (b"npm error code E401\n" * 17, "present", "overflow", "plain", "unknown", False, False),
    (b"npm error code E401\n" * 16, "present", "allowlisted", "plain", "E401", False, False),
    (b"x" * 8192 + b"\nnpm error code E401", "present", "none", "unmatched", "unknown", True, False),
    ("é" * 4096 + "\nnpm error code E401", "present", "none", "unmatched", "unknown", True, False),
    (b"npm error code E401\n" + b"x" * 8192, "present", "allowlisted", "plain", "unknown", True, False),
    (b"\xffnpm error code E401", "present", "none", "unmatched", "unknown", False, False),
    (b"npm error code E401 https://HOSTILE_SECRET", "present", "none", "unmatched", "unknown", False, False),
    (b"\x1b[999999999m npm error code E401", "present", "none", "unmatched", "unknown", False, False),
    (b"npm error Exit handler never called!", "present", "none", "unmatched", "unknown", False, True),
    (b"npm error code E401\n\x1b[31mnpm error code E401\x1b[0m", "present", "allowlisted", "mixed", "E401", False, False),
])
def test_npm_structure_has_closed_schema_and_byte_bound(
    stderr, state, kind, format_class, code, truncated, exit_handler,
):
    observed = ab._npm_stderr_structure(stderr)
    assert observed == {
        "stderr_state": state, "scan_truncated": truncated,
        "code_line_class": kind, "format_class": format_class,
        "npm_code": code, "exit_handler_never_called": exit_handler,
    }
    assert "HOSTILE_SECRET" not in str(observed)


def test_npm_structural_record_is_bounded_and_private(monkeypatch, caplog):
    monkeypatch.setattr(ab.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, b"1.2.3"))
    ab._observe_npm_install_failure(subprocess.CalledProcessError(
        1, ["HOSTILE_SECRET"], output=b"HOSTILE_SECRET", stderr=b"HOSTILE_SECRET error code E401",
    ))
    record = caplog.records[0].getMessage()
    assert len(record.encode("ascii")) <= 512
    assert "HOSTILE_SECRET" not in record
    assert "format_class=alternate_heading" in record
    assert caplog.records[0].exc_info is None


@pytest.mark.parametrize("output", [b"HOSTILE_SECRET", b"20.19.0\nHOSTILE_SECRET", b"1" * 33, b"\xff"])
def test_npm_observer_rejects_malformed_versions(monkeypatch, caplog, output):
    monkeypatch.setattr(ab.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, output))
    ab._observe_npm_install_failure(subprocess.CalledProcessError(1, ["npm"]))
    assert caplog.records[0].getMessage() == (
        "App build step=npm_install exit_status=1 npm_code=unknown "
        "node_version=unknown npm_version=unknown stderr_state=absent "
        "scan_truncated=false code_line_class=none format_class=unmatched exit_handler_never_called=false"
    )
    assert "HOSTILE_SECRET" not in caplog.text


@pytest.mark.parametrize("failure", [OSError("HOSTILE_SECRET"), subprocess.TimeoutExpired("HOSTILE_SECRET", 2)])
def test_npm_observer_unavailable_versions_remain_unknown(monkeypatch, caplog, failure):
    def run(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(ab.subprocess, "run", run)
    ab._observe_npm_install_failure(subprocess.CalledProcessError(1, ["npm"]))
    assert "node_version=unknown npm_version=unknown" in caplog.text
    assert "HOSTILE_SECRET" not in caplog.text


def test_npm_observer_logging_failure_does_not_replace_install_error(monkeypatch, tmp_path):
    builder = object.__new__(SolutionAppBuilder)
    error = subprocess.CalledProcessError(1, ["npm"])

    def run(argv, **_kwargs):
        if argv[1] == "install":
            raise error
        return subprocess.CompletedProcess(argv, 0, b"1.2.3")

    def broken_log(*_args, **_kwargs):
        raise RuntimeError("HOSTILE_SECRET")

    monkeypatch.setattr(ab.subprocess, "run", run)
    monkeypatch.setattr(ab.logger, "warning", broken_log)
    with pytest.raises(subprocess.CalledProcessError) as caught:
        builder._run_vite_build(tmp_path)
    assert caught.value is error


def test_npm_observer_does_not_run_for_vite_failure(monkeypatch, tmp_path, caplog):
    builder = object.__new__(SolutionAppBuilder)
    calls = []
    error = subprocess.CalledProcessError(1, ["npx", "vite"], stderr=b"npm error code E401")

    def run(argv, **_kwargs):
        calls.append(argv)
        if argv[0] == "npx":
            raise error
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(ab.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError) as caught:
        builder._run_vite_build(tmp_path)
    assert caught.value is error
    assert len(calls) == 2
    assert not caplog.records


@pytest.mark.e2e
async def test_prebuilt_dist_is_used_without_building(monkeypatch):
    """A non-empty prebuilt_dist short-circuits the vite build and is uploaded."""
    built = {"index.html": b"<html>v2</html>", "assets/app-abc.js": b"//js"}
    app_id = uuid.uuid4()

    b = SolutionAppBuilder()

    def _boom(*a, **k):
        raise AssertionError("vite build must not run when prebuilt dist is supplied")

    monkeypatch.setattr(b, "_run_vite_build", _boom)

    out = await b.build(
        app_id=app_id,
        src_files={},
        dependencies={},
        prebuilt_dist=built,
    )
    assert set(out) == set(built)

    # And the uploaded dist is readable back from _apps/{app_id}/dist/.
    index = await b.read_dist(app_id, "index.html")
    assert index == b"<html>v2</html>"


@pytest.mark.e2e
async def test_build_runs_vite_when_no_prebuilt_dist(monkeypatch):
    """With no prebuilt dist, the builder runs vite and uploads the produced dist."""
    app_id = uuid.uuid4()
    b = SolutionAppBuilder()

    captured = {}

    def _fake_vite(workdir, base="/"):
        captured["workdir"] = workdir
        captured["base"] = base
        return {"index.html": b"<html>built</html>"}

    monkeypatch.setattr(b, "_run_vite_build", _fake_vite)

    out = await b.build(
        app_id=app_id,
        src_files={"src/main.tsx": b"console.log(1)", "index.html": b"<html></html>"},
        dependencies={"bifrost": "1.0.0"},
        prebuilt_dist=None,
    )
    assert out == {"index.html": b"<html>built</html>"}
    assert "workdir" in captured
    # base is the serving route so emitted asset URLs resolve there, not root.
    assert captured["base"] == f"/api/applications/{app_id}/dist/"


@pytest.mark.e2e
def test_materialize_vendors_bifrost_sdk(monkeypatch, tmp_path):
    """`_materialize` drops the bifrost SDK tarball into the workspace and adds a
    file: dependency so `import from "bifrost"` resolves during the build (P1-a).
    The SDK build is stubbed (no esbuild needed for this assertion)."""
    import json

    from src.services.solutions import app_build as ab

    monkeypatch.setattr(ab, "build_sdk_tarball", lambda v: b"FAKE_TGZ", raising=False)
    # patch the symbol the method imports locally
    monkeypatch.setattr(sdkpkg, "build_sdk_tarball", lambda v: b"FAKE_TGZ")

    b = SolutionAppBuilder()
    b._materialize(tmp_path, {"src/main.tsx": b"export {}"}, {"react": "^18"})

    # tarball vendored
    assert (tmp_path / "bifrost-sdk.tgz").read_bytes() == b"FAKE_TGZ"
    # package.json carries app deps + the file: ref to the SDK
    pkg = json.loads((tmp_path / "package.json").read_text())
    assert pkg["dependencies"]["react"] == "^18"
    assert pkg["dependencies"]["bifrost"] == "file:./bifrost-sdk.tgz"


@pytest.mark.e2e
def test_materialize_merges_into_app_package_json(monkeypatch, tmp_path):
    """If the app shipped its own package.json, the SDK ref is merged in, not
    clobbered (the app keeps its scripts/deps)."""
    import json

    monkeypatch.setattr(sdkpkg, "build_sdk_tarball", lambda v: b"FAKE_TGZ")

    app_pkg = {"name": "my-app", "scripts": {"dev": "vite"},
               "dependencies": {"lodash": "^4"}}
    (tmp_path / "package.json").write_text(json.dumps(app_pkg))

    b = SolutionAppBuilder()
    b._materialize(tmp_path, {}, {})

    pkg = json.loads((tmp_path / "package.json").read_text())
    assert pkg["scripts"] == {"dev": "vite"}          # preserved
    assert pkg["dependencies"]["lodash"] == "^4"       # preserved
    assert pkg["dependencies"]["bifrost"] == "file:./bifrost-sdk.tgz"


@pytest.mark.e2e
def test_build_subprocesses_have_timeouts(monkeypatch, tmp_path):
    """Both npm install and vite build run under the self-renewing per-install
    write lock — without a timeout, a hanging npm postinstall wedges the install
    until process restart (every deploy 409s). Both calls must carry one."""

    calls: list[tuple[list[str], dict]] = []

    def _fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(ab.subprocess, "run", _fake_run)
    (tmp_path / "dist").mkdir()

    b = SolutionAppBuilder()
    out = b._run_vite_build(tmp_path, base="/x/")

    assert out == {}
    assert len(calls) == 2
    assert calls[0][0] == [
        "npm",
        "install",
        "--no-audit",
        "--no-fund",
        "--package-lock=false",
    ]
    for _argv, kwargs in calls:
        assert kwargs.get("timeout") == ab._BUILD_STEP_TIMEOUT_S


@pytest.mark.e2e
def test_build_timeout_translated_to_deploy_failure(monkeypatch):
    """A TimeoutExpired from the npm/vite step surfaces as the deploy's
    build-failure exception (409 with the reason), not a raw TimeoutExpired."""

    monkeypatch.setattr(sdkpkg, "build_sdk_tarball", lambda v: b"FAKE_TGZ")
    app_id = uuid.uuid4()
    b = SolutionAppBuilder()

    def _hang(workdir, base="/"):
        raise subprocess.TimeoutExpired(cmd=["npm", "install"], timeout=ab._BUILD_STEP_TIMEOUT_S)

    monkeypatch.setattr(b, "_run_vite_build", _hang)

    with pytest.raises(SolutionDeployConflict, match="timed out"):
        b.compile_dist(app_id, src_files={}, dependencies={}, prebuilt_dist=None)


@pytest.mark.e2e
async def test_redeploy_clears_stale_dist_files(monkeypatch):
    """A second build with a different file set must not leave the old files
    fetchable (full replace of the dist/ artifact)."""
    app_id = uuid.uuid4()
    b = SolutionAppBuilder()
    monkeypatch.setattr(b, "_run_vite_build", lambda *a, **k: {})

    # First deploy ships index.html + an old hashed asset.
    await b.build(
        app_id=app_id, src_files={}, dependencies={},
        prebuilt_dist={"index.html": b"<html>v1</html>", "assets/old-aaa.js": b"//old"},
    )
    assert await b.read_dist(app_id, "assets/old-aaa.js") == b"//old"

    # Redeploy with a renamed asset — the old one must be gone.
    await b.build(
        app_id=app_id, src_files={}, dependencies={},
        prebuilt_dist={"index.html": b"<html>v2</html>", "assets/new-bbb.js": b"//new"},
    )
    assert await b.read_dist(app_id, "assets/new-bbb.js") == b"//new"
    assert "assets/old-aaa.js" not in await b.list_dist(app_id)


@pytest.mark.e2e
async def test_delete_all_app_artifacts_deletes_legacy_and_versioned_paginated(
    monkeypatch,
):
    app_id = uuid.uuid4()
    prefix = f"_apps/{app_id}/"
    pages = {
        None: {
            "Contents": [
                {"Key": f"{prefix}dist/index.html"},
                {"Key": f"{prefix}deployments/{uuid.uuid4()}/dist/index.html"},
            ],
            "IsTruncated": True,
            "NextContinuationToken": "page-2",
        },
        "page-2": {
            "Contents": [
                {"Key": f"{prefix}deployments/{uuid.uuid4()}/dist/assets/app.js"},
            ],
            "IsTruncated": False,
        },
    }
    deleted: list[str] = []

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def list_objects_v2(self, **kwargs):
            assert kwargs["Prefix"] == prefix
            return pages[kwargs.get("ContinuationToken")]

        async def delete_object(self, **kwargs):
            deleted.append(kwargs["Key"])

    b = SolutionAppBuilder()

    @asynccontextmanager
    async def _client():
        yield _FakeClient()

    monkeypatch.setattr(b, "_client", _client)

    await b.delete_all_app_artifacts(app_id)

    assert deleted == [
        pages[None]["Contents"][0]["Key"],
        pages[None]["Contents"][1]["Key"],
        pages["page-2"]["Contents"][0]["Key"],
    ]
