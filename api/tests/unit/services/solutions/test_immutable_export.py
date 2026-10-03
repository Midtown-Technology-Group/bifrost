"""An exported Solution carries the active runtime closure, including relative imports."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.solutions.deployment_manifest import (
    CompiledDeploymentManifest,
    DeploymentResolutionMap,
    DeploymentSource,
    RuntimeEntityDefinition,
    RuntimeResourceResolution,
    RuntimeSourceResolution,
    canonical_json,
    sha256_digest,
)
from src.services.solutions.deployment_storage import SolutionDeploymentStorage
from src.services.solutions.immutable_export import (
    read_active_source,
    require_export_registration,
)


@pytest.fixture
def closure(monkeypatch):
    sid, did = uuid4(), uuid4()
    solution = Solution(
        id=sid,
        organization_id=None,
        active_deployment_id=did,
        execution_runtime_mode="deployment-v1",
    )
    prefix = f"_solutions/{sid}/{did}/"
    source_key = f"_solution_artifacts/{sid}/{did}/source.zip"
    # One workflow and a package whose relative imports Root's scanner omitted.
    files = {
        "workflows/main.py": b"from modules.package import read\n",
        "modules/package/__init__.py": b"from .helper import read\n",
        "modules/package/helper.py": b"def read():\n    return 'immutable'\n",
    }
    assets = {"assets/schema.json": b'{"reviewed":true}'}
    resources = {
        p: RuntimeResourceResolution(
            object_key=prefix + "_resources/" + p,
            content_hash=sha256_digest(b),
            size_bytes=len(b),
        )
        for p, b in assets.items()
    }
    resolution = DeploymentResolutionMap(
        sources={
            p: RuntimeSourceResolution(
                object_key=prefix + p, content_hash=sha256_digest(b)
            )
            for p, b in files.items()
        },
        resources=resources,
    )
    manifest = CompiledDeploymentManifest(
        solution_id=sid,
        deployment_id=did,
        bundle_hash="sha256:bundle",
        resolution_map_hash=sha256_digest(canonical_json(resolution)),
        source=DeploymentSource(artifact_key=source_key, runtime_prefix=prefix),
        resources=resources,
    )
    row = SimpleNamespace(
        solution_id=sid,
        organization_id=None,
        state="active",
        dependencies=[],
        compiled_manifest=manifest,
        resolution_map=resolution,
        compiled_manifest_hash=manifest.content_hash(),
        resolution_map_hash=manifest.resolution_map_hash,
        bundle_hash=manifest.bundle_hash,
        source_artifact_key=source_key,
        runtime_storage_prefix=prefix,
    )
    lookup = AsyncMock(return_value=row)
    monkeypatch.setattr(SolutionDeploymentRepository, "get_runtime_closure", lookup)
    monkeypatch.setattr(
        SolutionDeploymentStorage,
        "read_compiled_manifest",
        AsyncMock(return_value=manifest.canonical_bytes()),
    )
    read = AsyncMock(side_effect=lambda path, **_: files[path])
    monkeypatch.setattr(SolutionDeploymentStorage, "read_runtime_file", read)
    monkeypatch.setattr(
        SolutionDeploymentStorage,
        "read_resource",
        AsyncMock(side_effect=lambda path, _: assets[path]),
    )
    return solution, row, files, assets, lookup, read


async def test_reads_complete_active_source_and_reviewed_assets(closure):
    solution, _, files, assets, lookup, read = closure
    source, resources = await read_active_source(AsyncMock(), solution)
    assert source == {p: b.decode() for p, b in files.items()}
    assert resources == assets
    assert read.await_count == 3
    assert lookup.await_args.args[1:] == (None, solution.id)


@pytest.mark.parametrize(
    "change",
    [
        "missing_pointer",
        "wrong_mode",
        "wrong_owner",
        "wrong_scope",
        "superseded",
        "manifest_hash",
        "resolution_hash",
        "source_prefix",
    ],
)
async def test_refuses_incoherent_active_identity(closure, change):
    solution, row, *_ = closure
    if change == "missing_pointer":
        solution.active_deployment_id = None
    elif change == "wrong_mode":
        solution.execution_runtime_mode = "repo-v1"
    elif change == "wrong_owner":
        row.solution_id = uuid4()
    elif change == "wrong_scope":
        row.organization_id = uuid4()
    elif change == "superseded":
        row.state = "superseded"
    elif change == "manifest_hash":
        row.compiled_manifest_hash = "sha256:tampered"
    elif change == "resolution_hash":
        row.resolution_map_hash = "sha256:tampered"
    else:
        row.runtime_storage_prefix = "_repo/"
    with pytest.raises(ValueError):
        await read_active_source(AsyncMock(), solution)


async def test_missing_active_row_is_not_root_fallback(closure):
    solution, _, _, _, lookup, read = closure
    lookup.return_value = None
    with pytest.raises(ValueError, match="missing"):
        await read_active_source(AsyncMock(), solution)
    read.assert_not_awaited()


@pytest.mark.parametrize("target", ["manifest", "source", "resource", "missing_source"])
async def test_refuses_storage_tampering_or_missing_source(
    closure, monkeypatch, target
):
    solution, *_ = closure
    method = {
        "manifest": "read_compiled_manifest",
        "source": "read_runtime_file",
        "resource": "read_resource",
        "missing_source": "read_runtime_file",
    }[target]
    replacement = AsyncMock(return_value=b"tampered")
    if target == "missing_source":
        replacement.side_effect = FileNotFoundError("missing")
    monkeypatch.setattr(SolutionDeploymentStorage, method, replacement)
    with pytest.raises((ValueError, FileNotFoundError)):
        await read_active_source(AsyncMock(), solution)


async def test_combined_source_and_resource_size_is_bounded(closure, monkeypatch):
    from src.services.solutions import immutable_export

    solution, _, files, _assets, _, _ = closure
    monkeypatch.setattr(
        immutable_export, "MAX_ARCHIVE_BYTES", sum(map(len, files.values()))
    )
    with pytest.raises(ValueError, match="combined.*limit"):
        await read_active_source(AsyncMock(), solution)


async def test_declared_dependencies_are_not_silently_dropped(closure, monkeypatch):
    from src.services.solutions import immutable_export

    solution, row, _, _, _, read = closure
    manifest = row.compiled_manifest.model_copy(
        update={"dependencies": {"peer": object()}}
    )
    monkeypatch.setattr(
        immutable_export,
        "validate_runtime_closure",
        lambda *a, **k: (manifest, row.resolution_map),
    )
    with pytest.raises(ValueError, match="dependencies"):
        await read_active_source(AsyncMock(), solution)
    read.assert_not_awaited()


@pytest.mark.parametrize(
    "change", [None, "extra", "missing", "inactive", "scope", "path", "endpoint"]
)
async def test_export_registration_requires_exact_owned_set_and_controls(
    closure, change
):
    solution, *_ = closure
    wid = uuid4()
    workflow = Workflow(
        id=wid,
        solution_id=solution.id,
        organization_id=None,
        path="workflows/main.py",
        function_name="run",
        name="Immutable run",
        type="workflow",
        is_active=True,
        timeout_seconds=20,
        execution_mode="async",
        time_saved=0,
        value=0,
        cache_ttl_seconds=0,
        endpoint_enabled=False,
        allowed_methods=[],
        roles=[],
    )
    definition = {
        "path": workflow.path,
        "function_name": "run",
        "name": "Immutable run",
        "type": "workflow",
        "organization_id": None,
        "timeout_seconds": 20,
        "execution_mode": "async",
        "time_saved": 0,
        "value": 0.0,
        "cache_ttl_seconds": 0,
        "endpoint_enabled": False,
    }
    entity = RuntimeEntityDefinition(
        portable_ref="run", resolved_id=wid, definition=definition
    )
    resolution = DeploymentResolutionMap(workflows={"run": entity})
    rows = [workflow]
    if change == "extra":
        rows.append(Workflow(id=uuid4()))
    elif change == "missing":
        rows.clear()
    elif change == "inactive":
        workflow.is_active = False
    elif change == "scope":
        workflow.organization_id = uuid4()
    elif change == "path":
        workflow.path = "workflows/stale.py"
    elif change == "endpoint":
        workflow.endpoint_enabled = True
    db = AsyncMock()
    db.scalars.return_value = SimpleNamespace(all=lambda: rows)
    if change is None:
        await require_export_registration(db, solution, resolution)
    else:
        with pytest.raises(ValueError):
            await require_export_registration(db, solution, resolution)
