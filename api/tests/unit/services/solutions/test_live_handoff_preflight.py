"""Checks for exact-source Live to Solution candidate inspection."""

from io import BytesIO
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4
from zipfile import ZipFile

import pytest
from src.models.contracts.solution_deployments import (
    WorkspaceLiveHandoffPreflightRequest,
)
from src.services.solutions.deployment_manifest import (
    DeploymentResolutionMap,
    RuntimeEntityDefinition,
    RuntimeSourceResolution,
)
from src.services.solutions.live_handoff_preflight import (
    WorkspaceLiveHandoffPreflightError,
    WorkspaceLiveHandoffPreflightService,
    _require_workflow_binding,
    _verify_source_archive,
)
from src.services.workspace_release_runtime import WorkspaceReleaseDescriptor


def _archive(files: list[tuple[str, bytes]]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as output:
        for path, content in files:
            output.writestr(path, content)
    return buffer.getvalue()


def test_source_archive_requires_exact_live_closure() -> None:
    expected = {"features/example.py": b"reviewed source\n"}
    _verify_source_archive(_archive(list(expected.items())), expected)

    with pytest.raises(WorkspaceLiveHandoffPreflightError, match="bytes differ"):
        _verify_source_archive(
            _archive([("features/example.py", b"later source\n")]), expected
        )
    with pytest.raises(WorkspaceLiveHandoffPreflightError, match="reviewed source"):
        _verify_source_archive(
            _archive(
                [
                    ("features/example.py", expected["features/example.py"]),
                    ("features/unreviewed.py", b"new code\n"),
                ]
            ),
            expected,
        )


def test_workflow_binding_keeps_uuid_scope_and_runtime_metadata() -> None:
    workflow_id = uuid4()
    organization_id = uuid4()
    solution_id = uuid4()
    path = "features/example.py"
    source_hash = "a" * 64
    workflow = SimpleNamespace(
        id=workflow_id,
        solution_id=None,
        organization_id=organization_id,
        path=path,
        function_name="run",
        name="Example",
        type="workflow",
        is_active=True,
        endpoint_enabled=False,
        public_endpoint=False,
        api_key_enabled=False,
        access_level="role_based",
        roles=[],
        timeout_seconds=30,
        execution_mode="async",
    )
    solution = SimpleNamespace(id=solution_id, organization_id=organization_id)
    release = WorkspaceReleaseDescriptor(
        release_row_id=uuid4(),
        artifact_id=uuid4(),
        organization_id=organization_id,
        release_id="sha256:" + "b" * 64,
        effective_manifest_id="sha256:" + "c" * 64,
        runtime_storage_prefix="_workspace_releases/test/files/",
        source_hashes={path: source_hash},
        governed_paths=(path,),
        governed_manifest_id="sha256:" + "d" * 64,
        effective_registrations={
            f"{path}::run": {
                "workflow_id": str(workflow_id),
                "path": path,
                "function": "run",
                "name": "Example",
                "type": "workflow",
                "source_sha256": source_hash,
                "organization_id": str(organization_id),
                "is_active": True,
                "endpoint_enabled": False,
                "public_endpoint": False,
                "api_key_enabled": False,
                "access_level": "role_based",
                "role_ids": [],
            }
        },
        effective_registration_manifest_id="sha256:" + "e" * 64,
        source_commit_sha="1" * 40,
        source_tree_sha="2" * 40,
        registration_state_fingerprint="sha256:" + "f" * 64,
    )
    definition = {
        "path": path,
        "function_name": "run",
        "name": "Example",
        "type": "workflow",
        "organization_id": str(organization_id),
        "timeout_seconds": 30,
        "execution_mode": "async",
    }

    def resolution(source_hash_value: str) -> DeploymentResolutionMap:
        return DeploymentResolutionMap(
            workflows={
                "example": RuntimeEntityDefinition(
                    portable_ref="example",
                    resolved_id=workflow_id,
                    definition=definition,
                    source_ref=path,
                    source_hash=source_hash_value,
                )
            },
            sources={
                path: RuntimeSourceResolution(
                    object_key=f"_solutions/{solution_id}/candidate/{path}",
                    content_hash=source_hash_value,
                )
            },
        )

    _require_workflow_binding(
        cast(Any, workflow),
        cast(Any, solution),
        release,
        resolution("sha256:" + source_hash),
    )
    with pytest.raises(WorkspaceLiveHandoffPreflightError, match="source differs"):
        _require_workflow_binding(
            cast(Any, workflow),
            cast(Any, solution),
            release,
            resolution("sha256:" + "0" * 64),
        )
    workflow.endpoint_enabled = True
    with pytest.raises(WorkspaceLiveHandoffPreflightError, match="not bound"):
        _require_workflow_binding(
            cast(Any, workflow),
            cast(Any, solution),
            release,
            resolution("sha256:" + source_hash),
        )


@pytest.mark.asyncio
async def test_preflight_compares_stored_runtime_bytes_before_returning_evidence(
    monkeypatch,
) -> None:
    from src.services.solutions import live_handoff_preflight as module

    solution_id = uuid4()
    deployment_id = uuid4()
    workflow_id = uuid4()
    path = "features/example.py"
    content = b"reviewed source\n"
    from src.services.solutions.deployment_manifest import sha256_digest

    runtime_prefix = f"_solutions/{solution_id}/{deployment_id}/"
    source_key = f"_solution_artifacts/{solution_id}/{deployment_id}/source.zip"
    release = SimpleNamespace(
        release_row_id=uuid4(),
        artifact_id=uuid4(),
        release_id="sha256:" + "a" * 64,
        governed_manifest_id="sha256:" + "b" * 64,
        registration_state_fingerprint="sha256:" + "c" * 64,
        governed_paths=(path,),
        source_hashes={path: sha256_digest(content).removeprefix("sha256:")},
    )
    request = WorkspaceLiveHandoffPreflightRequest(
        expected_release_row_id=release.release_row_id,
        expected_release_id=release.release_id,
        expected_artifact_id=release.artifact_id,
        expected_governed_manifest_id=release.governed_manifest_id,
        expected_registration_state_fingerprint=release.registration_state_fingerprint,
        expected_active_deployment_id=None,
        workflow_ids=[workflow_id],
    )
    manifest = SimpleNamespace(
        source=SimpleNamespace(artifact_key=source_key, runtime_prefix=runtime_prefix),
        canonical_bytes=lambda: b"{}",
    )
    resolution = SimpleNamespace(
        sources={
            path: RuntimeSourceResolution(
                object_key=f"{runtime_prefix}{path}",
                content_hash=sha256_digest(content),
            )
        },
        workflows={},
    )
    deployment = SimpleNamespace(
        state="ready",
        base_deployment_id=None,
        compiled_manifest={},
        resolution_map={},
        dependencies=[],
        compiled_manifest_hash="sha256:" + "d" * 64,
        resolution_map_hash="sha256:" + "e" * 64,
        source_artifact_key=source_key,
        runtime_storage_prefix=runtime_prefix,
    )
    solution = SimpleNamespace(
        status="active",
        setup_complete=True,
        allow_outbound_access=False,
        organization_id=uuid4(),
        active_deployment_id=None,
    )

    class Rows:
        def all(self):
            return [SimpleNamespace(id=workflow_id)]

        def scalars(self):
            return self

    class DB:
        async def get(self, _model, _id):
            return solution

        async def execute(self, _statement):
            return Rows()

        async def scalars(self, _statement):
            return SimpleNamespace(all=list)

    class Repository:
        def __init__(self, _db):
            pass

        async def get_runtime_closure(self, *_args):
            return deployment

    class Storage:
        def __init__(self, *_args):
            self.source_artifact_key = source_key
            self.runtime_prefix = runtime_prefix
            self.runtime_bytes = content

        async def read_compiled_manifest(self):
            return b"{}"

        async def read_runtime_file(self, _path):
            return self.runtime_bytes

        async def read_source_artifact(self):
            return _archive([(path, content)])

    storage = Storage()
    monkeypatch.setattr(
        module, "active_workspace_release", AsyncMock(return_value=release)
    )
    monkeypatch.setattr(module, "SolutionDeploymentRepository", Repository)
    monkeypatch.setattr(module, "SolutionDeploymentStorage", lambda *_args: storage)
    monkeypatch.setattr(
        module,
        "validate_runtime_closure",
        lambda *_args, **_kwargs: (manifest, resolution),
    )
    monkeypatch.setattr(module, "_require_workflow_binding", lambda *_args: None)
    monkeypatch.setattr(
        module.WorkspaceReleaseFileView,
        "from_release",
        lambda *_args: SimpleNamespace(
            read_many=AsyncMock(return_value={path: content})
        ),
    )

    response = await WorkspaceLiveHandoffPreflightService(cast(Any, DB())).inspect(
        solution_id, deployment_id, request
    )
    assert response.workflow_ids == [workflow_id]
    assert response.verified_source_paths == [path]
    assert response.evidence_id.startswith("sha256:")

    storage.runtime_bytes = b"unreviewed source\n"
    with pytest.raises(WorkspaceLiveHandoffPreflightError, match="runtime bytes"):
        await WorkspaceLiveHandoffPreflightService(cast(Any, DB())).inspect(
            solution_id, deployment_id, request
        )
