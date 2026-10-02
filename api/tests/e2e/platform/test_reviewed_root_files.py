"""Signed file API requests retain Root ownership and execution scope."""

from datetime import UTC, datetime
import base64
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from bifrost.root_file_bindings import BACKUP_PREFIX, RootFileBinding
from shared.file_paths import resolve_s3_key
from src.core.constants import PROVIDER_ORG_ID
from src.core.database import get_db
from src.core.security import mint_engine_token
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution, WorkflowExecutionAttempt
from src.models.orm.file_metadata import FilePolicy
from src.routers.files import router
from src.services.file_storage import FileStorageService
from src.services.solutions.deployment_manifest import (
    canonical_json, sha256_digest,
)
from src.services.solutions.deployment_runtime import pin_workflow_runtime
from tests.e2e.platform.test_solution_source_revision import (
    _seed_adopted_revision, db_session as db_session,
)

pytestmark = pytest.mark.e2e


async def fixture(db, admin, monkeypatch, install_org, execution_org, *, package_bound=1024):
    prefix = f"features/reviewed_root_{uuid4().hex}"
    installer, package = prefix + "/installer.ps1", prefix + "/package.zip"
    catalog = prefix + "/clients.json"
    script = b"Write-Output 'synthetic installer'"
    grants = {
        "installer": RootFileBinding(location="workspace", path=installer,
            operations=["read", "exists", "signed_get"], max_bytes=len(script), expected_read_sha256=sha256_digest(script)),
        "download": RootFileBinding(location="uploads", path=package,
            operations=["exists", "signed_get"], max_bytes=package_bound),
        "backup": RootFileBinding(location="workspace", path=BACKUP_PREFIX,
            directory_prefix=True, operations=["create"], max_bytes=64 * 1024),
        "catalog": RootFileBinding(location="workspace", path=catalog,
            operations=["read"], max_bytes=1024),
    }
    f = await _seed_adopted_revision(db, admin, monkeypatch,
        organization_id=install_org, root_file_bindings=grants)
    pin = await pin_workflow_runtime(db, f.workflow_id)
    assert pin is not None
    execution_id, claim_token = uuid4(), uuid4()
    evidence = pin.queue_evidence()
    db.add(Execution(id=execution_id, workflow_id=f.workflow_id, workflow_name="Root fixture",
        executed_by_name="Synthetic", organization_id=execution_org, status=ExecutionStatus.RUNNING,
        runtime_mode="deployment-v1", solution_deployment_id=f.base_id,
        runtime_evidence=evidence, runtime_evidence_hash=sha256_digest(canonical_json(evidence))))
    await db.flush()
    attempt = WorkflowExecutionAttempt(execution_id=execution_id, attempt_number=1,
        claim_token=claim_token, status="running", phase="execution", published_at=datetime.now(UTC),
        claimed_at=datetime.now(UTC), started_at=datetime.now(UTC))
    db.add(attempt)
    policy = FilePolicy(location="uploads", path=package, organization_id=execution_org,
        policies={"policies": [{"name": "fixture", "actions": ["read"]}]})
    db.add(policy)
    await db.commit()
    storage = FileStorageService(db)
    scope = str(execution_org) if execution_org else "global"
    installer_key = resolve_s3_key("workspace", scope, installer)
    package_key = resolve_s3_key("uploads", scope, package)
    await storage.write_raw_to_s3(installer_key, script)
    await storage.write_raw_to_s3(package_key, b"synthetic package")
    await storage.write_raw_to_s3(resolve_s3_key("workspace", "global", catalog), b'{"clients":1}')
    token, _ = mint_engine_token(execution_id=str(execution_id), attempt_token=str(claim_token),
        solution_id=str(f.solution_id), organization_id=str(execution_org) if execution_org else None)
    app = FastAPI()
    app.include_router(router)

    async def current_db():
        yield db

    app.dependency_overrides[get_db] = current_db
    f.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": "Bearer " + token}, params={"solution": str(f.solution_id)})
    f.storage, f.installer, f.package, f.catalog = storage, installer, package, catalog
    f.installer_key, f.package_key, f.script = installer_key, package_key, script
    f.policy, f.attempt, f.execution_id = policy, attempt, execution_id
    return f


@pytest.mark.asyncio
async def test_large_signed_download_streams_bound_and_denies_raw_read(
    db_session, platform_admin, monkeypatch,
):
    """Exercise signed authority and the stream boundary without a large allocation."""
    bound = 135419864
    f = await fixture(db_session, platform_admin, monkeypatch, None, PROVIDER_ORG_ID,
                      package_bound=bound)
    observed = []
    retained = []
    import src.services.solutions.root_file_bindings as root_file_bindings
    original_read = root_file_bindings.read_reviewed_root_bytes

    async def read_root(*args, **kwargs):
        retained.append(kwargs.get("retain_bytes"))
        return await original_read(*args, **kwargs)

    monkeypatch.setattr(root_file_bindings, "read_reviewed_root_bytes", read_root)
    size = bound

    async def chunks(storage, key, *, chunk_size):
        assert key == f.package_key
        observed.append(chunk_size)
        remaining = size
        chunk = b"x" * chunk_size
        while remaining:
            count = min(remaining, chunk_size)
            yield chunk[:count]
            remaining -= count

    monkeypatch.setattr(FileStorageService, "iter_raw_s3_chunks", chunks)
    async with f.client:
        request = {"path": f.package, "location": "uploads", "method": "GET"}
        signed = await f.client.post("/api/files/signed-url", json=request)
        assert signed.status_code == 200, signed.text
        assert retained == [False]
        assert signed.json()["path"] == f.package_key
        assert signed.json()["expires_in"] == 600
        reads_before_denial = len(observed)
        denied_read = await f.client.post("/api/files/read", json={
            "path": f.package, "location": "uploads"})
        assert denied_read.status_code == 404  # Ordinary Solution tier cannot see this Root object.
        assert "content" not in denied_read.json()
        assert len(observed) == reads_before_denial
        assert retained == [False]
        size = bound + 1
        overflow = await f.client.post("/api/files/signed-url", json=request)
        assert overflow.status_code == 422
        assert "url" not in overflow.json()
        assert retained == [False, False]
        assert max(observed) <= 64 * 1024


@pytest.mark.asyncio
@pytest.mark.parametrize("install_org,execution_org", [
    (None, None), (None, PROVIDER_ORG_ID), (PROVIDER_ORG_ID, PROVIDER_ORG_ID),
])
async def test_signed_root_file_requests_enforce_scope_bytes_and_current_policy(
    db_session, platform_admin, monkeypatch, install_org, execution_org,
):
    f = await fixture(db_session, platform_admin, monkeypatch, install_org, execution_org)
    async with f.client:
        for value in [b'{"clients":1}', b'{"clients":2}']:
            await f.storage.write_raw_to_s3(resolve_s3_key("workspace", "global", f.catalog), value)
            catalog_response = await f.client.post("/api/files/read", json={"path": f.catalog})
            assert catalog_response.status_code == 200, catalog_response.text
            assert catalog_response.json()["content"] == value.decode()
        editor_read = f.client.build_request("GET", "/api/files/editor/content")
        editor_read.url = editor_read.url.copy_with(query=("path=" + f.catalog).encode())
        editor_response = await f.client.send(editor_read)
        assert editor_response.status_code == 200, editor_response.text
        assert editor_response.json()["content"] == '{"clients":2}'
        response = await f.client.post("/api/files/read", json={"path": f.installer})
        assert response.status_code == 200, response.text
        assert response.json()["content"] == f.script.decode()
        missing_context = f.client.build_request("POST", "/api/files/read", json={"path": f.installer})
        missing_context.url = missing_context.url.copy_with(query=b"")
        assert (await f.client.send(missing_context)).status_code == 403
        for endpoint, payload in [
            ("write", {"path": f.installer, "content": "replacement"}),
            ("delete", {"path": f.installer}), ("list", {"directory": "features"}),
            ("stat", {"path": f.installer}), ("exists", {"path": f.installer}),
            ("signed-url", {"path": f.installer, "method": "GET"}),
            ("signed-url", {"path": f.installer, "method": "PUT"}),
        ]:
            dropped = f.client.build_request("POST", "/api/files/" + endpoint, json=payload)
            dropped.url = dropped.url.copy_with(query=b"")
            assert (await f.client.send(dropped)).status_code == 403
        assert (await f.client.post("/api/files/exists", json={"path": f.installer})).json()["exists"]
        installer_download = {"path": f.installer, "location": "workspace", "method": "GET"}
        assert (await f.client.post("/api/files/signed-url", json=installer_download)).status_code == 200
        for unsafe_path in ["features/../secret.json", "features/%2e/secret.json", "/features/secret.json"]:
            denied_path = await f.client.post("/api/files/signed-url", json={
                **installer_download, "path": unsafe_path})
            assert denied_path.status_code in {400, 403}
            assert "url" not in denied_path.json()
        foreign = str(uuid4())
        denied = await f.client.post("/api/files/read", json={"path": f.installer, "scope": foreign})
        assert denied.status_code == 403
        download = {"path": f.package, "location": "uploads", "method": "GET"}
        signed = await f.client.post("/api/files/signed-url", json=download)
        assert signed.status_code == 200, signed.text
        assert signed.json()["path"] == f.package_key
        assert signed.json()["expires_in"] == 600
        assert (await f.client.post("/api/files/signed-url", json={**download, "expires_in": 601})).status_code == 422
        assert (await f.client.post("/api/files/signed-url", json={**download, "scope": foreign})).status_code == 403
        await f.storage.write_raw_to_s3(f.installer_key, b"X" * len(f.script))
        assert (await f.client.post("/api/files/signed-url", json=installer_download)).status_code == 422
        assert (await f.client.post("/api/files/read", json={"path": f.installer})).status_code == 400
        await f.storage.write_raw_to_s3(f.installer_key, f.script + b"!")
        assert (await f.client.post("/api/files/read", json={"path": f.installer})).status_code == 400
        f.policy.policies = {"policies": []}
        await db_session.commit()
        assert (await f.client.post("/api/files/signed-url", json=download)).status_code == 403
        f.attempt.status = "succeeded"
        f.attempt.phase = "terminal"
        f.attempt.completed_at = datetime.now(UTC)
        await db_session.commit()
        assert (await f.client.post("/api/files/read", json={"path": f.installer})).status_code == 409
    assert f.workflow.solution_id == f.solution_id
    assert f.solution.active_deployment_id == f.base_id
    assert f.policy.solution_id is None


@pytest.mark.asyncio
async def test_root_backup_forces_create_only_and_never_grants_read_or_delete(
    db_session, platform_admin, monkeypatch,
):
    f = await fixture(db_session, platform_admin, monkeypatch, None, PROVIDER_ORG_ID)
    backup_path = f"{BACKUP_PREFIX}/20261001T010000Z-{uuid4().hex[:12]}.json"
    async with f.client:
        request = {"path": backup_path, "content": '{"synthetic":true}'}
        first = await f.client.post("/api/files/write", json=request)
        assert first.status_code == 204, first.text
        assert (await f.client.post("/api/files/write", json=request)).status_code == 409
        assert await f.storage.read_uploaded_file(resolve_s3_key("workspace", "global", backup_path)) == b'{"synthetic":true}'
        for endpoint, payload in [
            ("read", {"path": backup_path}), ("delete", {"path": backup_path}),
            ("signed-url", {"path": backup_path, "location": "workspace", "method": "GET"}),
            ("write", {"path": backup_path + ".other", "content": "{}"}),
            ("write", {"path": f.installer + ".py", "content": "print(1)"}),
            ("read", {"path": ".bifrost/credentials.json"}),
        ]:
            assert (await f.client.post("/api/files/" + endpoint, json=payload)).status_code in {400, 403}
        replacement = {**request, "expected_version": "known"}
        assert (await f.client.post("/api/files/write", json=replacement)).status_code == 422
        huge = {"path": f"{BACKUP_PREFIX}/20261001T010001Z-{uuid4().hex[:12]}.json",
            "content": '{"x":"' + "X" * 65536 + '"}'}
        assert (await f.client.post("/api/files/write", json=huge)).status_code == 400
    assert f.solution.active_deployment_id == f.base_id


@pytest.mark.asyncio
async def test_revision_rechecks_required_root_assets_and_keeps_accepted_old_pin(
    db_session, platform_admin, monkeypatch,
):
    from sqlalchemy import func, select
    from src.models.contracts.solution_deployments import (
        SolutionSourceFile, SolutionSourceRevisionCommitRequest,
        SolutionSourceRevisionInspectRequest, SolutionSourceRevisionRequest,
    )
    from src.services.solutions.source_revision import (
        SolutionSourceRevisionError, SolutionSourceRevisionService,
    )

    f = await fixture(db_session, platform_admin, monkeypatch, PROVIDER_ORG_ID, PROVIDER_ORG_ID)
    service = SolutionSourceRevisionService(db_session)
    staged = await service.stage(f.solution_id, f.revision_id, platform_admin.user_id,
        SolutionSourceRevisionRequest(expected_active_deployment_id=f.base_id,
            expected_active_manifest_hash=f.manifest.content_hash(), source_commit_sha="f" * 40,
            files=[SolutionSourceFile(path=f.path,
                content_base64=base64.b64encode(f.new_source).decode())]))
    await db_session.commit()
    expected = SolutionSourceRevisionInspectRequest(expected_active_deployment_id=f.base_id,
        expected_active_manifest_hash=f.manifest.content_hash())
    await f.storage.delete_raw_from_s3(f.installer_key)
    with pytest.raises(SolutionSourceRevisionError, match="asset is missing"):
        await service.inspect(f.solution_id, f.revision_id, expected)
    await f.storage.write_raw_to_s3(f.installer_key, b"X" * len(f.script))
    commit = SolutionSourceRevisionCommitRequest(**expected.model_dump(), expected_evidence_id=staged.evidence_id)
    with pytest.raises(SolutionSourceRevisionError, match="reviewed hash"):
        await service.activate(f.solution_id, f.revision_id, commit)
    assert f.solution.active_deployment_id == f.base_id
    await f.storage.write_raw_to_s3(f.installer_key, f.script)
    await service.activate(f.solution_id, f.revision_id, commit)
    await db_session.commit()
    assert f.solution.active_deployment_id == f.revision_id
    async with f.client:
        # This HTTP token belongs to the accepted predecessor execution.
        response = await f.client.post("/api/files/read", json={"path": f.installer})
        assert response.status_code == 200, response.text
        assert response.json()["content"] == f.script.decode()
    assert await db_session.scalar(select(func.count()).select_from(Execution).where(
        Execution.workflow_id == f.workflow_id)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("organization_id", [None, PROVIDER_ORG_ID])
async def test_initial_root_file_install_executes_through_real_worker(
    e2e_client, platform_admin, db_session, organization_id,
):
    """Prove SDK context propagation through HTTP, durable queue and worker."""
    from sqlalchemy import select
    from src.models.orm.file_metadata import FileMetadata
    from src.models.orm.solutions import Solution
    from tests.e2e.conftest import execute_workflow_sync
    from tests.e2e.platform.test_initial_workflow_install import _recipe

    token = uuid4().hex
    path = f"solutions/root_worker_{token}.py"
    installer = f"features/root_worker_{token}/installer.ps1"
    package = f"root_worker_{token}/package.zip"
    backup = f"{BACKUP_PREFIX}/20261001T010000Z-{token}.json"
    script = b"Write-Output 'worker proof'"
    # An explicit run of a global registration inherits this caller's Org2
    # execution scope. It must not substitute the global installation scope.
    scope = str(PROVIDER_ORG_ID)
    storage = FileStorageService(db_session)
    await storage.write_raw_to_s3(resolve_s3_key("workspace", "global", installer), script)
    await storage.write_raw_to_s3(resolve_s3_key("uploads", scope, package), b"worker package")
    headers = platform_admin.headers
    policy = e2e_client.put(f"/api/files/policies/{package}", headers=headers,
        params={"location": "uploads", "scope": scope},
        json={"policies": [{"name": "worker proof", "actions": ["read"]}]})
    assert policy.status_code == 200, policy.text
    assert policy.json()["solution_id"] is None
    created = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": f"root-worker-{token}", "name": "Root worker proof",
        "organization_id": str(organization_id) if organization_id else None,
    })
    assert created.status_code == 201, created.text
    solution_id = UUID(created.json()["id"])
    workflow_id, deployment_id = uuid4(), uuid4()
    recipe = _recipe(solution_id, workflow_id, path, organization_id)
    recipe = recipe.model_copy(update={"root_file_bindings": {
        "installer": RootFileBinding(location="workspace", path=installer,
            operations=["read", "exists"], max_bytes=len(script), expected_read_sha256=sha256_digest(script)),
        "download": RootFileBinding(location="uploads", path=package,
            operations=["signed_get"], max_bytes=1024),
        "backup": RootFileBinding(location="workspace", path=BACKUP_PREFIX,
            directory_prefix=True, operations=["create"], max_bytes=64 * 1024),
    }})
    source = (
        "from bifrost import workflow, files\n"
        "@workflow(name='Root worker proof')\n"
        "async def run(user: str = 'system'):\n"
        f"    exists = await files.exists({installer!r})\n"
        f"    script = await files.read({installer!r})\n"
        f"    download = await files.get_signed_url({package!r}, method='GET', location='uploads')\n"
        f"    await files.write({backup!r}, '{{\"synthetic\":true}}')\n"
        "    return {'exists': exists, 'script': script, 'download_path': download['path'], 'ttl': download['expires_in']}\n"
    )
    base = f"/api/solutions/{solution_id}/deployments/{deployment_id}/initial-workflow"
    inspect = {"reviewed_recipe": recipe.model_dump(mode="json")}
    candidate = e2e_client.post(f"{base}/candidate", headers=headers, json={
        **inspect, "source_commit_sha": "d" * 40,
        "files": [{"path": path, "content_base64": base64.b64encode(source.encode()).decode()}],
        "resources": [],
    })
    assert candidate.status_code == 200, candidate.text
    activated = e2e_client.post(f"{base}/activate", headers=headers, json={
        **inspect, "expected_evidence_id": candidate.json()["evidence_id"],
    })
    assert activated.status_code == 200, activated.text
    assert await db_session.scalar(select(Execution.id).where(
        Execution.solution_deployment_id == deployment_id)) is None
    result = execute_workflow_sync(e2e_client, headers, str(workflow_id), request_sync=True, max_wait=60)
    assert result["status"] == "Success", result
    assert result["result"] == {
        "exists": True, "script": script.decode(),
        "download_path": resolve_s3_key("uploads", scope, package), "ttl": 600,
    }
    execution = await db_session.get(Execution, UUID(result["execution_id"]))
    assert execution is not None and execution.solution_deployment_id == deployment_id
    assert execution.organization_id == PROVIDER_ORG_ID
    attempt = await db_session.scalar(select(WorkflowExecutionAttempt).where(
        WorkflowExecutionAttempt.execution_id == execution.id))
    assert attempt is not None and attempt.status == "succeeded" and attempt.worker_id
    assert attempt.runtime_evidence_hash == execution.runtime_evidence_hash
    metadata = await db_session.scalar(select(FileMetadata).where(
        FileMetadata.location == "workspace", FileMetadata.path == backup))
    assert metadata is not None and metadata.solution_id is None and metadata.organization_id is None
    assert await storage.read_uploaded_file(metadata.s3_key) == b'{"synthetic":true}'
    assert (await db_session.get(Solution, solution_id)).active_deployment_id == deployment_id
