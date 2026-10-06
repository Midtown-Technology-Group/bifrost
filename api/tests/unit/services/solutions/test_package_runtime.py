"""Mixed immutable package publication against real database/object storage."""

from uuid import uuid4

import pytest
from sqlalchemy import select, update

from bifrost.workspace_release import canonical_digest
from src.models.contracts.solution_deployments import SolutionDeploymentCreate
from src.models.orm.applications import Application
from src.models.orm.solutions import Solution
from src.services.solutions.app_build import SolutionAppBuilder
from src.services.solutions.deploy import SolutionDeployer
from src.services.solutions.deploy import SolutionDeployConflict
from src.services.solutions.deployment_api import SolutionDeploymentAPIService
from src.services.solutions.deployment_storage import SolutionDeploymentStorage
from src.services.solutions.package_controls import capture_package_controls
from src.services.solutions.package_runtime import (
    activate_package_runtime,
    compile_package_runtime,
    readback_package_runtime,
    stage_package_runtime,
)
from tests.unit.test_solution_app_deploy import _reviewed_package_source


@pytest.fixture
def compile_app(monkeypatch):
    # Only the external Node compiler is substituted. Source, all immutable
    # outputs, registrations, state transitions and pointer CAS use real stores.
    monkeypatch.setattr(SolutionAppBuilder, "compile_dist", lambda *args, **kwargs: {
        "index.html": b'<script type="module" src="/assets/main.js"></script>',
        "assets/main.js": b"export const version = 1;",
    })


async def prepare(db, solution):
    source = _reviewed_package_source(solution, runtime=True)
    prepared = await SolutionDeployer(db).prepare_reviewed_package(
        source, expected_active_deployment_id=solution.active_deployment_id,
        expected_controls_digest=canonical_digest(await capture_package_controls(db, solution.id)),
    )
    did = uuid4()
    manifest, resolution = await compile_package_runtime(db, source, prepared, did)
    return source, prepared, manifest, resolution


async def stage(db, user, solution, source, prepared, manifest, resolution):
    await stage_package_runtime(source, prepared, manifest)
    await SolutionDeploymentAPIService(db).create_ready_draft(solution.id, user.id,
        SolutionDeploymentCreate(compiled_manifest=manifest, resolution_map=resolution,
            base_deployment_id=solution.active_deployment_id))


@pytest.mark.e2e
@pytest.mark.parametrize("successor", [False, True])
async def test_complete_package_publishes_workflow_resources_app_and_full_source(
    db_session, seed_user, compile_app, successor,
):
    solution = Solution(id=uuid4(), slug=f"package-{uuid4().hex[:8]}", name="Package", organization_id=None)
    db_session.add(solution)
    await db_session.flush()
    previous = None
    for _ in range(2 if successor else 1):
        source, prepared, manifest, resolution = await prepare(db_session, solution)
        await stage(db_session, seed_user, solution, source, prepared, manifest, resolution)
        await activate_package_runtime(db_session, source, prepared, manifest, previous)
        result = await readback_package_runtime(db_session, solution.id, manifest.deployment_id,
            expected_source_sha256=source.evidence()["package"]["source_archive_sha256"],
            expected_organization_id=None)
        assert result["source_verified"] and result["registrations_verified"] and result["runtime_verified"]
        assert len(result["workflow_runtime_pins"]) == len(result["app_runtime_pins"]) == 1
        assert result["entity_id_map"]["apps"] == {
            str(original["id"]): str(resolved["id"])
            for original, resolved in zip(prepared.source_bundle.apps, prepared.bundle.apps, strict=True)
        }
        assert result["entity_id_map"]["workflows"] == {
            str(original["id"]): str(resolved["id"])
            for original, resolved in zip(prepared.source_bundle.workflows, prepared.bundle.workflows, strict=True)
        }
        assert len(manifest.tables) == len(manifest.file_locations) == 1
        assert set(resolution.sources) == set(source.authored.files)
        assert await SolutionDeploymentStorage(solution.id, manifest.deployment_id).read_runtime_file(
            "modules/__init__.py", max_bytes=0) == b""
        assert await SolutionDeploymentStorage(solution.id, manifest.deployment_id).read_runtime_file(
            "apps/example/public/icon.bin") == b"\x00\xff\x01"
        previous = manifest.deployment_id


@pytest.mark.e2e
async def test_failed_app_cas_rolls_back_solution_pointer_and_original_app_pointer(
    db_session, seed_user, compile_app,
):
    solution = Solution(id=uuid4(), slug=f"package-{uuid4().hex[:8]}", name="Package", organization_id=None)
    db_session.add(solution)
    await db_session.flush()
    sid = solution.id
    source, prepared, manifest, resolution = await prepare(db_session, solution)
    await stage(db_session, seed_user, solution, source, prepared, manifest, resolution)
    app = await db_session.get(Application, prepared.compiled_apps[0].app_id)
    assert app is not None
    changed = uuid4()
    # Simulate a competing deploy CAS through the same Core write surface.
    # Direct ORM mutation is rightly rejected by the managed-entity backstop.
    await db_session.execute(update(Application).where(Application.id == app.id)
                             .values(active_deployment_id=changed))
    with pytest.raises(ValueError, match="activation was not established"):
        await activate_package_runtime(db_session, source, prepared, manifest, None)
    assert await db_session.scalar(select(Solution.active_deployment_id).where(Solution.id == sid)) is None
    assert await db_session.scalar(select(Application.active_deployment_id).where(
        Application.id == prepared.compiled_apps[0].app_id)) == changed


@pytest.mark.e2e
async def test_package_readback_detects_authored_asset_tampering(db_session, seed_user, compile_app):
    solution = Solution(id=uuid4(), slug=f"package-{uuid4().hex[:8]}", name="Package", organization_id=None)
    db_session.add(solution)
    await db_session.flush()
    source, prepared, manifest, resolution = await prepare(db_session, solution)
    await stage(db_session, seed_user, solution, source, prepared, manifest, resolution)
    await activate_package_runtime(db_session, source, prepared, manifest, None)
    storage = SolutionDeploymentStorage(solution.id, manifest.deployment_id)
    from src.config import get_settings
    from src.services.file_storage.s3_client import S3StorageClient
    settings = get_settings()
    async with S3StorageClient(settings).get_client() as client:
        await client.put_object(Bucket=settings.s3_bucket,
            Key=storage.runtime_prefix + "apps/example/public/icon.bin", Body=b"bad")
    with pytest.raises(ValueError, match="runtime bytes differ"):
        await readback_package_runtime(db_session, solution.id, manifest.deployment_id,
            expected_source_sha256=source.evidence()["package"]["source_archive_sha256"],
            expected_organization_id=None)


@pytest.mark.e2e
async def test_package_successor_cannot_add_a_required_parameter_to_existing_callers(db_session, compile_app):
    solution = Solution(id=uuid4(), slug=f"package-{uuid4().hex[:8]}", name="Package", organization_id=None)
    db_session.add(solution)
    await db_session.flush()
    await prepare(db_session, solution)
    controls = await capture_package_controls(db_session, solution.id)
    with pytest.raises(SolutionDeployConflict, match="Breaking parameter changes"):
        await SolutionDeployer(db_session).prepare_reviewed_package(
            _reviewed_package_source(solution, runtime=True, function_args="tenant_id: str"),
            expected_active_deployment_id=None, expected_controls_digest=canonical_digest(controls))
