"""HTTP contract coverage for authoritative workspace _repo changesets."""

import base64
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from src.models.contracts.workspace_repo_changesets import WorkspaceRepoActivateRequest
from src.models.orm.users import User
from src.models.orm.workspace_repo_changesets import WorkspaceRepoChangeset
from src.repositories.workspace_repo_changesets import GIT_CLOSURE_PENDING_STALE_AFTER
from src.services.platform_commit_writer import (
    PlatformCommitResult,
    PlatformCommitSnapshot,
)
from src.services.repo_storage import RepoStorage
from src.services.workspace_repo_changesets import WorkspaceRepoChangesetService

from tests.e2e.conftest import execute_workflow_sync, write_and_register


@pytest.mark.e2e
def test_workspace_repo_changeset_stages_validates_and_activates_atomically(
    e2e_client, platform_admin
):
    scope = f"test_changesets_{uuid4().hex}"
    path = f"{scope}/hello.txt"
    headers = platform_admin.headers
    content = b"hello from a changeset\n"
    try:
        state = e2e_client.get(
            "/api/workspace-repo-changesets/state",
            headers=headers,
            params={"scope": scope},
        )
        assert state.status_code == 200, state.text
        assert state.json()["storage_root"] == "_repo"

        started = e2e_client.post(
            "/api/workspace-repo-changesets",
            headers=headers,
            json={
                "scope": scope,
                "base_revision": state.json()["revision"],
                "worker_id": "e2e-worker",
            },
        )
        assert started.status_code == 201, started.text
        changeset_id = started.json()["id"]

        staged = e2e_client.post(
            f"/api/workspace-repo-changesets/{changeset_id}/files",
            headers=headers,
            json={
                "path": path,
                "operation": "write",
                "content_base64": base64.b64encode(content).decode(),
            },
        )
        assert staged.status_code == 200, staged.text
        assert staged.json()["status"] == "staged"

        # Staging never exposes content through the authoritative files API.
        absent = e2e_client.post(
            "/api/files/exists", headers=headers, json={"path": path}
        )
        assert absent.status_code == 200
        assert absent.json()["exists"] is False

        validated = e2e_client.post(
            f"/api/workspace-repo-changesets/{changeset_id}/validate", headers=headers
        )
        assert validated.status_code == 200, validated.text
        assert validated.json()["valid"] is True

        activated = e2e_client.post(
            f"/api/workspace-repo-changesets/{changeset_id}/activate",
            headers=headers,
            json={"candidate_id": validated.json()["candidate_id"]},
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["status"] == "activated"

        read = e2e_client.post("/api/files/read", headers=headers, json={"path": path})
        assert read.status_code == 200, read.text
        assert read.json()["content"] == content.decode()
    finally:
        # The test owns this unique prefix and may clean it directly.
        import asyncio

        asyncio.run(RepoStorage().delete(path))


@pytest.mark.e2e
def test_workspace_repo_changeset_registers_new_decorated_functions_atomically(
    e2e_client, platform_admin
):
    suffix = uuid4().hex
    scope = f"test_changeset_registration_{suffix}"
    path = f"{scope}/workflow.py"
    function_name = f"planned_workflow_{suffix}"
    headers = platform_admin.headers
    workflow_id = None
    source = f'''from bifrost import workflow

@workflow(name="Planned workflow {suffix}")
async def {function_name}() -> dict:
    return {{"registered": True}}
'''
    try:
        started = e2e_client.post(
            "/api/workspace-repo-changesets",
            headers=headers,
            json={"scope": scope},
        )
        assert started.status_code == 201, started.text
        changeset_id = started.json()["id"]

        staged = e2e_client.post(
            f"/api/workspace-repo-changesets/{changeset_id}/files",
            headers=headers,
            json={
                "path": path,
                "operation": "write",
                "content_base64": base64.b64encode(source.encode()).decode(),
            },
        )
        assert staged.status_code == 200, staged.text

        validated = e2e_client.post(
            f"/api/workspace-repo-changesets/{changeset_id}/validate",
            headers=headers,
        )
        assert validated.status_code == 200, validated.text
        assert validated.json()["valid"] is True, validated.text
        registration_actions = validated.json()["registration_actions"]
        assert len(registration_actions) == 1
        assert registration_actions[0] | {"organization_id": None} == {
            "action": "create",
            "path": path,
            "function_name": function_name,
            "type": "workflow",
            "name": f"Planned workflow {suffix}",
            "requested_id": None,
            "organization_id": None,
        }
        assert registration_actions[0]["organization_id"]

        activated = e2e_client.post(
            f"/api/workspace-repo-changesets/{changeset_id}/activate",
            headers=headers,
            json={"candidate_id": validated.json()["candidate_id"]},
        )
        assert activated.status_code == 200, activated.text
        workflow_id = activated.json()["validation"]["registration_actions"][0][
            "workflow_id"
        ]

        listing = e2e_client.get("/api/workflows", headers=headers)
        assert listing.status_code == 200, listing.text
        registered = next(
            (
                item
                for item in listing.json()
                if item.get("source_file_path") == path
                and item.get("function_name") == function_name
            ),
            None,
        )
        assert registered is not None, listing.json()
        assert registered["id"] == workflow_id
        assert registered["name"] == f"Planned workflow {suffix}"

        execution = execute_workflow_sync(
            e2e_client, headers, registered["id"], max_wait=30.0
        )
        assert execution["status"] == "Success", execution
        assert execution["result"] == {"registered": True}
    finally:
        if workflow_id:
            e2e_client.request(
                "DELETE",
                f"/api/workflows/{workflow_id}",
                headers=headers,
                json={"force_deactivation": True},
            )
        import asyncio

        asyncio.run(RepoStorage().delete(path))


@pytest.mark.e2e
def test_workspace_repo_changeset_registers_exact_live_source_without_rewrite(
    e2e_client, platform_admin
):
    suffix = uuid4().hex
    scope = f"test_registration_only_{suffix}"
    path = f"{scope}/workflow.py"
    function_name = f"registration_only_{suffix}"
    headers = platform_admin.headers
    workflow_id = None
    source = f'''from bifrost import workflow

@workflow(name="Registration only {suffix}")
async def {function_name}() -> dict:
    return {{"registered": True}}
'''
    source_hash = hashlib.sha256(source.encode()).hexdigest()
    try:
        written = e2e_client.put(
            "/api/files/editor/content",
            headers=headers,
            json={"path": path, "content": source, "encoding": "utf-8"},
        )
        assert written.status_code in {200, 201}, written.text
        listing = e2e_client.get("/api/workflows", headers=headers)
        assert listing.status_code == 200, listing.text
        assert not any(
            item.get("source_file_path") == path
            and item.get("function_name") == function_name
            for item in listing.json()
        )

        started = e2e_client.post(
            "/api/workspace-repo-changesets",
            headers=headers,
            json={"scope": scope},
        )
        assert started.status_code == 201, started.text
        changeset_id = started.json()["id"]
        staged = e2e_client.post(
            f"/api/workspace-repo-changesets/{changeset_id}/files",
            headers=headers,
            json={
                "path": path,
                "operation": "verify",
                "expected_hash": source_hash,
            },
        )
        assert staged.status_code == 200, staged.text
        assert staged.json()["mutations"] == [
            {
                "path": path,
                "operation": "verify",
                "content_base64": None,
                "before_hash": source_hash,
                "after_hash": source_hash,
                "force_deactivation": False,
            }
        ]

        validated = e2e_client.post(
            f"/api/workspace-repo-changesets/{changeset_id}/validate",
            headers=headers,
        )
        assert validated.status_code == 200, validated.text
        assert validated.json()["valid"] is True, validated.text
        assert validated.json()["registration_actions"][0]["action"] == "create"

        activated = e2e_client.post(
            f"/api/workspace-repo-changesets/{changeset_id}/activate",
            headers=headers,
            json={"candidate_id": validated.json()["candidate_id"]},
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["status"] == "activated"
        assert activated.json()["commit_sha"] is None
        evidence = activated.json()["validation"]["activation_evidence"]
        assert evidence["files"] == [
            {"path": path, "operation": "verify", "sha256": source_hash}
        ]
        workflow_id = activated.json()["validation"]["registration_actions"][0][
            "workflow_id"
        ]

        readback = e2e_client.post(
            "/api/files/read", headers=headers, json={"path": path}
        )
        assert readback.status_code == 200, readback.text
        assert readback.json()["content"] == source
        listing = e2e_client.get("/api/workflows", headers=headers)
        registered = next(
            item for item in listing.json() if item.get("id") == workflow_id
        )
        assert registered["source_file_path"] == path
        assert registered["function_name"] == function_name
    finally:
        if workflow_id:
            e2e_client.request(
                "DELETE",
                f"/api/workflows/{workflow_id}",
                headers=headers,
                json={"force_deactivation": True},
            )
        import asyncio

        asyncio.run(RepoStorage().delete(path))


@pytest.mark.e2e
def test_python_activation_invalidates_worker_import_generation_immediately(
    e2e_client, platform_admin
):
    """A dependent workflow must see one coherent revision after activation."""
    suffix = uuid4().hex
    scope = f"test_generation_{suffix}"
    helper_path = f"{scope}/helper.py"
    workflow_path = f"{scope}/workflow.py"
    function_name = f"generation_probe_{suffix}"
    headers = platform_admin.headers
    helper_v1 = 'def revision():\n    return "revision-a"\n'
    helper_v2 = 'def revision():\n    return "revision-b"\n'
    workflow_source = f'''from bifrost import workflow
from {scope}.helper import revision

@workflow(name="{function_name}", execution_mode="async")
async def {function_name}() -> dict:
    return {{"revision": revision()}}
'''

    try:
        helper_created = e2e_client.put(
            "/api/files/editor/content",
            headers=headers,
            json={"path": helper_path, "content": helper_v1, "encoding": "utf-8"},
        )
        assert helper_created.status_code in {200, 201}, helper_created.text
        registered = write_and_register(
            e2e_client,
            headers,
            workflow_path,
            workflow_source,
            function_name,
        )

        first = execute_workflow_sync(
            e2e_client, headers, registered["id"], max_wait=30.0
        )
        assert first["status"] == "Success", first
        assert first["result"] == {"revision": "revision-a"}
        first_generation = (first.get("execution_context") or {}).get(
            "workspace_generation"
        )
        assert first_generation

        state = e2e_client.get(
            "/api/workspace-repo-changesets/state",
            headers=headers,
            params={"scope": scope},
        )
        assert state.status_code == 200, state.text
        started = e2e_client.post(
            "/api/workspace-repo-changesets",
            headers=headers,
            json={"scope": scope, "base_revision": state.json()["revision"]},
        )
        assert started.status_code == 201, started.text
        changeset_id = started.json()["id"]
        staged = e2e_client.post(
            f"/api/workspace-repo-changesets/{changeset_id}/files",
            headers=headers,
            json={
                "path": helper_path,
                "operation": "write",
                "content_base64": base64.b64encode(helper_v2.encode()).decode(),
            },
        )
        assert staged.status_code == 200, staged.text
        validated = e2e_client.post(
            f"/api/workspace-repo-changesets/{changeset_id}/validate",
            headers=headers,
        )
        assert validated.status_code == 200, validated.text
        assert validated.json()["valid"] is True, validated.text
        activated = e2e_client.post(
            f"/api/workspace-repo-changesets/{changeset_id}/activate",
            headers=headers,
            json={"candidate_id": validated.json()["candidate_id"]},
        )
        assert activated.status_code == 200, activated.text

        second = execute_workflow_sync(
            e2e_client, headers, registered["id"], max_wait=30.0
        )
        assert second["status"] == "Success", second
        assert second["result"] == {"revision": "revision-b"}
        second_generation = (second.get("execution_context") or {}).get(
            "workspace_generation"
        )
        assert second_generation
        assert second_generation != first_generation
    finally:
        e2e_client.delete(f"/api/files/editor?path={workflow_path}", headers=headers)
        e2e_client.delete(f"/api/files/editor?path={helper_path}", headers=headers)


@pytest.mark.e2e
def test_workspace_repo_git_closure_retry_requires_authentication(e2e_client):
    response = e2e_client.post(
        f"/api/workspace-repo-changesets/{uuid4()}/retry-git-closure",
        json={"commit_message": "e2e retry", "push": True},
    )

    assert response.status_code in {401, 403}


@pytest.mark.e2e
@pytest.mark.parametrize("action", ["preview", "apply"])
def test_workspace_repo_git_convergence_requires_authentication(e2e_client, action):
    payload = {
        "changeset_ids": [str(uuid4())],
        "protected_main_source_sha": "a" * 40,
    }
    if action == "apply":
        payload.update(
            {
                "candidate_id": "sha256:" + "b" * 64,
                "commit_message": "Converge selected production history",
            }
        )
    response = e2e_client.post(
        f"/api/workspace-repo-changesets/git-convergence/{action}",
        json=payload,
    )

    assert response.status_code in {401, 403}


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_workspace_repo_git_closure_retry_is_org_scoped_and_state_guarded(
    e2e_client, platform_admin, org1, db_session
):
    admin = (
        await db_session.execute(select(User).where(User.id == platform_admin.user_id))
    ).scalar_one()
    assert admin.organization_id is not None
    cross_org = WorkspaceRepoChangeset(
        organization_id=UUID(org1["id"]),
        scope=f"test_changesets_{uuid4().hex}",
        base_revision="0" * 64,
        base_files={},
        mutations=[],
        status="activated",
        created_by=admin.id,
        failure_detail={"phase": "git_closure", "state": "failed"},
    )
    db_session.add(cross_org)
    await db_session.commit()
    try:
        scoped = e2e_client.post(
            f"/api/workspace-repo-changesets/{cross_org.id}/retry-git-closure",
            headers=platform_admin.headers,
            json={"commit_message": "e2e retry", "push": True},
        )
        assert scoped.status_code == 404, scoped.text

        started = e2e_client.post(
            "/api/workspace-repo-changesets",
            headers=platform_admin.headers,
            json={"scope": f"test_changesets_{uuid4().hex}"},
        )
        assert started.status_code == 201, started.text
        invalid = e2e_client.post(
            f"/api/workspace-repo-changesets/{started.json()['id']}/retry-git-closure",
            headers=platform_admin.headers,
            json={"commit_message": "e2e retry", "push": True},
        )
        assert invalid.status_code == 422, invalid.text
    finally:
        await db_session.execute(
            delete(WorkspaceRepoChangeset).where(
                WorkspaceRepoChangeset.id.in_([cross_org.id, started.json()["id"]])
            )
        )
        await db_session.commit()


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("closeout_branch", ["already_current", "write"])
async def test_git_closure_closeout_refreshes_durable_operation_owner(
    async_session_factory,
    platform_admin,
    closeout_branch,
):
    """A stale attempt cannot overwrite an owner installed by another session."""
    before = b"before"
    target = b"target"
    before_hash = hashlib.sha256(before).hexdigest()
    target_hash = hashlib.sha256(target).hexdigest()
    path = f"test_changesets_{uuid4().hex}/owned.txt"
    replacement_operation_id = str(uuid4())

    async with async_session_factory() as setup_db:
        admin = (
            await setup_db.execute(
                select(User).where(User.id == platform_admin.user_id)
            )
        ).scalar_one()
        assert admin.organization_id is not None
        organization_id = admin.organization_id
        row = WorkspaceRepoChangeset(
            organization_id=organization_id,
            scope=path.rsplit("/", 1)[0],
            base_revision="0" * 64,
            base_files={path: before_hash},
            mutations=[
                {
                    "path": path,
                    "operation": "write",
                    "content_base64": base64.b64encode(target).decode(),
                    "before_hash": before_hash,
                    "after_hash": target_hash,
                }
            ],
            status="activated",
            created_by=admin.id,
            failure_detail={
                "phase": "git_closure",
                "state": "failed",
                "provenance": {
                    "operator": "first-attempt",
                    "commit_message": "close workspace source",
                },
            },
        )
        setup_db.add(row)
        await setup_db.commit()
        changeset_id = row.id

    class OwnershipReplacingWriter:
        write_calls = 0

        async def replace_owner(self):
            async with async_session_factory() as replacement_db:
                replacement = (
                    await replacement_db.execute(
                        select(WorkspaceRepoChangeset)
                        .where(WorkspaceRepoChangeset.id == changeset_id)
                        .with_for_update()
                    )
                ).scalar_one()
                replacement.failure_detail = {
                    "phase": "git_closure",
                    "state": "pending",
                    "operation_id": replacement_operation_id,
                    "provenance": {
                        "operator": "replacement-attempt",
                        "commit_message": "close workspace source",
                    },
                }
                await replacement_db.commit()

        async def inspect(self, paths, *, ref=None, reachable_from=None):
            assert tuple(paths) == (path,)
            if closeout_branch == "already_current":
                await self.replace_owner()
            return PlatformCommitSnapshot(
                commit_sha="d" * 40,
                tree_sha="e" * 40,
                file_sha256={
                    path: (
                        target_hash
                        if closeout_branch == "already_current"
                        else before_hash
                    )
                },
                signature_state="VALID",
            )

        async def write(self, request):
            self.write_calls += 1
            await self.replace_owner()
            return PlatformCommitResult(
                commit_sha="a" * 40,
                tree_sha="b" * 40,
                signature_state="VALID",
            )

    writer = OwnershipReplacingWriter()
    try:
        async with async_session_factory() as attempt_db:
            service = WorkspaceRepoChangesetService(
                attempt_db,
                organization_id,
                commit_writer=writer,
            )
            result = await service.retry_git_closure(
                changeset_id,
                WorkspaceRepoActivateRequest(push=True),
                "retrying-operator",
            )

            assert result.status == "activated"
            assert result.commit_sha is None
            assert result.failure_detail["operation_id"] == replacement_operation_id

        assert writer.write_calls == (0 if closeout_branch == "already_current" else 1)
        async with async_session_factory() as observer_db:
            persisted = (
                await observer_db.execute(
                    select(WorkspaceRepoChangeset).where(
                        WorkspaceRepoChangeset.id == changeset_id
                    )
                )
            ).scalar_one()
            assert persisted.status == "activated"
            assert persisted.commit_sha is None
            assert persisted.failure_detail["operation_id"] == replacement_operation_id
    finally:
        async with async_session_factory() as cleanup_db:
            await cleanup_db.execute(
                delete(WorkspaceRepoChangeset).where(
                    WorkspaceRepoChangeset.id == changeset_id
                )
            )
            await cleanup_db.commit()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_recoverable_git_closures_include_durable_retry_states(
    e2e_client, platform_admin, db_session
):
    admin = (
        await db_session.execute(select(User).where(User.id == platform_admin.user_id))
    ).scalar_one()
    assert admin.organization_id is not None
    scope = f"test_changesets_{uuid4().hex}"
    rows = [
        WorkspaceRepoChangeset(
            organization_id=admin.organization_id,
            scope=scope,
            base_revision="0" * 64,
            base_files={},
            mutations=[],
            status="activated",
            created_by=admin.id,
            failure_detail={"phase": "git_closure", "state": state},
        )
        for state in ("failed", "not_configured", "pending")
    ]
    rows[-1].updated_at = (
        datetime.now(timezone.utc)
        - GIT_CLOSURE_PENDING_STALE_AFTER
        - timedelta(seconds=1)
    )
    db_session.add_all(rows)
    await db_session.commit()
    try:
        response = e2e_client.get(
            "/api/workspace-repo-changesets/recoverable-git-closures",
            headers=platform_admin.headers,
            params={"scope": scope},
        )

        assert response.status_code == 200, response.text
        assert {item["id"] for item in response.json()} == {
            str(row.id) for row in rows
        }
    finally:
        await db_session.execute(
            delete(WorkspaceRepoChangeset).where(
                WorkspaceRepoChangeset.id.in_([row.id for row in rows])
            )
        )
        await db_session.commit()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_workspace_repo_git_closure_retry_requires_remote_push_without_reactivation(
    e2e_client, platform_admin, db_session
):
    admin = (
        await db_session.execute(select(User).where(User.id == platform_admin.user_id))
    ).scalar_one()
    assert admin.organization_id is not None
    row = WorkspaceRepoChangeset(
        organization_id=admin.organization_id,
        scope=f"test_changesets_{uuid4().hex}",
        base_revision="0" * 64,
        base_files={},
        mutations=[],
        status="activated",
        created_by=admin.id,
        activated_revision="a" * 64,
        failure_detail={"phase": "git_closure", "state": "failed"},
    )
    db_session.add(row)
    await db_session.commit()
    try:
        retried = e2e_client.post(
            f"/api/workspace-repo-changesets/{row.id}/retry-git-closure",
            headers=platform_admin.headers,
            json={"commit_message": "e2e retry", "push": False},
        )

        assert retried.status_code == 422, retried.text
        shown = e2e_client.get(
            f"/api/workspace-repo-changesets/{row.id}",
            headers=platform_admin.headers,
        )
        assert shown.status_code == 200, shown.text
        assert shown.json()["status"] == "activated"
        assert shown.json()["activated_revision"] == "a" * 64
        assert shown.json()["failure_detail"] == {
            "phase": "git_closure",
            "state": "failed",
        }
    finally:
        await db_session.execute(
            delete(WorkspaceRepoChangeset).where(WorkspaceRepoChangeset.id == row.id)
        )
        await db_session.commit()
