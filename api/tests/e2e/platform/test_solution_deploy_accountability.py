"""Post-deploy reconciliation clears reviewed-source obligations (MIDT-166).

Production evidence (Midtown-Technology-Group/bifrost#682): a Solution deploy
succeeded while its post-deploy source-release accountability reconciliation
failed with ``SolutionManagedWriteError``. The installed Solution was updated,
but the reviewed-source obligation stayed ``attention_required`` with null
candidate/deploy-job ids, so the Workspace release notification never cleared.

This test drives the exact production path — ``_run_deploy_job`` with the
PR #673 tracking organization and a matching reviewed-source obligation left
in the swept ``attention_required`` state — and asserts the obligation is
released with the candidate and deploy-job ids recorded.
"""
from __future__ import annotations

import hashlib
import io
import uuid
import zipfile

import pytest
from sqlalchemy import delete, select

from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.solutions import Solution
from src.models.orm.workspace_promotions import (
    SolutionDeployObligation,
    WorkspaceSourceRelease,
)
from src.services.solution_deploy_obligations import solution_source_content_id
from src.services.solutions.guard import install_solution_write_guard

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _install_write_guard():
    install_solution_write_guard()
    yield


def _bundle_files(slug: str, workflow_id: uuid.UUID) -> dict[str, str]:
    source = (
        "from bifrost import workflow\n\n"
        f'@workflow(name="{slug}")\n'
        "async def run():\n    return 'ok'\n"
    )
    return {
        "bifrost.solution.yaml": f"slug: {slug}\nname: {slug}\nversion: 1.0.0\n",
        ".bifrost/workflows.yaml": (
            f"workflows:\n  {workflow_id}:\n    id: {workflow_id}\n"
            f"    name: {slug}\n    function_name: run\n"
            "    path: workflows/main.py\n    type: workflow\n"
        ),
        "workflows/main.py": source,
    }


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


async def test_post_deploy_reconciliation_releases_matching_obligation(
    db_session,
    tmp_path,
    platform_admin,
    org1,
) -> None:
    from src.routers.solutions import _run_deploy_job

    slug = f"postdeploy-{uuid.uuid4().hex[:8]}"
    solution_id = uuid.uuid4()
    deploy_job_id = uuid.uuid4()
    release_id = uuid.uuid4()
    tracking_org_id = org1["id"]
    workflow_id = uuid.uuid4()
    files = _bundle_files(slug, workflow_id)
    artifact = _zip_bytes(files)
    candidate_id = f"sha256:{hashlib.sha256(artifact).hexdigest()}"
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

    # Stage flushes in FK dependency order: the UOW does not reliably order
    # a joint Solution + SolutionDeployJob insert in one flush.
    db_session.add(
        Solution(id=solution_id, slug=slug, name=slug, organization_id=None)
    )
    await db_session.flush()
    db_session.add(
        SolutionDeployJob(
            id=deploy_job_id, install_id=solution_id, status="queued"
        )
    )
    await db_session.flush()
    db_session.add(
        WorkspaceSourceRelease(
            id=release_id,
            organization_id=tracking_org_id,
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
    db_session.add(
        SolutionDeployObligation(
            source_release_id=release_id,
            organization_id=tracking_org_id,
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
            reason="reviewed Solution source has not reached verified production",
        )
    )
    await db_session.commit()

    zip_path = tmp_path / "solution.zip"
    zip_path.write_bytes(artifact)
    try:
        await _run_deploy_job(
            deploy_job_id,
            solution_id,
            zip_path,
            force=False,
            candidate_id=candidate_id,
            accountability_organization_id=tracking_org_id,
        )

        job = await db_session.get(SolutionDeployJob, deploy_job_id)
        assert job is not None
        assert job.status == "succeeded", job.error
        accountability = (job.result or {}).get("source_release_accountability")
        assert accountability is not None, job.result
        assert accountability["state"] == "released", accountability

        obligation = await db_session.scalar(
            select(SolutionDeployObligation).where(
                SolutionDeployObligation.organization_id == tracking_org_id,
                SolutionDeployObligation.solution_slug == slug,
            )
        )
        assert obligation is not None
        assert obligation.disposition == "released"
        assert obligation.solution_id == solution_id
        assert obligation.deploy_job_id == deploy_job_id
        assert obligation.candidate_id == candidate_id
        assert obligation.resolved_at is not None
        assert (
            obligation.completion_evidence["evidence_id"]
            == accountability["evidence_id"]
        )
    finally:
        await db_session.rollback()
        await db_session.execute(
            delete(SolutionDeployObligation).where(
                SolutionDeployObligation.organization_id == tracking_org_id,
                SolutionDeployObligation.solution_slug == slug,
            )
        )
        await db_session.execute(
            delete(WorkspaceSourceRelease).where(
                WorkspaceSourceRelease.id == release_id
            )
        )
        await db_session.execute(
            delete(SolutionDeployJob).where(
                SolutionDeployJob.id == deploy_job_id
            )
        )
        await db_session.execute(
            delete(Solution).where(Solution.id == solution_id)
        )
        await db_session.commit()
