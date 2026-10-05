from __future__ import annotations

import hashlib
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import update

from src.models.orm.platform_jobs import PlatformJob
from src.services.app_storage import AppStorageService

pytestmark = pytest.mark.e2e


def _create_app(
    e2e_client,
    headers,
    slug: str,
    *,
    organization_id: str | None = None,
) -> dict:
    body = {
        "name": slug,
        "slug": slug,
        "app_model": "inline_v1",
    }
    if organization_id is not None:
        body["organization_id"] = organization_id
    response = e2e_client.post(
        "/api/applications",
        headers=headers,
        json=body,
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def app_factory(e2e_client, platform_admin):
    """Create publish-test apps and remove them after each shared E2E session test."""
    app_ids: list[str] = []

    def create(
        headers,
        slug: str,
        *,
        organization_id: str | None = None,
    ) -> dict:
        app = _create_app(
            e2e_client,
            headers,
            slug,
            organization_id=organization_id,
        )
        app_ids.append(app["id"])
        return app

    yield create

    for app_id in reversed(app_ids):
        response = e2e_client.delete(
            f"/api/applications/{app_id}",
            headers=platform_admin.headers,
        )
        assert response.status_code in (204, 404), response.text


def _poll(e2e_client, headers, job_id: str) -> dict:
    for _ in range(120):
        response = e2e_client.get(
            f"/api/platform-jobs/{job_id}",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in ("succeeded", "failed", "cancelled", "requires_action"):
            return body
        time.sleep(0.25)
    raise AssertionError(f"publish job {job_id} did not finish")


def _poll_notification(e2e_client, headers, notification_id: str, status: str) -> dict:
    """Wait for the Redis notification projection to follow terminal job state."""
    for _ in range(120):
        response = e2e_client.get(
            f"/api/notifications/{notification_id}",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] == status:
            return body
        time.sleep(0.25)
    raise AssertionError(
        f"notification {notification_id} did not reach status {status}"
    )


def test_enqueue_poll_and_success(e2e_client, platform_admin, app_factory):
    app = app_factory(
        platform_admin.headers,
        f"async-publish-{uuid.uuid4().hex[:8]}",
    )

    response = e2e_client.post(
        f"/api/applications/{app['id']}/publish",
        headers=platform_admin.headers,
    )

    assert response.status_code == 202, response.text
    assert response.headers["location"].endswith(
        f"/{response.json()['job_id']}"
    )
    assert response.json()["notification_id"]
    accepted = response.json()
    visible = e2e_client.get(
        f"/api/platform-jobs/{accepted['job_id']}",
        headers=platform_admin.headers,
    )
    assert visible.status_code == 200, visible.text
    assert "payload" not in visible.json()
    assert visible.json()["job_type"] == "application.publish"
    body = _poll(
        e2e_client,
        platform_admin.headers,
        response.json()["job_id"],
    )
    assert body["status"] == "succeeded", body
    assert body["result"]["publication_verified"] is True
    assert body["result"]["files_published"] >= 2
    assert body["completed_at"] is not None
    application = e2e_client.get(
        f"/api/applications/{app['slug']}",
        headers=platform_admin.headers,
    )
    assert application.status_code == 200
    assert application.json()["is_published"] is True
    # Verify the actual durable publish/build/object-storage boundary. Source
    # reads are current authoring; the live manifest separately attests the
    # materialized build inputs and every promoted output byte.
    source = e2e_client.get(
        f"/api/applications/{app['id']}/files", headers=platform_admin.headers,
    )
    assert source.status_code == 200, source.text
    expected_sources = {
        item["path"]: "sha256:" + hashlib.sha256(item["source"].encode()).hexdigest()
        for item in source.json()["files"]
        if item["path"] != "app.yaml" and ".tmp." not in item["path"]
    }
    manifest_response = e2e_client.get(
        f"/api/applications/{app['id']}/bundle-asset/manifest.json?mode=live",
        headers=platform_admin.headers,
    )
    assert manifest_response.status_code == 200, manifest_response.text
    assert manifest_response.headers["cache-control"] == "no-store"
    manifest = manifest_response.json()
    evidence = manifest["build_evidence"]
    assert evidence["schema_version"] == "bifrost.inline-app-build/v1"
    assert evidence["materialized_source_hashes"] == expected_sources
    assert set(evidence["output_hashes"]) == set(manifest["outputs"])
    for filename, expected_hash in evidence["output_hashes"].items():
        asset = e2e_client.get(
            f"/api/applications/{app['id']}/bundle-asset/{filename}?mode=live",
            headers=platform_admin.headers,
        )
        assert asset.status_code == 200, asset.text
        assert "sha256:" + hashlib.sha256(asset.content).hexdigest() == expected_hash
    notification = _poll_notification(
        e2e_client,
        platform_admin.headers,
        response.json()["notification_id"],
        "completed",
    )
    assert notification["percent"] == 100


def test_repeat_publication_reuses_immutable_outputs(e2e_client, platform_admin, app_factory):
    app = app_factory(platform_admin.headers, f"repeat-publish-{uuid.uuid4().hex[:8]}")
    for _ in range(2):
        accepted = e2e_client.post(f"/api/applications/{app['id']}/publish", headers=platform_admin.headers)
        assert accepted.status_code == 202, accepted.text
        result = _poll(e2e_client, platform_admin.headers, accepted.json()["job_id"])
        assert result["status"] == "succeeded", result
        assert result["result"]["publication_verified"] is True


@pytest.mark.asyncio
async def test_captured_source_build_preserves_real_editor_live_storage_and_app_controls(
    e2e_client, platform_admin, app_factory,
):
    from src.services.app_bundler import BundlerService
    from src.services.inline_app_source import InlineAppSourceSnapshot

    app = app_factory(platform_admin.headers, f"captured-build-{uuid.uuid4().hex[:8]}")
    headers = platform_admin.headers
    storage = AppStorageService()
    original_live = _captured_bundle("already-published")
    await storage.publish(app["id"], bundle_files=original_live)
    await storage.write_preview_file(app["id"], "editor-marker.tsx", b"independent editor state")
    controls_before = e2e_client.get(f"/api/applications/{app['slug']}", headers=headers)
    source_before = e2e_client.get(f"/api/applications/{app['id']}/files", headers=headers)
    assert controls_before.status_code == source_before.status_code == 200
    authored = {
        "app.yaml": b"scope: global\naccess_level: public\n",
        "pages/index.tsx": (
            b'import { useNavigate } from "react-router-dom";\n'
            b'export default function Page() { const navigate = useNavigate(); '
            b'return <button onClick={() => navigate("/")}>captured protected source</button>; }\n'
        ),
    }
    result = await BundlerService().build(
        app["id"], f"apps/{app['slug']}/", "capture", source_snapshot=InlineAppSourceSnapshot(authored),
    )
    assert result.success, result.errors
    assert result.publication_files is not None
    manifest = json.loads(result.publication_files["manifest.json"])
    evidence = manifest["source_snapshot_evidence"]
    assert evidence["authored_source_hashes"] == {
        path: "sha256:" + hashlib.sha256(content).hexdigest() for path, content in authored.items()
    }
    assert evidence["migration_changed_paths"] == ["pages/index.tsx"]
    assert evidence["compiler_source_hashes"]["pages/index.tsx"] != evidence["authored_source_hashes"]["pages/index.tsx"]
    assert evidence["metadata_not_applied"] == ["app.yaml"]
    assert b"captured protected source" in result.publication_files[manifest["entry"]]
    for path, digest in manifest["build_evidence"]["output_hashes"].items():
        assert "sha256:" + hashlib.sha256(result.publication_files[path]).hexdigest() == digest
    assert await storage.read_file(app["id"], "preview", "editor-marker.tsx") == b"independent editor state"
    for path, content in original_live.items():
        assert await storage.read_file(app["id"], "live", path) == content
    controls_after = e2e_client.get(f"/api/applications/{app['slug']}", headers=headers)
    source_after = e2e_client.get(f"/api/applications/{app['id']}/files", headers=headers)
    assert controls_after.status_code == source_after.status_code == 200
    assert controls_after.json() == controls_before.json()
    assert source_after.json() == source_before.json()


@pytest.mark.asyncio
async def test_uncertain_publish_request_recovers_original_job_without_manifest_write(
    e2e_client, platform_admin, app_factory, db_session,
):
    app = app_factory(platform_admin.headers, f"readback-publish-{uuid.uuid4().hex[:8]}")
    accepted = e2e_client.post(f"/api/applications/{app['id']}/publish", headers=platform_admin.headers)
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job_id"]
    completed = _poll(e2e_client, platform_admin.headers, job_id)
    assert completed["status"] == "succeeded", completed
    intent = completed["result"]["publication_intent"]
    storage = AppStorageService()
    async def revision():
        async with storage._get_client() as client:
            result = await client.get_object(Bucket=storage._bucket, Key=storage._key(app["id"], "live", "manifest.json"))
            await result["Body"].read()
            return result["ETag"]
    before = await revision()
    # Simulate a lost terminal response after the checkpoint and storage switch.
    # The public request must reuse this row, not publish current editable source.
    await db_session.execute(update(PlatformJob).where(PlatformJob.id == uuid.UUID(job_id)).values(
        status="requires_action", result=intent,
    ))
    await db_session.commit()
    resumed = e2e_client.post(f"/api/applications/{app['id']}/publish", headers=platform_admin.headers)
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["job_id"] == job_id
    recovered = _poll(e2e_client, platform_admin.headers, job_id)
    assert recovered["status"] == "succeeded", recovered
    assert recovered["result"]["recovered_from_intent"] is True
    assert recovered["result"]["publication_intent"] == intent
    assert await revision() == before


def _captured_bundle(label: str, *, filename: str | None = None) -> dict[str, bytes]:
    filename = filename or f"entry-{label}.js"
    body = f"export default {json.dumps(label)};".encode()
    manifest = {"entry": filename, "outputs": [filename], "build_evidence": {
        "schema_version": "bifrost.inline-app-build/v1",
        "output_hashes": {filename: "sha256:" + hashlib.sha256(body).hexdigest()},
    }}
    return {filename: body, "manifest.json": json.dumps(manifest).encode()}


@pytest.mark.asyncio
async def test_storage_rejects_late_publication_without_deleting_previous_outputs(
    platform_admin, app_factory,
):
    app = app_factory(platform_admin.headers, f"stale-publish-{uuid.uuid4().hex[:8]}")
    storage = AppStorageService()
    await storage.publish(app["id"], bundle_files=_captured_bundle("old"))
    winner = _captured_bundle("winner")
    async def competing_publication(_intent):
        await storage.publish(app["id"], bundle_files=winner)
    with pytest.raises(Exception):
        await storage.publish(app["id"], bundle_files=_captured_bundle("late"),
                              checkpoint_callback=competing_publication)
    assert await storage.read_file(app["id"], "live", "manifest.json") == winner["manifest.json"]
    assert await storage.read_file(app["id"], "live", "entry-old.js") == _captured_bundle("old")["entry-old.js"]
    assert await storage.read_file(app["id"], "live", "entry-late.js") == _captured_bundle("late")["entry-late.js"]


@pytest.mark.asyncio
async def test_storage_rejects_conflicting_immutable_output_bytes(platform_admin, app_factory):
    app = app_factory(platform_admin.headers, f"collision-publish-{uuid.uuid4().hex[:8]}")
    storage = AppStorageService()
    original = _captured_bundle("old", filename="same-output.js")
    await storage.publish(app["id"], bundle_files=original)
    with pytest.raises(ValueError, match="conflicts with immutable storage"):
        await storage.publish(app["id"], bundle_files=_captured_bundle("new", filename="same-output.js"))
    assert await storage.read_file(app["id"], "live", "same-output.js") == original["same-output.js"]
    assert await storage.read_file(app["id"], "live", "manifest.json") == original["manifest.json"]


def test_job_is_not_visible_to_another_user(
    e2e_client,
    platform_admin,
    org1_user,
    app_factory,
):
    app = app_factory(
        platform_admin.headers,
        f"private-publish-{uuid.uuid4().hex[:8]}",
    )
    response = e2e_client.post(
        f"/api/applications/{app['id']}/publish",
        headers=platform_admin.headers,
    )
    assert response.status_code == 202, response.text
    hidden = e2e_client.get(
        f"/api/platform-jobs/{response.json()['job_id']}",
        headers=org1_user.headers,
    )
    assert hidden.status_code == 404
    terminal = _poll(
        e2e_client,
        platform_admin.headers,
        response.json()["job_id"],
    )
    assert terminal["status"] == "succeeded", terminal


def test_concurrent_enqueue_reuses_active_job(
    e2e_client,
    platform_admin,
    app_factory,
):
    app = app_factory(
        platform_admin.headers,
        f"concurrent-publish-{uuid.uuid4().hex[:8]}",
    )
    barrier = Barrier(2)

    def enqueue():
        barrier.wait()
        return e2e_client.post(
            f"/api/applications/{app['id']}/publish",
            headers=platform_admin.headers,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _index: enqueue(), range(2)))

    assert all(response.status_code == 202 for response in responses), [
        response.text for response in responses
    ]
    bodies = [response.json() for response in responses]
    assert len({body["job_id"] for body in bodies}) == 1
    assert len({body["notification_id"] for body in bodies}) == 1
    assert sorted(body["reused"] for body in bodies) == [False, True]
    terminal = _poll(
        e2e_client,
        platform_admin.headers,
        bodies[0]["job_id"],
    )
    assert terminal["status"] == "succeeded", terminal


def test_different_requester_gets_conflict_for_active_job(
    e2e_client,
    platform_admin,
    org1,
    org1_user,
    app_factory,
):
    app = app_factory(
        platform_admin.headers,
        f"cross-user-publish-{uuid.uuid4().hex[:8]}",
        organization_id=org1["id"],
    )
    first = e2e_client.post(
        f"/api/applications/{app['id']}/publish",
        headers=platform_admin.headers,
    )
    assert first.status_code == 202, first.text

    duplicate = e2e_client.post(
        f"/api/applications/{app['id']}/publish",
        headers=org1_user.headers,
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["detail"] == (
        "An application publish is already in progress"
    )

    terminal = _poll(
        e2e_client,
        platform_admin.headers,
        first.json()["job_id"],
    )
    assert terminal["status"] == "succeeded", terminal


def test_bundle_failure_is_persisted_and_does_not_publish(
    e2e_client,
    platform_admin,
    app_factory,
):
    app = app_factory(
        platform_admin.headers,
        f"failed-publish-{uuid.uuid4().hex[:8]}",
    )
    update = e2e_client.put(
        f"/api/applications/{app['id']}/files/pages/index.tsx",
        headers=platform_admin.headers,
        json={"source": "export default function Index( {"},
    )
    assert update.status_code == 200, update.text

    response = e2e_client.post(
        f"/api/applications/{app['id']}/publish",
        headers=platform_admin.headers,
    )
    assert response.status_code == 202, response.text
    body = _poll(
        e2e_client,
        platform_admin.headers,
        response.json()["job_id"],
    )

    assert body["status"] == "failed", body
    assert "Bundle build failed" in body["error"]["message"]
    notification = _poll_notification(
        e2e_client,
        platform_admin.headers,
        response.json()["notification_id"],
        "failed",
    )
    assert notification["status"] == "failed"
    assert "Bundle build failed" in notification["error"]
    app_response = e2e_client.get(
        f"/api/applications/{app['slug']}",
        headers=platform_admin.headers,
    )
    assert app_response.status_code == 200
    assert app_response.json()["is_published"] is False
