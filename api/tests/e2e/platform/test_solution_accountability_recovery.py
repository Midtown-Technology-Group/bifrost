"""Recovery persists deployment evidence without deploying the Solution again."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import time
import uuid
import zipfile

import pytest
from sqlalchemy import delete

from src.core.security import decrypt_secret, encrypt_secret
from src.models.orm.applications import Application
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.workspace_promotions import (
    SolutionDeployObligation,
    WorkspaceSourceRelease,
)
from src.services.solution_deploy_obligations import solution_source_content_id
from src.services.solutions.app_build import SolutionAppBuilder
from src.services.solutions.deploy import solution_entity_id
from src.services.solutions.source_artifact import SolutionSourceArtifactStorage
from src.services.solutions.storage import SolutionStorage
from tests.e2e.platform.conftest import wait_for_deploy

pytestmark = pytest.mark.e2e


async def _wait_for_platform_job(e2e_client, headers, job_id):
    deadline = time.monotonic() + 30
    body = {}
    while time.monotonic() < deadline:
        response = e2e_client.get(f"/api/platform-jobs/{job_id}", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in {"succeeded", "failed", "cancelled"}:
            break
        await asyncio.sleep(0.25)
    assert body["status"] == "succeeded", body
    return body


@pytest.mark.parametrize("with_source_app", [False, True])
async def test_recovery_releases_obligation_without_redeploying(
    e2e_client,
    platform_admin,
    org1,
    org1_user,
    db_session,
    with_source_app,
):
    headers = platform_admin.headers
    slug = f"accountability-{uuid.uuid4().hex[:8]}"
    created = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={"slug": slug, "name": slug, "organization_id": None},
    )
    assert created.status_code == 201, created.text
    solution_id = uuid.UUID(created.json()["id"])
    release_id = uuid.uuid4()
    try:
        workflow_id = uuid.uuid4()
        source = (
            "from bifrost import workflow\n\n"
            f'@workflow(name="{slug}")\n'
            "async def run():\n    return 'ok'\n"
        )
        files = {
            "bifrost.solution.yaml": f"slug: {slug}\nname: {slug}\nversion: 1.0.0\n",
            ".bifrost/workflows.yaml": (
                f"workflows:\n  {workflow_id}:\n    id: {workflow_id}\n"
                f"    name: {slug}\n    function_name: run\n"
                "    path: workflows/main.py\n    type: workflow\n"
            ),
            "workflows/main.py": source,
        }
        portable_app_id = uuid.uuid4()
        if with_source_app:
            files.update(
                {
                    ".bifrost/apps.yaml": (
                        f"apps:\n  {portable_app_id}:\n    id: {portable_app_id}\n"
                        f"    name: {slug}\n    slug: {slug}\n    path: apps/example\n"
                        "    app_model: standalone_v2\n    access_level: authenticated\n"
                    ),
                    "apps/example/index.html": '<html><body><div id="app"></div><script type="module" src="/src/main.js"></script></body></html>',
                    "apps/example/src/main.js": 'document.getElementById("app").textContent = "Source-built proof";',
                    "apps/example/package.json": json.dumps(
                        {
                            "name": "source-accounting-fixture",
                            "private": True,
                            "devDependencies": {"vite": "8.2.2"},
                        }
                    ),
                }
            )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for path, content in files.items():
                archive.writestr(path, content)
        artifact = buffer.getvalue()
        candidate_id = f"sha256:{hashlib.sha256(artifact).hexdigest()}"
        deployed = e2e_client.post(
            f"/api/solutions/{solution_id}/deploy",
            headers={k: v for k, v in headers.items() if k.lower() != "content-type"},
            params={"candidate_id": candidate_id},
            files={"file": ("solution.zip", artifact, "application/zip")},
        )
        assert deployed.status_code == 202, deployed.text
        deploy_job_id = uuid.UUID(deployed.json()["deploy_job_id"])
        completed = wait_for_deploy(e2e_client, deployed, headers)
        assert completed.status_code == 200, completed.text
        await _wait_for_platform_job(e2e_client, headers, deploy_job_id)

        # Simulate a historical deploy with configured tracking whose evidence
        # flush failed. Keep its real successful job, candidate and stored bytes.
        job = await db_session.get(PlatformJob, deploy_job_id)
        assert job is not None and job.encrypted_payload is not None
        payload = json.loads(decrypt_secret(job.encrypted_payload))
        payload["options"]["accountability_organization_id"] = str(org1["id"])
        job.encrypted_payload = encrypt_secret(json.dumps(payload))
        subpath = f"solutions/{slug}"
        source_files = [
            {
                "path": f"{subpath}/{path}",
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "size": len(content.encode()),
                "mode": "100644",
            }
            for path, content in sorted(files.items())
        ]
        commit_sha = hashlib.sha1(slug.encode(), usedforsecurity=False).hexdigest()
        db_session.add(
            WorkspaceSourceRelease(
                id=release_id,
                organization_id=org1["id"],
                source_commit_sha=commit_sha,
                source_tree_sha="a" * 40,
                paths={},
                declaration_actor="platform_admin",
                declared_disposition="pending",
                disposition="pending",
                created_by=platform_admin.user_id,
            )
        )
        await db_session.flush()
        obligation = SolutionDeployObligation(
            source_release_id=release_id,
            organization_id=org1["id"],
            source_commit_sha=commit_sha,
            source_tree_sha="a" * 40,
            solution_slug=slug,
            repo_subpath=subpath,
            source_subtree_sha="b" * 40,
            source_content_id=solution_source_content_id(
                solution_slug=slug,
                repo_subpath=subpath,
                source_files=source_files,
            ),
            source_files=source_files,
            changed_paths={},
            declared_disposition="solution_deploy_required",
            disposition="attention_required",
            reason="historical evidence flush failed",
        )
        db_session.add(obligation)
        await db_session.commit()

        def read_solution():
            response = e2e_client.get(f"/api/solutions/{solution_id}", headers=headers)
            assert response.status_code == 200, response.text
            return response.json()

        before = read_solution()
        app = None
        builder = None
        app_before = None
        app_outputs_before = None
        if with_source_app:
            app = await db_session.get(
                Application, solution_entity_id(solution_id, portable_app_id)
            )
            assert app is not None and app.active_deployment_id is not None
            assert app.published_snapshot["runtime_pin"]["build_mode"] == "source"
            app_before = (app.active_deployment_id, app.published_snapshot)
            builder = SolutionAppBuilder()
            app_outputs_before = {
                path: await builder.read_dist(
                    app.id, path, deployment_id=app.active_deployment_id
                )
                for path in await builder.list_dist(
                    app.id, deployment_id=app.active_deployment_id
                )
            }
        storage = SolutionStorage(solution_id)
        runtime_before = {
            path: await storage.read(path) for path in await storage.list()
        }
        route = f"/api/solutions/{solution_id}/deploy-jobs/{deploy_job_id}/reconcile"
        forbidden = e2e_client.post(route, headers=org1_user.headers)
        assert forbidden.status_code == 403, forbidden.text
        recovered = e2e_client.post(route, headers=headers)
        assert recovered.status_code == 202, recovered.text
        recovery_job_id = recovered.json()["job_id"]
        assert recovered.json()["notification_id"]
        assert recovered.headers["location"] == f"/api/platform-jobs/{recovery_job_id}"
        body = await _wait_for_platform_job(e2e_client, headers, recovery_job_id)
        assert body["job_type"] == "solution.deploy.reconcile"
        accountability = body["result"]["source_release_accountability"]
        assert accountability["state"] == "released", body
        assert accountability["obligation_id"] == str(obligation.id)
        await db_session.refresh(obligation)
        assert obligation.disposition == "released"
        assert obligation.solution_id == solution_id
        assert obligation.deploy_job_id == deploy_job_id
        assert obligation.candidate_id == candidate_id
        assert (
            obligation.completion_evidence["evidence_id"]
            == accountability["evidence_id"]
        )
        assert obligation.resolved_at is not None
        assert read_solution() == before
        assert await SolutionSourceArtifactStorage(solution_id).read() == artifact
        assert {
            path: await storage.read(path) for path in await storage.list()
        } == runtime_before
        repeated = e2e_client.post(route, headers=headers)
        assert repeated.status_code == 202, repeated.text
        repeated_body = await _wait_for_platform_job(
            e2e_client, headers, repeated.json()["job_id"]
        )
        assert (
            repeated_body["result"]["source_release_accountability"]["evidence_id"]
            == accountability["evidence_id"]
        )
        if with_source_app:
            assert app is not None and builder is not None
            await db_session.refresh(app)
            assert (app.active_deployment_id, app.published_snapshot) == app_before
            assert {
                path: await builder.read_dist(
                    app.id, path, deployment_id=app.active_deployment_id
                )
                for path in await builder.list_dist(
                    app.id, deployment_id=app.active_deployment_id
                )
            } == app_outputs_before
    finally:
        await db_session.rollback()
        await db_session.execute(
            delete(WorkspaceSourceRelease).where(
                WorkspaceSourceRelease.id == release_id
            )
        )
        await db_session.commit()
        removed = e2e_client.request(
            "DELETE",
            f"/api/solutions/{solution_id}",
            headers=headers,
            params={"confirm": slug},
        )
        assert removed.status_code in (200, 204), removed.text
