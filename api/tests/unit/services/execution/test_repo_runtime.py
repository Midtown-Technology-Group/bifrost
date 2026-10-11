import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from src.services.execution import repo_runtime
from src.services.solutions.deployment_manifest import canonical_json, sha256_digest


@pytest.mark.asyncio
async def test_pin_repo_runtime_captures_verified_source_and_generation(monkeypatch):
    workflow_id = uuid4()
    content = "def run():\n    return 'safe'\n"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    workflow = SimpleNamespace(
        id=workflow_id,
        name="Safe workflow",
        function_name="run",
        path="workflows/safe.py",
    )
    source = SimpleNamespace(content=content, content_hash=content_hash)
    result = SimpleNamespace(one_or_none=lambda: (workflow, source))
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    generation = AsyncMock(side_effect=["generation-1", "generation-1"])
    monkeypatch.setattr(repo_runtime, "wait_for_workspace_generation", generation)

    pinned = await repo_runtime.pin_repo_workflow_runtime(session, workflow_id)

    assert pinned.runtime_mode == "repo-v1"
    assert pinned.source_hash == content_hash
    assert pinned.workspace_generation == "generation-1"
    assert pinned.queue_evidence()["workflow_path"] == "workflows/safe.py"


@pytest.mark.asyncio
async def test_pin_repo_runtime_rejects_index_hash_mismatch(monkeypatch):
    workflow_id = uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        name="Unsafe workflow",
        function_name="run",
        path="workflows/unsafe.py",
    )
    source = SimpleNamespace(content="poisoned()", content_hash="0" * 64)
    result = SimpleNamespace(one_or_none=lambda: (workflow, source))
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    monkeypatch.setattr(
        repo_runtime,
        "wait_for_workspace_generation",
        AsyncMock(side_effect=["generation-1", "generation-1"]),
    )

    with pytest.raises(repo_runtime.RepoRuntimeError, match="integrity mismatch"):
        await repo_runtime.pin_repo_workflow_runtime(session, workflow_id)


def test_repo_evidence_binds_current_registration_and_source():
    workflow_id = str(uuid4())
    evidence = {
        "schema_version": repo_runtime.REPO_RUNTIME_SCHEMA,
        "workflow_id": workflow_id,
        "workflow_name": "Pinned workflow",
        "workflow_function_name": "run",
        "workflow_path": "workflows/pinned.py",
        "workflow_source_hash": "a" * 64,
        "workspace_generation": "generation-1",
    }
    verified = repo_runtime.verify_repo_runtime_evidence(
        evidence,
        dict(evidence),
        sha256_digest(canonical_json(evidence)),
    )
    current = {
        "name": "Pinned workflow",
        "function_name": "run",
        "path": "workflows/pinned.py",
        "content_hash": "a" * 64,
    }

    bound = repo_runtime.workflow_data_from_repo_evidence(
        current, verified, workflow_id
    )

    assert bound["content_hash"] == "a" * 64
    assert bound["workspace_generation"] == "generation-1"


def test_repo_evidence_rejects_source_changed_after_admission():
    workflow_id = str(uuid4())
    evidence = {
        "schema_version": repo_runtime.REPO_RUNTIME_SCHEMA,
        "workflow_id": workflow_id,
        "workflow_name": "Pinned workflow",
        "workflow_function_name": "run",
        "workflow_path": "workflows/pinned.py",
        "workflow_source_hash": "a" * 64,
        "workspace_generation": "generation-1",
    }
    current = {
        "name": "Pinned workflow",
        "function_name": "run",
        "path": "workflows/pinned.py",
        "content_hash": "b" * 64,
    }

    with pytest.raises(repo_runtime.RepoRuntimeError, match="changed after"):
        repo_runtime.workflow_data_from_repo_evidence(
            current, evidence, workflow_id
        )
