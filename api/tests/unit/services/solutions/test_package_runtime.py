"""Mixed immutable package publication against real database/object storage."""

import io
import zipfile
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from bifrost.workspace_release import canonical_digest
from bifrost.root_file_bindings import RootFileBinding
from src.models.contracts.solution_deployments import SolutionDeploymentCreate
from src.models.orm.applications import Application
from src.models.orm.solutions import Solution
from src.models.orm.solution_deployments import SolutionDeployment
from src.models.orm.tables import Document, Table
from src.services.solutions.app_build import SolutionAppBuilder
from src.services.solutions.deploy import SolutionDeployer
from src.services.solutions.deploy import SolutionDeployConflict
from src.services.solutions.deployment_api import SolutionDeploymentAPIService
from src.services.solutions.deployment_storage import SolutionDeploymentStorage
from src.services.solutions.deployment_manifest import (
    RuntimeResourceResolution, SharedRootTableBinding, canonical_json, sha256_digest,
)
from src.services.solutions.resource_delivery import read_deployment_resources
from src.services.solutions.source_revision import SolutionSourceRevisionError
from src.services.solutions.shared_table_bindings import SharedTableBindingError, table_metadata_hash
from src.services.solutions.root_file_bindings import RootFileBindingError
from src.services.solutions.package_controls import capture_package_controls
from src.services.solutions.immutable_export import read_active_source, write_active_export
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
@pytest.mark.parametrize("include_app", [False, True])
@pytest.mark.parametrize("backup", [False, True])
async def test_complete_package_export_preserves_authored_bytes_and_encrypted_backup(
    db_session, seed_user, compile_app, monkeypatch, tmp_path, include_app, backup,
):
    from src.services.solutions.capture import SolutionCaptureService
    from src.services.solutions.secrets_blob import decode_secrets_blob

    def reject_mutable_capture(*args, **kwargs):
        pytest.fail("Complete package export must not reconstruct authored source from mutable captures")

    async def config_values(*args):
        return {"API_KEY": "runtime-secret"}

    monkeypatch.setattr(SolutionCaptureService, "bundle_for", reject_mutable_capture)
    monkeypatch.setattr(SolutionCaptureService, "_config_values", config_values)
    solution = Solution(id=uuid4(), slug=f"export-{uuid4().hex[:8]}", name="Export", organization_id=None)
    db_session.add(solution)
    await db_session.flush()
    source = _reviewed_package_source(solution, runtime=True, include_app=include_app,
        additional_files={"README.md": b"", "assets/opaque.bin": b"\x00\xff\x02"})
    prepared = await SolutionDeployer(db_session).prepare_reviewed_package(source,
        expected_active_deployment_id=None,
        expected_controls_digest=canonical_digest(await capture_package_controls(db_session, solution.id)))
    manifest, resolution = await compile_package_runtime(db_session, source, prepared, uuid4())
    await stage(db_session, seed_user, solution, source, prepared, manifest, resolution)
    await activate_package_runtime(db_session, source, prepared, manifest, None)
    table_id = next(iter(manifest.tables.values())).resolved_id
    row_data = {"retained": "document-data"}
    db_session.add(Document(id="export-row", table_id=table_id, data=row_data))
    await db_session.flush()
    python, resources = await read_active_source(db_session, solution)
    assert python == {path: raw.decode() for path, raw in source.authored.files.items() if path.endswith(".py")}
    assert resources == {}
    destination = tmp_path / "export.zip"
    await write_active_export(db_session, solution, destination,
        include_values=backup, include_data=backup, include_files=False, password="backup-password" if backup else None)
    with zipfile.ZipFile(destination) as archive:
        expected = dict(source.authored.files)
        if backup:
            content = decode_secrets_blob(archive.read(".bifrost/secrets.enc").decode(), password="backup-password")
            assert content.config_values == {"API_KEY": "runtime-secret"}
            assert content.table_data == {"evidence": [row_data]}
            assert b"runtime-secret" not in archive.read(".bifrost/secrets.enc")
            actual = {path: archive.read(path) for path in archive.namelist() if path != ".bifrost/secrets.enc"}
        else:
            actual = {path: archive.read(path) for path in archive.namelist()}
        assert actual == expected
        assert len(archive.namelist()) == len(set(archive.namelist()))
    assert solution.active_deployment_id == manifest.deployment_id
    document = await db_session.scalar(select(Document).where(Document.table_id == table_id))
    assert document is not None and document.data == row_data


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


@pytest.mark.e2e
async def test_package_retains_named_immutable_resources_on_update_revert_and_failed_activation(
    db_session, seed_user, tmp_path,
):
    solution = Solution(id=uuid4(), slug=f"resources-{uuid4().hex[:8]}", name="Resources", organization_id=None)
    db_session.add(solution)
    await db_session.flush()
    sid = solution.id
    path = "config/policy.json"
    previous = None
    for raw in (b'{"v":1}', b'{"v":2}', b'{"v":1}'):
        source = _reviewed_package_source(solution, runtime=True, include_app=False,
            immutable_resource=raw, reads_resource=previous is not None)
        prepared = await SolutionDeployer(db_session).prepare_reviewed_package(
            source, expected_active_deployment_id=previous,
            expected_controls_digest=canonical_digest(await capture_package_controls(db_session, sid)))
        did = uuid4()
        manifest, resolution = await compile_package_runtime(db_session, source, prepared, did)
        if previous is None:
            # An earlier reviewed resource-aware adoption grants this exact
            # name. Whole-package source may update its bytes, not add names.
            resources = {path: RuntimeResourceResolution(
                object_key=SolutionDeploymentStorage(sid, did).runtime_prefix + "_resources/" + path,
                content_hash=sha256_digest(raw), size_bytes=len(raw))}
            resolution = resolution.model_copy(update={"resources": resources,
                "sources": {name: value for name, value in resolution.sources.items() if name != path}})
            manifest = manifest.model_copy(update={"resources": resources,
                "resolution_map_hash": sha256_digest(canonical_json(resolution))})
        assert set(manifest.resources) == set(resolution.resources) == {path}
        assert path not in resolution.sources
        assert resolution.resources[path].content_hash == sha256_digest(raw)
        await stage(db_session, seed_user, solution, source, prepared, manifest, resolution)
        await activate_package_runtime(db_session, source, prepared, manifest, previous)
        assert await read_deployment_resources(sid, did, resolution) == {path: raw}
        result = await readback_package_runtime(db_session, sid, did,
            expected_source_sha256=source.evidence()["package"]["source_archive_sha256"],
            expected_organization_id=None)
        assert result["source_verified"] and result["runtime_verified"]
        assert len(result["workflow_runtime_pins"]) == 1
        assert result["app_runtime_pins"] == {}
        destination = tmp_path / "resources.zip"
        await write_active_export(db_session, solution, destination,
            include_values=False, include_data=False, include_files=False, password=None)
        with zipfile.ZipFile(io.BytesIO(destination.read_bytes())) as archive:
            assert {name: archive.read(name) for name in archive.namelist()} == source.authored.files
        previous = did

    before = await capture_package_controls(db_session, sid)
    collision_id = uuid4()
    with pytest.raises(ValueError, match="source object conflicts with immutable resource storage"):
        async with db_session.begin_nested():
            collision = _reviewed_package_source(solution, runtime=True, include_app=False,
                immutable_resource=b'{"v":1}', reads_resource=True,
                additional_files={"_resources/config/policy.json": b'{"v":9}'})
            attempted = await SolutionDeployer(db_session).prepare_reviewed_package(collision,
                expected_active_deployment_id=previous, expected_controls_digest=canonical_digest(before))
            await compile_package_runtime(db_session, collision, attempted, collision_id)
    assert await capture_package_controls(db_session, sid) == before
    assert await db_session.scalar(select(Solution.active_deployment_id).where(Solution.id == sid)) == previous
    assert await db_session.get(SolutionDeployment, collision_id) is None
    assert await read_deployment_resources(sid, previous, resolution) == {path: b'{"v":1}'}
    await db_session.refresh(solution)
    with pytest.raises(ValueError, match="omits an inherited immutable resource"):
        async with db_session.begin_nested():
            missing = _reviewed_package_source(solution, runtime=True, include_app=False)
            attempted = await SolutionDeployer(db_session).prepare_reviewed_package(
                missing, expected_active_deployment_id=previous, expected_controls_digest=canonical_digest(before))
            await compile_package_runtime(db_session, missing, attempted, uuid4())
    assert await capture_package_controls(db_session, sid) == before
    await db_session.refresh(solution)

    prepared = await SolutionDeployer(db_session).prepare_reviewed_package(
        source, expected_active_deployment_id=previous, expected_controls_digest=canonical_digest(before))
    candidate, candidate_resolution = await compile_package_runtime(db_session, source, prepared, uuid4())
    await stage(db_session, seed_user, solution, source, prepared, candidate, candidate_resolution)
    from src.config import get_settings
    from src.services.file_storage.s3_client import S3StorageClient
    settings = get_settings()
    async with S3StorageClient(settings).get_client() as client:
        await client.put_object(Bucket=settings.s3_bucket,
            Key=candidate_resolution.resources[path].object_key, Body=b'{"v":9}')
    with pytest.raises(ValueError, match="activation was not established"):
        await activate_package_runtime(db_session, source, prepared, candidate, previous)
    assert await db_session.scalar(select(Solution.active_deployment_id).where(Solution.id == sid)) == previous
    assert await read_deployment_resources(sid, previous, resolution) == {path: b'{"v":1}'}
    async with S3StorageClient(settings).get_client() as client:
        await client.put_object(Bucket=settings.s3_bucket,
            Key=resolution.resources[path].object_key, Body=b'{"v":9}')
    with pytest.raises(SolutionSourceRevisionError, match="Resource archive or runtime bytes differ"):
        await readback_package_runtime(db_session, sid, previous,
            expected_source_sha256=source.evidence()["package"]["source_archive_sha256"],
            expected_organization_id=None)
    assert await db_session.scalar(select(Solution.active_deployment_id).where(Solution.id == sid)) == previous


@pytest.mark.e2e
@pytest.mark.parametrize("grant_access", ["read", "read-write"])
async def test_workflow_package_schema_successor_and_revert_preserve_documents_and_controls(
    db_session, seed_user, monkeypatch, grant_access,
):
    def unexpected_app_build(*args, **kwargs):
        pytest.fail("A workflow-only package must not compile or publish an App")

    monkeypatch.setattr(SolutionAppBuilder, "compile_dist", unexpected_app_build)
    solution = Solution(id=uuid4(), slug=f"package-{uuid4().hex[:8]}", name="Package", organization_id=None)
    db_session.add(solution)
    await db_session.flush()
    original = {"columns": [{"name": "canonical_key", "type": "string"}]}
    expanded = {"columns": [*original["columns"], {"name": "native_backup", "type": "object"}]}
    controls = None
    table_id = None
    previous = None
    data = {"canonical_key": "example", "native_backup": {"retained": True}}
    root_table = Table(id=uuid4(), name=f"shared_{uuid4().hex[:8]}", organization_id=None,
        schema={"columns": [{"name": "key", "type": "string"}]})
    db_session.add(root_table)
    await db_session.flush()
    shared = {root_table.name: SharedRootTableBinding(table_id=root_table.id,
        metadata_hash=table_metadata_hash(root_table), access=grant_access)}
    root_data = {"key": "root-retained"}
    db_session.add(Document(id="root-retained-row", table_id=root_table.id, data=root_data))
    await db_session.flush()
    raw = b'{"reviewed": true}'
    root_file = RootFileBinding(location="workspace", path=f"features/package-{uuid4().hex}.json",
        operations=("read",), max_bytes=1024, expected_read_sha256=sha256_digest(raw))
    bindings = {"reviewed_asset": root_file}
    from shared.file_paths import resolve_s3_key
    from src.config import get_settings
    from src.services.file_storage.s3_client import S3StorageClient
    settings = get_settings()
    key = resolve_s3_key(root_file.location, "global", root_file.path)
    async with S3StorageClient(settings).get_client() as client:
        await client.put_object(Bucket=settings.s3_bucket, Key=key, Body=raw)
    for schema in (original, expanded, original):
        source = _reviewed_package_source(solution, runtime=True, include_app=False, table_schema=schema)
        prepared = await SolutionDeployer(db_session).prepare_reviewed_package(
            source, expected_active_deployment_id=previous,
            expected_controls_digest=canonical_digest(await capture_package_controls(db_session, solution.id)),
        )
        manifest, resolution = await compile_package_runtime(db_session, source, prepared, uuid4())
        if previous is None:
            # Seed the retained reviewed baseline as an earlier adoption would;
            # complete-package Source cannot introduce Root grants itself.
            resolution = resolution.model_copy(update={"shared_tables": shared, "root_file_bindings": bindings})
            manifest = manifest.model_copy(update={"shared_tables": shared, "root_file_bindings": bindings,
                "resolution_map_hash": sha256_digest(canonical_json(resolution))})
        assert manifest.shared_tables == resolution.shared_tables == shared
        assert manifest.root_file_bindings == resolution.root_file_bindings == bindings
        await stage(db_session, seed_user, solution, source, prepared, manifest, resolution)
        await activate_package_runtime(db_session, source, prepared, manifest, previous)
        result = await readback_package_runtime(db_session, solution.id, manifest.deployment_id,
            expected_source_sha256=source.evidence()["package"]["source_archive_sha256"],
            expected_organization_id=None)
        assert result["source_verified"] and result["registrations_verified"] and result["runtime_verified"]
        assert len(result["workflow_runtime_pins"]) == 1
        assert result["app_runtime_pins"] == {}
        assert manifest.applications == {}
        current_controls = await capture_package_controls(db_session, solution.id)
        if controls is None:
            table_id = next(iter(current_controls["tables"]))
            db_session.add(Document(id="retained-row", table_id=UUID(table_id), data=data))
            await db_session.flush()
        for value in current_controls["tables"].values():
            observed_schema = value.pop("schema")
            assert observed_schema == schema
        if controls is None:
            controls = current_controls
        else:
            assert current_controls == controls
        table = await db_session.scalar(select(Table).where(Table.solution_id == solution.id))
        assert table is not None and str(table.id) == table_id
        assert table.schema == schema
        document = await db_session.scalar(select(Document).where(Document.table_id == table.id))
        assert document is not None and document.data == data
        previous = manifest.deployment_id

    # Preparation and compilation share the publisher's SQL transaction. A new
    # owned table must not shadow an inherited Root name under either grant.
    sid = solution.id
    root_id = root_table.id
    before = await capture_package_controls(db_session, sid)
    collision_id = uuid4()
    with pytest.raises(ValueError, match="shared table binding conflicts with an owned table"):
        async with db_session.begin_nested():
            collision = _reviewed_package_source(solution, runtime=True, include_app=False,
                table_schema=original, additional_table_name=root_table.name)
            attempted = await SolutionDeployer(db_session).prepare_reviewed_package(
                collision, expected_active_deployment_id=previous,
                expected_controls_digest=canonical_digest(before),
            )
            await compile_package_runtime(db_session, collision, attempted, collision_id)
    assert await capture_package_controls(db_session, sid) == before
    assert await db_session.scalar(select(Solution.active_deployment_id).where(Solution.id == sid)) == previous
    assert await db_session.get(SolutionDeployment, collision_id) is None
    root_document = await db_session.scalar(select(Document).where(Document.table_id == root_id))
    owned_document = await db_session.scalar(select(Document).where(Document.table_id == UUID(table_id)))
    assert root_document is not None and root_document.data == root_data
    assert owned_document is not None and owned_document.data == data
    await db_session.refresh(solution)
    await db_session.refresh(root_table)
    assert table_metadata_hash(root_table) == shared[root_table.name].metadata_hash

    # Readback seals metadata as well as Python bytes. An out-of-band schema
    # change cannot be accepted as the published package.
    await db_session.execute(update(Table).where(Table.solution_id == solution.id).values(schema=expanded))
    with pytest.raises(ValueError, match="controls/resources"):
        await readback_package_runtime(db_session, solution.id, previous,
            expected_source_sha256=source.evidence()["package"]["source_archive_sha256"],
            expected_organization_id=None)
    await db_session.execute(update(Table).where(Table.solution_id == solution.id).values(schema=original))
    await db_session.execute(update(Table).where(Table.id == root_table.id).values(schema={"drift": True}))
    with pytest.raises(SharedTableBindingError, match="metadata"):
        await readback_package_runtime(db_session, solution.id, previous,
            expected_source_sha256=source.evidence()["package"]["source_archive_sha256"],
            expected_organization_id=None)
    await db_session.execute(update(Table).where(Table.id == root_table.id).values(
        schema={"columns": [{"name": "key", "type": "string"}]}))
    async with S3StorageClient(settings).get_client() as client:
        await client.put_object(Bucket=settings.s3_bucket, Key=key, Body=b'{"drift": true}')
    with pytest.raises(RootFileBindingError, match="hash"):
        await readback_package_runtime(db_session, solution.id, previous,
            expected_source_sha256=source.evidence()["package"]["source_archive_sha256"],
            expected_organization_id=None)
