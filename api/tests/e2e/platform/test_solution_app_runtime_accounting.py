"""Real deploy HTTP, PostgreSQL pointer and object bytes must agree for V2 Apps."""

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
from src.services.solutions.deploy import solution_entity_id
from tests.e2e.platform.conftest import wait_for_deploy

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("corrupt", [False, True])
async def test_solution_app_accounting_reads_the_active_compiled_bytes(
    e2e_client,
    platform_admin,
    db_session,
    corrupt,
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
            "index.html": '<html><script type="module" src="assets/main.js"></script></html>',
            "assets/main.js": "const version = 1;",
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
        assert manifest_response.json()["entry"] == "assets/main.js"
        if corrupt:
            # Mutate only this synthetic test install's object namespace.
            builder = SolutionAppBuilder()
            await builder.upload_deployment(
                app_id,
                deployment_id,
                {
                    "index.html": compiled["index.html"].encode(),
                    "assets/main.js": b"const version = 2;",
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
