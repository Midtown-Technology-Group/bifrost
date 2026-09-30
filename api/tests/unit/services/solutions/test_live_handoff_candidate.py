"""Candidate creation uses only the verified Live dependency closure."""

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
from src.services.solutions.live_handoff_candidate import (
    WorkspaceLiveHandoffCandidateService,
)
from src.services.solutions.live_handoff_source import (
    LiveHandoffSourceError,
    source_archive,
    source_closure,
)


def test_candidate_source_closure_includes_imports_and_rejects_uncertainty():
    files = {
        "features/demo.py": b"from modules.helper import VALUE\n",
        "modules/helper.py": b"VALUE = 1\n",
        "features/unrelated.py": b"VALUE = 2\n",
    }
    closure = source_closure(files, {"features/demo.py"})
    assert set(closure) == {"features/demo.py", "modules/helper.py"}

    with pytest.raises(LiveHandoffSourceError, match="cannot be proven"):
        source_closure(
            {"features/demo.py": b"from modules.missing import VALUE\n"},
            {"features/demo.py"},
        )
    with pytest.raises(LiveHandoffSourceError, match="cannot be proven"):
        source_closure(
            {
                "features/demo.py": (
                    b"import importlib\nname = input()\nimportlib.import_module(name)\n"
                )
            },
            {"features/demo.py"},
        )


def test_candidate_archive_is_deterministic_and_contains_exact_bytes():
    files = {"modules/helper.py": b"VALUE = 1\n", "features/demo.py": b"run\n"}
    first = source_archive(files)
    assert first == source_archive(dict(reversed(list(files.items()))))
    with ZipFile(BytesIO(first)) as archive:
        assert archive.namelist() == sorted(files)
        assert {path: archive.read(path) for path in archive.namelist()} == files


@pytest.mark.parametrize(
    "sdk_import",
    [
        "from bifrost import tables as state",
        "from bifrost.tables import upsert",
        "import bifrost.tables as state",
        "from bifrost import files",
        "from bifrost.files import read",
        "import bifrost as sdk",
        "import bifrost.workflows",
        "from bifrost import *",
    ],
)
def test_empty_handoff_rejects_unbound_resources_in_imported_helpers(sdk_import):
    files = {
        "features/demo.py": b"from modules.helper import run\n",
        "modules/helper.py": f"{sdk_import}\nasync def run(): pass\n".encode(),
    }
    with pytest.raises(LiveHandoffSourceError, match="resource bindings: modules/helper.py"):
        source_closure(files, {"features/demo.py"})


def test_resource_imports_outside_selected_closure_do_not_block_handoff():
    files = {
        "features/demo.py": b"from bifrost import workflow, integrations, config\n",
        "features/unselected.py": b"from bifrost import tables\n",
    }
    assert source_closure(files, {"features/demo.py"}) == {
        "features/demo.py": files["features/demo.py"]
    }


@pytest.mark.asyncio
async def test_builder_stages_live_bytes_and_preserves_uuid_and_bounds(monkeypatch):
    from src.services.solutions import live_handoff_candidate as module

    solution_id, deployment_id, workflow_id = uuid4(), uuid4(), uuid4()
    path = "features/demo.py"
    helper = "modules/helper.py"
    content = b"from modules.helper import VALUE\n"
    helper_content = b"VALUE = 1\n"
    bounds = {
        "max_duration_seconds": 20,
        "max_external_calls": 10,
        "max_records_read": 100,
        "max_output_bytes": 4096,
    }
    release = SimpleNamespace(
        release_row_id=uuid4(),
        release_id="sha256:" + "a" * 64,
        artifact_id=uuid4(),
        governed_manifest_id="sha256:" + "b" * 64,
        registration_state_fingerprint="sha256:" + "c" * 64,
        governed_paths=(path, helper),
        source_commit_sha="1" * 40,
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
    solution = SimpleNamespace(
        status="active",
        organization_id=None,
        setup_complete=True,
        allow_outbound_access=False,
        git_connected=False,
        active_deployment_id=None,
    )
    workflow = SimpleNamespace(
        id=workflow_id,
        function_name="run",
        name="Demo",
        type="workflow",
        organization_id=None,
        timeout_seconds=30,
        execution_mode="async",
        time_saved=0,
        value=0,
        cache_ttl_seconds=0,
    )

    class Rows:
        def scalars(self):
            return self

        def all(self):
            return [workflow]

    class DB:
        async def get(self, _model, _identity):
            return solution

        async def scalar(self, _statement):
            return None

        async def execute(self, _statement):
            return Rows()

    written = {}

    class Storage:
        def __init__(self, *_args):
            self.source_artifact_key = (
                f"_solution_artifacts/{solution_id}/{deployment_id}/source.zip"
            )
            self.runtime_prefix = f"_solutions/{solution_id}/{deployment_id}/"

        async def write_source_artifact(self, raw, *, idempotent):
            assert idempotent is True
            written["archive"] = raw

        async def write_runtime_file(self, source_path, raw, *, idempotent):
            assert idempotent is True
            written[source_path] = raw

    staged = []

    class API:
        def __init__(self, _db):
            pass

        async def create_ready_draft(self, sid, creator, body):
            staged.append((sid, creator, body))

    class Preflight:
        def __init__(self, _db):
            pass

        async def inspect(self, sid, did, body):
            assert (sid, did, body) == (solution_id, deployment_id, request)
            return "review-evidence"

    monkeypatch.setattr(
        module, "active_workspace_release", AsyncMock(return_value=release)
    )
    monkeypatch.setattr(
        module.WorkspaceReleaseFileView,
        "from_release",
        lambda *_args: SimpleNamespace(
            read_many=AsyncMock(return_value={path: content, helper: helper_content})
        ),
    )
    monkeypatch.setattr(
        module,
        "require_live_workflow",
        lambda *_args: (path, {"runtime_bounds": bounds}),
    )
    monkeypatch.setattr(module, "SolutionDeploymentStorage", Storage)
    monkeypatch.setattr(module, "SolutionDeploymentAPIService", API)
    monkeypatch.setattr(module, "WorkspaceLiveHandoffPreflightService", Preflight)

    result = await WorkspaceLiveHandoffCandidateService(cast(Any, DB())).build(
        solution_id, deployment_id, uuid4(), request
    )

    assert result == "review-evidence"
    assert written[path] == content
    assert written[helper] == helper_content
    with ZipFile(BytesIO(written["archive"])) as archive:
        assert set(archive.namelist()) == {path, helper}
    _, _, body = staged[0]
    entity = body.resolution_map.resolve_workflow_id(workflow_id)
    assert entity.definition["timeout_seconds"] == 20
    assert entity.definition["runtime_bounds"] == bounds
    assert body.compiled_manifest.git.commit_sha == release.source_commit_sha
