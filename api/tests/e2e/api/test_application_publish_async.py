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


def test_publish_success_deduplication_and_requester_visibility(
    e2e_client, platform_admin, org1, org1_user, app_factory
):
    app = app_factory(
        platform_admin.headers,
        f"async-publish-{uuid.uuid4().hex[:8]}",
        organization_id=org1["id"],
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
    accepted = bodies[0]
    assert all(
        response.headers["location"].endswith(f"/{accepted['job_id']}")
        for response in responses
    )
    assert accepted["notification_id"]
    # Write authorization precedes deduplication and does not reveal the job.
    duplicate = e2e_client.post(
        f"/api/applications/{app['id']}/publish",
        headers=org1_user.headers,
    )
    assert duplicate.status_code == 404, duplicate.text
    assert duplicate.json()["detail"] == (
        f"Application '{app['id']}' not found"
    )
    visible = e2e_client.get(
        f"/api/platform-jobs/{accepted['job_id']}",
        headers=platform_admin.headers,
    )
    assert visible.status_code == 200, visible.text
    assert "payload" not in visible.json()
    assert visible.json()["job_type"] == "application.publish"
    hidden = e2e_client.get(
        f"/api/platform-jobs/{accepted['job_id']}",
        headers=org1_user.headers,
    )
    assert hidden.status_code == 404
    body = _poll(
        e2e_client,
        platform_admin.headers,
        accepted["job_id"],
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
        accepted["notification_id"],
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
async def test_captured_repository_publication_serves_exact_build_and_preserves_editor_controls(
    e2e_client, platform_admin, app_factory, db_session,
):
    from src.repositories.applications import ApplicationRepository
    from src.services.application_publication import publication_controls_hash
    from src.services.application_publication_evidence import read_app_publication_runtime_pin
    from src.services.application_source_accountability import PreparedAppAccounting, verify_app_accounting
    from src.models.orm.platform_jobs import PlatformJob
    from src.core.constants import SYSTEM_USER_UUID
    from src.services.inline_app_source import InlineAppSourceSnapshot
    from bifrost.workspace_release import canonical_digest

    app = app_factory(platform_admin.headers, f"captured-publish-{uuid.uuid4().hex[:8]}")
    storage = AppStorageService()
    old = _captured_bundle("before-capture")
    await storage.publish(app["id"], bundle_files=old)
    before_revision = await storage.live_manifest_revision(app["id"])
    await storage.write_preview_file(app["id"], "editor-marker.tsx", b"independent draft")
    source_before = e2e_client.get(f"/api/applications/{app['id']}/files", headers=platform_admin.headers)
    assert source_before.status_code == 200
    app_id = uuid.UUID(app["id"])
    controls = await publication_controls_hash(db_session, app_id)
    captured = InlineAppSourceSnapshot({"app.yaml": b"scope: global\naccess_level: public\n",
        "pages/index.tsx": b'export default () => <h1>captured publication runtime</h1>;\n'})
    # This is compiler/publication integration proof. GitHub/OIDC admission has
    # separate source-bound tests; this test does not claim a real GitHub token.
    source_proof = {"schema_version": "bifrost.inline-app-git-source/v1",
        "application_id": app["id"], "organization_id": app.get("organization_id"),
        "source_commit_sha": "a" * 40, "source_tree_sha": "b" * 40, "source_subtree_sha": "c" * 40,
        "repository": "example/ci-fixture", "repository_id": 10, "repository_owner_id": 20,
        "repo_subpath": app["repo_path"], "source_hashes": captured.hashes(),
        "file_modes": {path: "100644" for path in captured.hashes()},
        "metadata_mode": "preserve_installed_controls"}
    protected = {"source_commit_sha": "a" * 40, "artifact_digest": canonical_digest(source_proof),
        "ci_run_id": 30, "ci_run_attempt": 1, "producer_run_id": "40", "producer_run_attempt": 1,
        "expected_controls_hash": controls}
    source_proof.update({key: value for key, value in protected.items() if key != "expected_controls_hash"})
    saved = []
    guard_calls = []

    async def guard():
        assert await publication_controls_hash(db_session, app_id) == controls
        guard_calls.append("checked")

    async def checkpoint(intent):
        assert await storage.read_file(app["id"], "live", "manifest.json") == old["manifest.json"]
        saved.append(intent)

    repository = ApplicationRepository(db_session, app.get("organization_id"),
        user_id=None, is_superuser=True)
    published = await repository.publish(app_id, "CI capture fixture", source_snapshot=captured,
        source_provenance=source_proof, before_publication=guard, checkpoint_callback=checkpoint)
    assert published is not None
    await db_session.commit()
    assert len(saved) == 2 and len(guard_calls) == 2
    assert saved[0]["manifest_write_started"] is False
    assert saved[1] == {**saved[0], "manifest_write_started": True}
    assert saved[0]["expected_live_etag"] == before_revision.etag
    intent = {**saved[-1], "controls_hash": controls}
    assert await storage.verify_publication(app["id"], intent) == len(intent["artifact_hashes"])
    manifest_bytes = await storage.read_file(app["id"], "live", "manifest.json")
    manifest = json.loads(manifest_bytes)
    assert manifest["git_source_evidence"] == source_proof
    assert manifest["source_snapshot_evidence"]["authored_source_hashes"] == captured.hashes()
    original_job_id = uuid.uuid4()
    runtime_pin = await read_app_publication_runtime_pin(storage,
        application_id=app_id, publication_job_id=original_job_id,
        organization_id=uuid.UUID(app["organization_id"]) if app.get("organization_id") else None,
        intent=intent, protected_git=protected)
    assert runtime_pin["publication_job_id"] == str(original_job_id)
    assert runtime_pin["source"] == source_proof
    assert runtime_pin["manifest_hash"] == intent["artifact_hashes"]["manifest.json"]
    assert runtime_pin["runtime_pin_hash"] == canonical_digest({
        key: value for key, value in runtime_pin.items() if key != "runtime_pin_hash"})
    # Final accounting uses actual PostgreSQL rows and published storage bytes.
    # Immutable Git/producer admission is exercised separately; this fixture
    # supplies a prepared proof, rather than claiming a real GitHub signature.
    original = PlatformJob(id=original_job_id, job_type="application.publish", status="succeeded",
        requested_by_user_id=str(SYSTEM_USER_UUID), requested_by_email="ci@bifrost.internal",
        requested_by_name="CI fixture", resource_type="application", resource_id=app["id"],
        organization_id=published.organization_id, title="Captured publication accounting fixture",
        payload={"application_id": app["id"], "protected_git": protected},
        result={"publication_verified": True, "publication_intent": intent, "runtime_pin": runtime_pin})
    db_session.add(original)
    await db_session.flush()
    assert published.repo_path is not None
    prepared = PreparedAppAccounting(app_id, original.id, published.organization_id, published.repo_path,
        canonical_digest({"payload": original.payload, "result": original.result}), runtime_pin,
        {"commit_sha": source_proof["source_commit_sha"], "tree_sha": source_proof["source_tree_sha"],
            "artifact_digest": source_proof["artifact_digest"]})
    consumers = await verify_app_accounting(db_session, [prepared])
    assert len(consumers) == 1
    assert consumers[0].publication_job_id == str(original_job_id)
    assert consumers[0].runtime_pin_hash == runtime_pin["runtime_pin_hash"]
    assert set(consumers[0].sources) == {f"{app['repo_path']}/{path}" for path in captured.files}
    # A metadata drift must leave accounting open, with no publication replay.
    old_scope = published.organization_id
    published.organization_id = None if old_scope is not None else uuid.UUID("00000000-0000-0000-0000-000000000002")
    await db_session.flush()
    assert await verify_app_accounting(db_session, [prepared]) == []
    published.organization_id = old_scope
    await db_session.flush()
    await db_session.delete(original)
    await db_session.flush()
    for path, expected_hash in intent["artifact_hashes"].items():
        served = e2e_client.get(f"/api/applications/{app['id']}/bundle-asset/{path}?mode=live",
            headers=platform_admin.headers)
        assert served.status_code == 200, served.text
        assert "sha256:" + hashlib.sha256(served.content).hexdigest() == expected_hash
    assert await publication_controls_hash(db_session, app_id) == controls
    assert await storage.read_file(app["id"], "preview", "editor-marker.tsx") == b"independent draft"
    assert await storage.read_file(app["id"], "live", "entry-before-capture.js") == old["entry-before-capture.js"]
    source_after = e2e_client.get(f"/api/applications/{app['id']}/files", headers=platform_admin.headers)
    assert source_after.status_code == 200 and source_after.json() == source_before.json()


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
    saved = []
    async def competing_publication(_intent):
        saved.append(_intent)
        if _intent["manifest_write_started"] is False:
            await storage.publish(app["id"], bundle_files=winner)
    with pytest.raises(Exception):
        await storage.publish(app["id"], bundle_files=_captured_bundle("late"),
                              checkpoint_callback=competing_publication)
    assert [proof["manifest_write_started"] for proof in saved] == [False, True, True]
    assert saved[-1]["manifest_write_rejected"] is True
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


@pytest.mark.asyncio
async def test_storage_does_not_switch_manifest_when_second_checkpoint_loses_lease(
    platform_admin, app_factory,
):
    from src.jobs.platform.base import PlatformJobCancelled

    app = app_factory(platform_admin.headers, f"fenced-write-{uuid.uuid4().hex[:8]}")
    storage = AppStorageService()
    old, proposed = _captured_bundle("before-lease-loss"), _captured_bundle("lost-lease")
    await storage.publish(app["id"], bundle_files=old)
    revision = await storage.live_manifest_revision(app["id"])
    saved = []

    async def checkpoint(intent):
        if intent["manifest_write_started"] is True:
            raise PlatformJobCancelled
        saved.append(intent)

    with pytest.raises(PlatformJobCancelled):
        await storage.publish(app["id"], bundle_files=proposed, checkpoint_callback=checkpoint)
    assert len(saved) == 1 and saved[0]["manifest_write_started"] is False
    assert await storage.live_manifest_revision(app["id"]) == revision
    assert await storage.read_file(app["id"], "live", "manifest.json") == old["manifest.json"]
    # Durable output objects are not a publication; neither old nor orphaned
    # outputs are deleted as part of this failed attempt.
    assert await storage.read_file(app["id"], "live", "entry-lost-lease.js") == proposed["entry-lost-lease.js"]
