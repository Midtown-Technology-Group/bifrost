"""Public export reads the sealed closure and refuses stale registration state."""

import io
import zipfile
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update

from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.services.repo_storage import RepoStorage
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
from src.services.solutions.live_handoff_source import source_archive
from src.services.solutions.source_revision import _workflow_snapshot

pytestmark = pytest.mark.e2e


async def test_export_keeps_complete_sealed_imports_and_assets(
    e2e_client, platform_admin, db_session
):
    token = uuid4().hex
    response = e2e_client.post(
        "/api/solutions",
        headers=platform_admin.headers,
        json={
            "slug": "sealed-export-" + token,
            "name": "Sealed export",
            "organization_id": None,
        },
    )
    assert response.status_code in (200, 201), response.text
    sid, did, wid = UUID(response.json()["id"]), uuid4(), uuid4()
    solution = await db_session.get(Solution, sid)
    path = f"workflows/export_{token}.py"
    package = f"modules/export_{token}"
    files = {
        path: f"from modules.export_{token} import read\nasync def run():\n    return read()\n".encode(),
        package + "/__init__.py": b"from .helper import read\n",
        package + "/helper.py": b'def read():\n    return "sealed"\n',
    }
    workflow = Workflow(
        id=wid,
        solution_id=sid,
        organization_id=None,
        path=path,
        function_name="run",
        name="Sealed export " + token,
        type="workflow",
        is_active=True,
        timeout_seconds=20,
        execution_mode="async",
        roles=[],
    )
    db_session.add(workflow)
    await db_session.flush()
    storage = SolutionDeploymentStorage(sid, did)
    entity = RuntimeEntityDefinition(
        portable_ref=path + "::run",
        resolved_id=wid,
        source_ref=path,
        source_hash=sha256_digest(files[path]),
        definition=_workflow_snapshot(workflow),
    )
    asset = b'{"sealed":true}'
    resource = RuntimeResourceResolution(
        object_key=storage.runtime_prefix + "_resources/assets/schema.json",
        content_hash=sha256_digest(asset),
        size_bytes=len(asset),
    )
    resolution = DeploymentResolutionMap(
        workflows={entity.portable_ref: entity},
        resources={"assets/schema.json": resource},
        sources={
            p: RuntimeSourceResolution(
                object_key=storage.runtime_prefix + p, content_hash=sha256_digest(b)
            )
            for p, b in files.items()
        },
    )
    manifest = CompiledDeploymentManifest(
        solution_id=sid,
        deployment_id=did,
        bundle_hash=sha256_digest(b"export-fixture"),
        resolution_map_hash=sha256_digest(canonical_json(resolution)),
        source=DeploymentSource(
            artifact_key=storage.source_artifact_key,
            runtime_prefix=storage.runtime_prefix,
        ),
        workflows=resolution.workflows,
        resources=resolution.resources,
    )
    await storage.write_source_artifact(source_archive(files))
    await storage.write_resources_artifact(source_archive({"assets/schema.json": asset}))
    await storage.write_compiled_manifest(manifest.canonical_bytes())
    for p, content in files.items():
        await storage.write_runtime_file(p, content)
    await storage.write_runtime_file("_resources/assets/schema.json", asset)
    from src.models.contracts.solution_deployments import SolutionDeploymentCreate
    from src.repositories.solution_deployments import SolutionDeploymentRepository
    from src.services.solutions.deployment_api import SolutionDeploymentAPIService

    # Exercise the real draft/state guards rather than inserting an impossible
    # active row. Export then reads this independently committed sealed runtime.
    await SolutionDeploymentAPIService(db_session).create_ready_draft(
        sid, platform_admin.user_id,
        SolutionDeploymentCreate(compiled_manifest=manifest, resolution_map=resolution),
    )
    repository = SolutionDeploymentRepository(db_session)
    for old, new in (("ready", "activating"), ("activating", "active")):
        await repository.transition(did, None, expected_state=old, new_state=new)
    solution.active_deployment_id = did
    solution.execution_runtime_mode = "deployment-v1"
    await db_session.commit()
    # Mutable workspace bytes are deliberately different; they must never win.
    await RepoStorage().write(path, b'async def run():\n    return "mutable"\n')
    response = e2e_client.post(
        f"/api/solutions/{sid}/export?mode=shareable",
        headers=platform_admin.headers,
        json={},
    )
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        exported = {p: archive.read(p) for p in archive.namelist() if p.endswith(".py")}
        assert exported == files
        assert archive.read("assets/schema.json") == asset
    # Deliberately inject registry drift through the same Core projection seam
    # used by deployment. Direct ORM edits correctly trip the managed guard.
    await db_session.execute(update(Workflow).where(Workflow.id == wid).values(is_active=False))
    await db_session.commit()
    response = e2e_client.post(
        f"/api/solutions/{sid}/export?mode=shareable",
        headers=platform_admin.headers,
        json={},
    )
    assert response.status_code == 409, response.text
