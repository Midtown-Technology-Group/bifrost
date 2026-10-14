"""Real deploy HTTP, PostgreSQL pointer and object bytes must agree for V2 Apps."""

from contextlib import asynccontextmanager
import hashlib
import io
import uuid
import zipfile

import pytest
import yaml
from sqlalchemy import text

from src.models.orm.applications import Application
from src.services.solution_deploy_obligations import _runtime_and_registration_readback
from src.services.solutions.app_build import SolutionAppBuilder
from src.services.solutions import deploy as deploy_module
from src.services.solutions.app_runtime import compiled_app_runtime_pin
from src.services.solutions.deploy import (
    CompiledSolutionAppDeployment,
    SolutionDeployer,
    solution_entity_id,
)
from tests.e2e.platform.conftest import wait_for_deploy

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize(
    ("corrupt", "lost_activation_ack"), [(False, False), (True, False), (False, True)]
)
async def test_solution_app_accounting_reads_the_active_compiled_bytes(
    e2e_client,
    platform_admin,
    db_session,
    corrupt,
    lost_activation_ack,
    monkeypatch,
):
    assert await db_session.scalar(text("SELECT current_database()")) == "bifrost_test"
    slug = "app-runtime-" + uuid.uuid4().hex[:8]
    headers = platform_admin.headers
    created = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={"slug": slug, "name": slug, "organization_id": None},
    )
    assert created.status_code == 201, created.text
    solution_id = uuid.UUID(created.json()["id"])
    try:
        portable_id = uuid.uuid4()
        app_id = solution_entity_id(solution_id, portable_id)
        compiled = {
            "index.html": '<html><script type="module" src="./assets/main.js?v=1#entry"></script><link rel="stylesheet" href="./assets/main.css?v=2#theme"></html>',
            "assets/main.js": "const version = 1;",
            "assets/main.css": "body { color: blue; }",
        }
        manifest = {
            "apps": {
                str(portable_id): {
                    "id": str(portable_id),
                    "name": slug,
                    "slug": slug,
                    "path": "apps/example",
                    "app_model": "standalone_v2",
                }
            }
        }
        original = yaml.safe_dump(manifest)
        manifest["apps"][str(portable_id)]["dist_files"] = compiled
        manifest["authored_manifest"] = original
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "bifrost.solution.yaml", f"slug: {slug}\nname: {slug}\nversion: 1.0.0\n"
            )
            archive.writestr(".bifrost/apps.yaml", yaml.safe_dump(manifest))
            archive.writestr("apps/example/index.html", "<html>authored input</html>")
        artifact = buffer.getvalue()
        deployed = e2e_client.post(
            f"/api/solutions/{solution_id}/deploy",
            headers={k: v for k, v in headers.items() if k.lower() != "content-type"},
            params={"candidate_id": "sha256:" + hashlib.sha256(artifact).hexdigest()},
            files={"file": ("solution.zip", artifact, "application/zip")},
        )
        assert deployed.status_code == 202, deployed.text
        completed = wait_for_deploy(e2e_client, deployed, headers)
        assert completed.status_code == 200, completed.text
        app = await db_session.get(Application, app_id)
        assert app is not None and app.active_deployment_id is not None
        deployment_id = app.active_deployment_id
        pin = app.published_snapshot["runtime_pin"]
        assert pin["source_artifact_sha256"] == hashlib.sha256(artifact).hexdigest()
        assert pin["deployment_id"] == str(deployment_id)
        served = e2e_client.get(
            f"/api/applications/{app_id}/dist/index.html", headers=headers
        )
        assert served.status_code == 200, served.text
        assert served.content == compiled["index.html"].encode()
        manifest_response = e2e_client.get(
            f"/api/applications/{app_id}/bundle-manifest",
            headers=headers,
            params={"mode": "live"},
        )
        assert manifest_response.status_code == 200, manifest_response.text
        assert manifest_response.json()["entry"] == "assets/main.js?v=1#entry"
        assert manifest_response.json()["css"] == "assets/main.css?v=2#theme"
        served_js = e2e_client.get(
            f"/api/applications/{app_id}/dist/assets/main.js?v=1", headers=headers
        )
        assert (
            served_js.status_code == 200
            and served_js.content == compiled["assets/main.js"].encode()
        )
        if lost_activation_ack:
            # Exercise the shared publisher against real DB/S3. The separate
            # activation transaction commits, then only its acknowledgement is
            # lost; no publish/activation replay is performed.
            original_context = deploy_module._solution_app_activation_db_context
            activations = 0

            @asynccontextmanager
            async def committed_but_unacknowledged():
                nonlocal activations
                activations += 1
                async with original_context() as activation_db:
                    yield activation_db
                raise TimeoutError("activation committed; acknowledgement lost")

            monkeypatch.setattr(
                deploy_module,
                "_solution_app_activation_db_context",
                committed_but_unacknowledged,
            )
            successor_id = uuid.uuid4()
            outputs = {path: raw.encode() for path, raw in compiled.items()}
            successor_pin = compiled_app_runtime_pin(
                solution_id=solution_id,
                application_id=app_id,
                deployment_id=successor_id,
                source_sha256=hashlib.sha256(artifact).hexdigest(),
                outputs=outputs,
                source_built=False,
            )
            successor = CompiledSolutionAppDeployment(
                app_id=app_id,
                solution_id=solution_id,
                deployment_id=successor_id,
                expected_old_deployment_id=deployment_id,
                superseded_deployment_id=deployment_id,
                dist=outputs,
                runtime_pin=successor_pin,
            )
            with pytest.raises(TimeoutError, match="acknowledgement lost"):
                await SolutionDeployer(db_session)._upload_compiled_dists([successor])
            assert activations == 1
            # Independent DB and public HTTP readback must still find every
            # active byte. Before the repair the error handler deleted them.
            await db_session.refresh(app)
            assert app.active_deployment_id == successor_id
            assert app.published_snapshot["runtime_pin"] == successor_pin
            for path, raw in outputs.items():
                response = e2e_client.get(
                    f"/api/applications/{app_id}/dist/{path}", headers=headers
                )
                assert response.status_code == 200, response.text
                assert response.content == raw
            deployment_id = successor_id
            pin = successor_pin
        if corrupt:
            # Mutate only this synthetic test install's object namespace.
            builder = SolutionAppBuilder()
            await builder.upload_deployment(
                app_id,
                deployment_id,
                {
                    "index.html": compiled["index.html"].encode(),
                    "assets/main.js": b"const version = 2;",
                    "assets/main.css": compiled["assets/main.css"].encode(),
                },
            )
            with pytest.raises(ValueError, match="differs"):
                await _runtime_and_registration_readback(
                    db_session, solution_id=solution_id, artifact=artifact
                )
        else:
            valid, reason, evidence = await _runtime_and_registration_readback(
                db_session, solution_id=solution_id, artifact=artifact
            )
            assert valid is True and reason is None
            assert evidence["app_runtime_pins"][str(app_id)] == pin
        await db_session.refresh(app)
        assert app.active_deployment_id == deployment_id
        assert app.published_snapshot["runtime_pin"] == pin
    finally:
        await db_session.rollback()
        removed = e2e_client.request(
            "DELETE",
            f"/api/solutions/{solution_id}",
            headers=headers,
            params={"confirm": slug},
        )
        assert removed.status_code in (200, 204), removed.text
