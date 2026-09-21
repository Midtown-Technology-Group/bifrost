"""P4-2: install_from_repo supplies candidate_id so the lane reaches released."""
from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from src.models.contracts.solutions import SolutionRepoPreviewRequest
from src.models.orm.workspace_promotions import SolutionDeployObligation
from src.routers.solutions import install_from_repo
from src.services.solution_deploy_obligations import (
    reconcile_solution_deploy_obligation,
    solution_source_content_id,
    verify_solution_artifact,
)

_ENTRIES = [
    ("bifrost.solution.yaml", b"slug: example\nname: Example\n"),
    ("functions/main.py", b"value = 1\n"),
]
_REPO_SUBPATH = "solutions/example"


def _zip(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in entries:
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return buffer.getvalue()


def _obligation(entries: list[tuple[str, bytes]]) -> SolutionDeployObligation:
    files = [
        {
            "path": f"{_REPO_SUBPATH}/{path}",
            "mode": "100644",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }
        for path, content in entries
    ]
    return SolutionDeployObligation(
        id=uuid4(),
        source_release_id=uuid4(),
        organization_id=uuid4(),
        source_commit_sha="a" * 40,
        source_tree_sha="b" * 40,
        base_commit_sha="c" * 40,
        solution_slug="example",
        repo_subpath=_REPO_SUBPATH,
        source_subtree_sha="d" * 40,
        source_content_id=solution_source_content_id(
            solution_slug="example",
            repo_subpath=_REPO_SUBPATH,
            source_files=files,
        ),
        source_files=files,
        changed_paths={files[-1]["path"]: files[-1]["sha256"]},
        kind="solution_deploy_required",
        disposition="pending",
        declared_disposition="solution_deploy_required",
        due_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


class _FakeDB:
    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:  # noqa: ANN001
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()


async def _install_from_repo(monkeypatch, archive: bytes) -> tuple[str, bytes]:
    """Run the endpoint with a fake clone; return the enqueued candidate_id."""
    captured: dict = {}

    async def fake_clone(repo_url, work, ref=None) -> None:  # noqa: ANN001, ANN202
        Path(work).mkdir(parents=True, exist_ok=True)
        (Path(work) / "bifrost.solution.yaml").write_text("slug: example\n")

    async def fake_enqueue(db, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(
        "src.services.solutions.git_sync.clone_repo_to_dir", fake_clone
    )
    monkeypatch.setattr(
        "src.services.solutions.git_sync.resolve_repo_subpath",
        lambda work, subpath: Path(work),
    )
    monkeypatch.setattr(
        "src.services.solutions.zip_install._parse_workspace",
        lambda root: SimpleNamespace(slug="example", name="Example"),
    )
    monkeypatch.setattr(
        "src.services.solutions.zip_install.find_install",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "bifrost.commands.solution._build_deploy_zip",
        lambda root, extra_text_files: archive,
    )
    monkeypatch.setattr(
        "src.routers.solutions._enqueue_solution_deploy_job", fake_enqueue
    )
    monkeypatch.setattr(
        "src.routers.solutions.build_solution_memory_profile_key",
        lambda parsed: "test-profile",
    )

    org_id = uuid4()
    body = SolutionRepoPreviewRequest(
        repo_url="file:///tmp/fake-repo",
        repo_subpath=_REPO_SUBPATH,
        organization_id=org_id,
    )
    ctx = SimpleNamespace(db=_FakeDB(), org_id=org_id)
    user = SimpleNamespace(
        user_id=uuid4(), email="admin@example.com", name="Admin"
    )

    result = await install_from_repo(body, ctx, user)

    assert result.deploy_job_id is not None
    return str(captured["options"]["candidate_id"]), archive


async def test_install_from_repo_supplies_raw_zip_candidate_id(monkeypatch) -> None:
    archive = _zip(_ENTRIES)
    candidate_id, _ = await _install_from_repo(monkeypatch, archive)

    assert candidate_id == f"sha256:{hashlib.sha256(archive).hexdigest()}"


async def test_install_from_repo_obligation_reaches_released(monkeypatch) -> None:
    archive = _zip(_ENTRIES)
    candidate_id, _ = await _install_from_repo(monkeypatch, archive)

    record = _obligation(_ENTRIES)
    valid, reason, _ = verify_solution_artifact(
        record, candidate_id=candidate_id, artifact=archive
    )
    assert valid is True, reason

    database = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [record])),
        flush=AsyncMock(),
    )
    monkeypatch.setattr(
        "src.services.solution_deploy_obligations._runtime_and_registration_readback",
        AsyncMock(return_value=(True, None, {"runtime_files": {}})),
    )

    result = await reconcile_solution_deploy_obligation(
        database,
        solution_id=uuid4(),
        solution_slug="example",
        accountability_organization_id=record.organization_id,
        deploy_job_id=uuid4(),
        candidate_id=candidate_id,
        artifact=archive,
    )

    assert result["state"] == "released"
    assert record.disposition == "released"
    assert record.completion_evidence["artifact"]["candidate_id"] == candidate_id
