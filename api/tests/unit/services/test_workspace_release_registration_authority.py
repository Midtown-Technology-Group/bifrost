"""Global Live owns governed workflow registration mutations."""

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock
from uuid import uuid4

import pytest

from src.services import workspace_release_registration_authority as authority


def _release():
    workflow_id = uuid4()
    return SimpleNamespace(
        release_id="sha256:" + "a" * 64,
        governed_paths=("features/live.py",),
        effective_registrations={
            "features/live.py::run": {
                "workflow_id": str(workflow_id),
            }
        },
    ), workflow_id


@pytest.mark.asyncio
async def test_external_guard_serializes_and_blocks_governed_path(monkeypatch):
    release, _ = _release()
    acquire = AsyncMock()
    monkeypatch.setattr(authority, "acquire_workspace_release_lock", acquire)
    monkeypatch.setattr(
        authority,
        "global_active_workspace_release_descriptor",
        AsyncMock(return_value=release),
    )

    with pytest.raises(authority.WorkspaceReleaseRegistrationGoverned) as exc:
        await authority.guard_workspace_registration_mutation(
            SimpleNamespace(),
            operation="register workflow",
            paths=("/features\\live.py",),
        )

    acquire.assert_awaited_once_with(ANY, None)
    assert exc.value.reference == "features/live.py"
    assert exc.value.release_id == release.release_id


@pytest.mark.asyncio
@pytest.mark.parametrize("match", ["id", "key"])
async def test_external_guard_blocks_effective_registration_after_row_drift(
    monkeypatch, match
):
    release, workflow_id = _release()
    monkeypatch.setattr(authority, "acquire_workspace_release_lock", AsyncMock())
    monkeypatch.setattr(
        authority,
        "global_active_workspace_release_descriptor",
        AsyncMock(return_value=release),
    )
    workflow = SimpleNamespace(
        id=workflow_id if match == "id" else uuid4(),
        path="features/renamed.py" if match == "id" else "features/live.py",
        function_name="renamed" if match == "id" else "run",
    )

    with pytest.raises(authority.WorkspaceReleaseRegistrationGoverned):
        await authority.guard_workspace_registration_mutation(
            SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))),
            operation="update workflow",
            workflows=(workflow,),
        )


@pytest.mark.asyncio
async def test_external_guard_allows_ungoverned_registration(monkeypatch):
    release, _ = _release()
    monkeypatch.setattr(authority, "acquire_workspace_release_lock", AsyncMock())
    monkeypatch.setattr(
        authority,
        "global_active_workspace_release_descriptor",
        AsyncMock(return_value=release),
    )

    await authority.guard_workspace_registration_mutation(
        SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))),
        operation="register workflow",
        paths=("features/legacy.py",),
        workflows=(
            SimpleNamespace(
                id=uuid4(),
                path="features/legacy.py",
                function_name="run",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_release_activation_authority_is_explicit_internal_bypass(monkeypatch):
    acquire = AsyncMock()
    load_release = AsyncMock()
    monkeypatch.setattr(authority, "acquire_workspace_release_lock", acquire)
    monkeypatch.setattr(
        authority, "global_active_workspace_release_descriptor", load_release
    )

    await authority.guard_workspace_registration_mutation(
        SimpleNamespace(),
        operation="activate reviewed release",
        paths=("features/live.py",),
        authority=authority.WorkspaceRegistrationMutationAuthority.RELEASE_ACTIVATION,
    )

    acquire.assert_not_awaited()
    load_release.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_authority", list(authority.WorkspaceRegistrationMutationAuthority))
@pytest.mark.parametrize("marker", [{}, {"release_id": "sha256:" + "a" * 64}])
async def test_stored_terminal_registration_blocks_every_authority_before_live_lookup(
    monkeypatch, mutation_authority, marker,
):
    retired = SimpleNamespace(id=uuid4(), path="features\\obsolete.py", function_name="run",
        organization_id=None, retirement_evidence=marker)
    # Caller metadata may be stale or omit the marker. Stored UUID state is authoritative.
    supplied = SimpleNamespace(id=retired.id, path="features/repointed.py", function_name="new",
        retirement_evidence=None)
    execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: retired))
    acquire = AsyncMock()
    load_release = AsyncMock(return_value=None)
    monkeypatch.setattr(authority, "acquire_workspace_release_lock", acquire)
    monkeypatch.setattr(authority, "global_active_workspace_release_descriptor", load_release)

    with pytest.raises(authority.WorkflowRegistrationRetired, match="permanently retired"):
        await authority.guard_workspace_registration_mutation(SimpleNamespace(execute=execute),
            operation="reactivate registration", workflows=(supplied,), authority=mutation_authority)

    load_release.assert_not_awaited()
    assert execute.await_args.args[0].get_execution_options()["autoflush"] is False
    if mutation_authority is authority.WorkspaceRegistrationMutationAuthority.EXTERNAL:
        acquire.assert_awaited_once()
    else:
        acquire.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_guard_checks_exact_native_identity_before_reminting_uuid(monkeypatch):
    retired = SimpleNamespace(id=uuid4(), path="features\\obsolete.py", function_name="run",
        organization_id=uuid4(), retirement_evidence={})
    supplied = SimpleNamespace(id=uuid4(), path=retired.path, function_name=retired.function_name,
        organization_id=retired.organization_id)
    execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: retired))
    with pytest.raises(authority.WorkflowRegistrationRetired):
        await authority.guard_workspace_registration_mutation(SimpleNamespace(execute=execute),
            operation="register new UUID", workflows=(supplied,),
            authority=authority.WorkspaceRegistrationMutationAuthority.RELEASE_ACTIVATION)

    statement = execute.await_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "workflows.retirement_evidence IS NOT NULL" in sql
    assert "workflows.path = 'features\\obsolete.py'" in sql
    assert "workflows.function_name = 'run'" in sql
    assert str(retired.organization_id).replace("-", "") in sql.replace("-", "")
    assert "NOT (EXISTS" in sql


@pytest.mark.asyncio
async def test_terminal_guard_allows_distinct_canonical_duplicate_survivor_without_live(monkeypatch):
    acquire = AsyncMock()
    load_release = AsyncMock(return_value=None)
    monkeypatch.setattr(authority, "acquire_workspace_release_lock", acquire)
    monkeypatch.setattr(authority, "global_active_workspace_release_descriptor", load_release)
    # The retired registration retains the backslash raw path and a different UUID.
    survivor = SimpleNamespace(id=uuid4(), path="features/obsolete.py", function_name="run",
        organization_id=None)
    execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))
    await authority.guard_workspace_registration_mutation(SimpleNamespace(execute=execute),
        operation="update surviving registration", workflows=(survivor,))
    sql = str(execute.await_args.args[0].compile(compile_kwargs={"literal_binds": True}))
    assert "workflows.path = 'features/obsolete.py'" in sql
    assert "workflows.organization_id IS NULL" in sql
    assert "NOT (EXISTS" in sql
    assert "workflows_1.function_name = 'run'" in sql
    assert "workflows_1.organization_id IS NULL" in sql
    assert str(survivor.id).replace("-", "") in sql.replace("-", "")
    load_release.assert_awaited_once()


@pytest.mark.asyncio
async def test_plain_string_authority_cannot_skip_fence_or_terminal_guard(monkeypatch):
    acquire = AsyncMock()
    monkeypatch.setattr(authority, "acquire_workspace_release_lock", acquire)
    with pytest.raises(ValueError, match="unsupported"):
        await authority.guard_workspace_registration_mutation(SimpleNamespace(),
            operation="mutate", authority="external")
    acquire.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate_path", [
    "features/obsolete.py", "/features/obsolete.py", "\\features\\obsolete.py",
])
async def test_terminal_guard_rejects_fresh_uuid_alias_remint(monkeypatch, candidate_path):
    retired = SimpleNamespace(id=uuid4(), path="features\\obsolete.py", function_name="run",
        organization_id=None, retirement_evidence={})
    fresh = SimpleNamespace(id=uuid4(), path=candidate_path, function_name="run", organization_id=None)
    execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: retired))
    with pytest.raises(authority.WorkflowRegistrationRetired):
        await authority.guard_workspace_registration_mutation(SimpleNamespace(execute=execute),
            operation="register fresh alias", workflows=(fresh,),
            authority=authority.WorkspaceRegistrationMutationAuthority.RELEASE_ACTIVATION)
    sql = str(execute.await_args.args[0].compile(compile_kwargs={"literal_binds": True}))
    assert "ltrim(replace(workflows.path," in sql and "= 'features/obsolete.py'" in sql
    assert "workflows.function_name = 'run'" in sql and "workflows.organization_id IS NULL" in sql
    assert "NOT (EXISTS" in sql


@pytest.mark.asyncio
async def test_distinct_solution_registration_is_not_a_root_alias_remint():
    managed = SimpleNamespace(id=uuid4(), solution_id=uuid4(), organization_id=None,
        path="features/obsolete.py", function_name="run")
    execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))
    await authority.guard_workspace_registration_mutation(SimpleNamespace(execute=execute),
        operation="reviewed Solution registration", workflows=(managed,),
        authority=authority.WorkspaceRegistrationMutationAuthority.RELEASE_ACTIVATION)
    sql = str(execute.await_args.args[0].compile(compile_kwargs={"literal_binds": True}))
    assert "workflows.id IN" in sql
    assert "workflows.path =" not in sql and "replace(" not in sql
