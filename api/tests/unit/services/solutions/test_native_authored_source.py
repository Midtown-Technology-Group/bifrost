"""A green native runtime cannot credit omitted or stale authored components."""

import hashlib
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
import yaml
from bifrost.manifest import ManifestTable, ManifestWorkflow
from bifrost.manifest_codec import Destination
from bifrost.solution_source_closure import source_archive
from src.models.orm.tables import Table
from src.models.orm.users import Role
from src.models.orm.workflows import Workflow
from src.services.solution_deploy_obligations import solution_source_content_id
from src.services.solutions import native_authored_source as native_readback
from src.services.solutions.github_delivery_source import (
    VerifiedAuthoredSolution,
    VerifiedAuthoredSolutionFile,
)
from src.services.solutions.native_authored_source import (
    NativeAuthoredSourceMismatch,
    native_authored_install_readback,
    native_authored_metadata,
    require_native_python_closure,
)

SID = UUID("00000000-0000-0000-0000-000000000010")
WID = "00000000-0000-0000-0000-000000000011"
TID = "00000000-0000-0000-0000-000000000012"
PREFIX = "solutions/fixture"
RID = UUID("12345678-abcd-4321-abcd-123456789abc")


def authored(**changes):
    files = {
        "bifrost.solution.yaml": yaml.safe_dump({"slug": "fixture", "name": "Fixture", "version": "0.1.0",
            "repo_subpath": PREFIX, "global_repo_access": False}).encode(),
        "README.md": b"Reviewed source instructions\n",
        ".bifrost/workflows.yaml": yaml.safe_dump({"workflows": {WID: {"id": WID, "name": "probe",
            "path": "functions/probe.py", "function_name": "probe"}}}).encode(),
        ".bifrost/tables.yaml": yaml.safe_dump({"tables": {TID: {"id": TID, "name": "evidence",
            "policies": [{"$ref": "admin_bypass"}]}}}).encode(),
        "functions/probe.py": b"from modules.runtime import helper\ndef probe():\n    return helper()\n",
        "modules/runtime.py": b"def helper():\n    return 1\n",
        "shared/__init__.py": b"",
        "shared/microsoft/__init__.py": b"",
    }
    for path, value in changes.items():
        if value is None:
            files.pop(path, None)
        else:
            files[path] = value
    manifest = tuple(VerifiedAuthoredSolutionFile(path=PREFIX + "/" + path, mode="100644",
        sha256=hashlib.sha256(raw).hexdigest(), size=len(raw)) for path, raw in sorted(files.items()))
    entries = [{"path": item.path, "mode": item.mode, "sha256": item.sha256, "size": item.size} for item in manifest]
    return VerifiedAuthoredSolution(commit_sha="a" * 40, tree_sha="b" * 40, subtree_sha="c" * 40,
        solution_slug="fixture", repo_subpath=PREFIX,
        source_content_id=solution_source_content_id(solution_slug="fixture", repo_subpath=PREFIX, source_files=entries),
        source_files=manifest, files=MappingProxyType(files))


def test_authored_inventory_is_not_the_native_runtime_archive():
    source = authored()
    metadata = native_authored_metadata(source)
    runtime = {path: source.files[path] for path in ("functions/probe.py", "modules/runtime.py")}
    assert require_native_python_closure(source, runtime, {"functions/probe.py"}, has_table_bindings=True) == [
        "shared/__init__.py", "shared/microsoft/__init__.py"]
    assert len(source.file_manifest()) == 8
    assert metadata.readme == "Reviewed source instructions\n"
    assert metadata.descriptor.allow_outbound_access is False
    assert metadata.workflows[0]["id"] == WID
    assert metadata.tables[0]["policies"] == [{"$ref": "admin_bypass"}]


@pytest.mark.parametrize("change", [
    {"shared/__init__.py": b"print('new behavior')\n"},
    {"unused.py": b"NEW_BEHAVIOR = True\n"},
    {"modules/runtime.py": b"def helper():\n    return 2\n"},
])
def test_runtime_success_cannot_credit_unmapped_or_different_python(change):
    baseline = authored()
    runtime = {path: baseline.files[path] for path in ("functions/probe.py", "modules/runtime.py")}
    with pytest.raises(NativeAuthoredSourceMismatch):
        require_native_python_closure(authored(**change), runtime, {"functions/probe.py"}, has_table_bindings=True)


def test_empty_initializer_is_not_ignored_when_it_is_a_dependency():
    source = authored(**{"functions/probe.py": b"import shared\ndef probe():\n    return shared\n",
        "modules/runtime.py": None})
    with pytest.raises(NativeAuthoredSourceMismatch, match="dependency closure"):
        require_native_python_closure(source, {"functions/probe.py": source.files["functions/probe.py"]},
            {"functions/probe.py"}, has_table_bindings=True)


@pytest.mark.parametrize("path", ["src/index.tsx", "dist/index.html", ".bifrost/apps.yaml", "settings.json"])
def test_apps_and_other_components_do_not_become_workflow_source(path):
    with pytest.raises(NativeAuthoredSourceMismatch, match="different delivery component"):
        native_authored_metadata(authored(**{path: b"not runtime Python"}))


def test_exact_immutable_resource_mapping_is_required_for_authored_assets():
    source = authored(**{"config/guardrails.json": b'{"allow": false}\n'})
    with pytest.raises(NativeAuthoredSourceMismatch, match="different delivery component"):
        native_authored_metadata(source)
    metadata = native_authored_metadata(
        source, resource_paths=frozenset({"config/guardrails.json"})
    )
    assert metadata.descriptor.slug == "fixture"
    with pytest.raises(NativeAuthoredSourceMismatch, match="resource mapping"):
        native_authored_metadata(source, resource_paths=frozenset({"config/other.json"}))
    with pytest.raises(NativeAuthoredSourceMismatch, match="resource mapping"):
        native_authored_metadata(source, resource_paths=frozenset({"functions/probe.py"}))


@pytest.mark.parametrize("content", [
    b"slug: fixture\nslug: other\nname: Fixture\nrepo_subpath: solutions/fixture\n",
    b"slug: fixture\nname: Fixture\nrepo_subpath: solutions/fixture\nnew_grant: true\n",
    b"slug: fixture\nname: Fixture\nrepo_subpath: solutions/fixture\nglobal_repo_access: false\nallow_outbound_access: true\n",
    b"slug: &slug fixture\nname: *slug\nrepo_subpath: solutions/fixture\n",
])
def test_unknown_or_ambiguous_metadata_remains_unproven(content):
    with pytest.raises(NativeAuthoredSourceMismatch):
        native_authored_metadata(authored(**{"bifrost.solution.yaml": content}))


def test_missing_and_empty_readme_are_distinct():
    assert native_authored_metadata(authored(**{"README.md": None})).readme is None
    assert native_authored_metadata(authored(**{"README.md": b""})).readme == ""


def test_readme_uses_existing_text_import_semantics_without_changing_byte_evidence():
    source = authored(**{"README.md": b"one\r\ntwo\rthree\n"})
    assert native_authored_metadata(source).readme == "one\ntwo\nthree\n"
    assert next(item.sha256 for item in source.source_files if item.path.endswith("/README.md")) == hashlib.sha256(
        b"one\r\ntwo\rthree\n").hexdigest()


@pytest.mark.asyncio
async def test_stale_readme_cannot_be_credited_by_a_green_runtime():
    source = authored()
    metadata = native_authored_metadata(source)
    solution = SimpleNamespace(**metadata.descriptor.model_dump(exclude={"logo"}), readme="Earlier instructions\n", id=SID)
    db = AsyncMock()
    with pytest.raises(NativeAuthoredSourceMismatch, match="README"):
        await native_authored_install_readback(db, solution, source,
            expected_active_deployment_id=SID, expected_active_manifest_hash="sha256:" + "1" * 64)
    db.scalars.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("version", "99.0.0"), ("allow_outbound_access", True), ("allow_inbound_access", False),
    ("repo_subpath", "solutions/other"), ("git_connected", True), ("name", "Other name"),
])
async def test_descriptor_drift_does_not_allow_accounting(field, value):
    source = authored()
    metadata = native_authored_metadata(source)
    values = {**metadata.descriptor.model_dump(exclude={"logo"}), "readme": metadata.readme, "id": SID}
    values[field] = value
    db = AsyncMock()
    with pytest.raises(NativeAuthoredSourceMismatch, match="descriptor"):
        await native_authored_install_readback(db, SimpleNamespace(**values), source,
            expected_active_deployment_id=SID, expected_active_manifest_hash="sha256:" + "1" * 64)
    db.commit.assert_not_awaited()


def _installed_readback(monkeypatch, *, roles=(), role_names=None, policies=None, resources=False):
    workflow_fields = {"id": WID, "name": "probe", "path": "functions/probe.py",
        "function_name": "probe", "roles": list(roles)}
    if role_names is not None:
        workflow_fields["role_names"] = role_names
    # Actual INSTALL stores this unexpanded dictionary after policy validation.
    policies = policies if policies is not None else [{"name": "read", "actions": ["read"]}]
    table_fields = {"id": TID, "name": "evidence", "policies": policies}
    changes = {
        ".bifrost/workflows.yaml": yaml.safe_dump({"workflows": {WID: workflow_fields}}).encode(),
        ".bifrost/tables.yaml": yaml.safe_dump({"tables": {TID: table_fields}}).encode(),
    }
    resource_bytes = b'{"allow": false}\n'
    if resources:
        changes["config/guardrails.json"] = resource_bytes
    source = authored(**changes)
    resource_paths = frozenset({"config/guardrails.json"}) if resources else frozenset()
    metadata = native_authored_metadata(source, resource_paths=resource_paths)
    solution = SimpleNamespace(**metadata.descriptor.model_dump(exclude={"logo"}),
        id=SID, organization_id=None, readme=metadata.readme)
    role_ids = [] if role_names is not None else list(dict.fromkeys(UUID(role) for role in roles))
    workflow = Workflow(id=UUID(WID), solution_id=SID, organization_id=None,
        roles=[Role(id=role_id, name="Reader") for role_id in role_ids],
        **ManifestWorkflow.model_validate(workflow_fields).to_orm_values(Destination.INSTALL).direct)
    table = Table(id=UUID(TID), solution_id=SID, organization_id=None, access={"policies": policies},
        **ManifestTable.model_validate(table_fields).to_orm_values(Destination.INSTALL).direct)
    db = AsyncMock()
    # Exercise the real owned-ID mapping as well as both metadata comparisons.
    db.scalars.side_effect = [
        SimpleNamespace(all=lambda: [workflow]), SimpleNamespace(all=lambda: [workflow.id]),
        SimpleNamespace(all=lambda: [table]), SimpleNamespace(all=lambda: [table.id]),
    ]
    manifest_hash = "sha256:" + "1" * 64
    runtime = {path: source.files[path] for path in ("functions/probe.py", "modules/runtime.py")}
    deployment = SimpleNamespace(id=SID, compiled_manifest={}, resolution_map={}, dependencies=[],
        compiled_manifest_hash=manifest_hash, resolution_map_hash="sha256:" + "2" * 64)
    runtime_resources = ({"config/guardrails.json": SimpleNamespace(
        object_key=f"_solutions/{SID}/{SID}/_resources/config/guardrails.json",
        content_hash="sha256:" + hashlib.sha256(resource_bytes).hexdigest(), size_bytes=len(resource_bytes))}
        if resources else {})
    resolution = SimpleNamespace(sources={path: SimpleNamespace(content_hash="sha256:" + hashlib.sha256(raw).hexdigest())
        for path, raw in runtime.items()}, resources=runtime_resources, shared_tables={}, root_file_bindings={})
    verify_source = AsyncMock()
    repository = SimpleNamespace(get_runtime_closure=AsyncMock(return_value=deployment))
    storage = SimpleNamespace(read_source_artifact=AsyncMock(return_value=source_archive(runtime)),
        read_runtime_file=AsyncMock(side_effect=lambda path: runtime[path]),
        read_resource=AsyncMock(side_effect=lambda path, _size: resource_bytes if resources else b""))
    monkeypatch.setattr(native_readback, "SolutionSourceRevisionService",
        lambda db: SimpleNamespace(verify_current_source=verify_source))
    monkeypatch.setattr(native_readback, "SolutionDeploymentRepository", lambda db: repository)
    monkeypatch.setattr(native_readback, "SolutionDeploymentStorage", lambda *args: storage)
    monkeypatch.setattr(native_readback, "validate_runtime_closure", lambda *args, **kwargs: (None, resolution))
    monkeypatch.setattr("src.routers.tables._validate_table_policy_claim_refs", AsyncMock())
    return SimpleNamespace(source=source, solution=solution, db=db, workflow=workflow, table=table,
        manifest_hash=manifest_hash, verify_source=verify_source, storage=storage, deployment=deployment, resolution=resolution)


async def _read_installed(fixture):
    return await native_authored_install_readback(fixture.db, fixture.solution, fixture.source,
        expected_active_deployment_id=SID, expected_active_manifest_hash=fixture.manifest_hash)


@pytest.mark.asyncio
async def test_successful_readback_preserves_inline_policy_omissions(monkeypatch):
    fixture = _installed_readback(monkeypatch)
    result = await _read_installed(fixture)
    assert fixture.table.access == {"policies": [{"name": "read", "actions": ["read"]}]}
    assert result["workflow_ids"] == [WID] and result["table_ids"] == [TID]
    assert result["runtime_paths"] == ["functions/probe.py", "modules/runtime.py"]
    assert result["source_content_id"] == fixture.source.source_content_id
    assert result["evidence_id"].startswith("sha256:")
    fixture.verify_source.assert_awaited_once()
    assert fixture.storage.read_runtime_file.await_count == 2
    fixture.db.commit.assert_not_awaited()
    fixture.db.flush.assert_not_awaited()
    fixture.db.add.assert_not_called()


@pytest.mark.asyncio
async def test_authored_resource_requires_exact_active_resolution_and_storage_bytes(monkeypatch):
    fixture = _installed_readback(monkeypatch, resources=True)
    result = await _read_installed(fixture)
    assert result["resource_hashes"] == {
        "config/guardrails.json": fixture.resolution.resources["config/guardrails.json"].content_hash
    }
    fixture.storage.read_resource.assert_awaited_once_with("config/guardrails.json", len(b'{"allow": false}\n'))

    fixture.storage.read_resource.side_effect = lambda _path, _size: b'{"allow": true}\n'
    with pytest.raises(NativeAuthoredSourceMismatch, match="resource bytes"):
        await native_readback._read_native_runtime(SID, fixture.deployment, fixture.resolution)
    fixture.storage.read_resource.side_effect = lambda _path, _size: b'{"allow": false}\n'

    collected = await native_readback._read_native_runtime(SID, fixture.deployment, fixture.resolution)
    assert dict(collected.resources) == {"config/guardrails.json": b'{"allow": false}\n'}
    fixture.storage.read_resource.reset_mock()
    from dataclasses import replace
    tampered = replace(collected, resources=MappingProxyType({"config/guardrails.json": b'{"allow": true}\n'}))
    base = AsyncMock(return_value=(fixture.solution, fixture.deployment, fixture.resolution))
    monkeypatch.setattr(native_readback, "SolutionSourceRevisionService",
        lambda db: SimpleNamespace(_base=base, _registrations=AsyncMock()))
    with pytest.raises(NativeAuthoredSourceMismatch, match="resource"):
        await native_authored_install_readback(fixture.db, fixture.solution, fixture.source,
            expected_active_deployment_id=SID, expected_active_manifest_hash=fixture.manifest_hash,
            _verified_runtime=tampered)
    fixture.storage.read_resource.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "wrong_domain", "infrastructure"])
async def test_named_table_policy_resolution_classifies_only_authored_mismatches(monkeypatch, fault):
    from shared.policy_rules import PolicyRuleDomainMismatch, PolicyRuleNotFound

    fixture = _installed_readback(monkeypatch, policies=[{"$ref": "reviewed_rule"}])
    lookup = AsyncMock(return_value=None if fault == "missing" else SimpleNamespace(domain="file"))
    failure = RuntimeError("policy repository unavailable")
    if fault == "infrastructure":
        lookup.side_effect = failure
    monkeypatch.setattr("src.repositories.policy_rule.PolicyRuleRepository",
        lambda *_args, **_kwargs: SimpleNamespace(get_for_ref=lookup))
    if fault == "infrastructure":
        with pytest.raises(RuntimeError) as raised:
            await _read_installed(fixture)
        assert raised.value is failure
    else:
        with pytest.raises(NativeAuthoredSourceMismatch, match="policy reference") as raised:
            await _read_installed(fixture)
        assert isinstance(raised.value.__cause__, PolicyRuleNotFound if fault == "missing" else PolicyRuleDomainMismatch)
    lookup.assert_awaited_once_with(name="reviewed_rule", domain="table", solution_id=SID)
    assert fixture.table.access == {"policies": [{"$ref": "reviewed_rule"}]}
    fixture.db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("roles", [
    [str(RID), str(RID)], [str(RID).upper()], ["{" + str(RID) + "}"],
])
async def test_successful_readback_uses_install_role_uuid_normalization(monkeypatch, roles):
    fixture = _installed_readback(monkeypatch, roles=roles)
    result = await _read_installed(fixture)
    assert [role.id for role in fixture.workflow.roles] == [RID]
    assert result["workflow_ids"] == [WID]


@pytest.mark.asyncio
async def test_empty_role_names_override_carried_uuid_grants(monkeypatch):
    fixture = _installed_readback(monkeypatch, roles=[str(RID)], role_names=[])
    result = await _read_installed(fixture)
    assert fixture.workflow.roles == [] and result["workflow_ids"] == [WID]


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["missing_role", "extra_role", "endpoint", "table_policy", "table_schema"])
async def test_successful_runtime_cannot_credit_installed_control_drift(monkeypatch, drift):
    fixture = _installed_readback(monkeypatch, roles=[str(RID), str(RID).upper()])
    if drift == "missing_role":
        fixture.workflow.roles = []
    elif drift == "extra_role":
        fixture.workflow.roles.append(Role(id=UUID(TID), name="Unexpected grant"))
    elif drift == "endpoint":
        fixture.workflow.endpoint_enabled = True
    elif drift == "table_policy":
        fixture.table.access = {"policies": [{"name": "read", "actions": ["read", "create"]}]}
    else:
        fixture.table.schema = {"type": "object"}
    with pytest.raises(NativeAuthoredSourceMismatch, match="Installed (workflow controls|table metadata or policies)"):
        await _read_installed(fixture)
    fixture.db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", [None, "closure", "runtime_bytes", "roles", "table_policy"])
async def test_cached_bytes_require_fresh_complete_database_readback_without_remote_io(monkeypatch, drift):
    fixture = _installed_readback(monkeypatch, roles=[str(RID)])
    collected = await native_readback._read_native_runtime(SID, fixture.deployment, fixture.resolution)
    fixture.storage.read_source_artifact.reset_mock()
    fixture.storage.read_runtime_file.reset_mock()
    base = AsyncMock(return_value=(fixture.solution, fixture.deployment, fixture.resolution))
    registrations = AsyncMock()
    monkeypatch.setattr(native_readback, "SolutionSourceRevisionService",
        lambda db: SimpleNamespace(_base=base, _registrations=registrations))
    if drift == "closure":
        fixture.deployment.resolution_map_hash = "sha256:" + "3" * 64
    elif drift == "runtime_bytes":
        from dataclasses import replace
        collected = replace(collected, files=MappingProxyType({**collected.files, "modules/runtime.py": b"tampered"}))
    elif drift == "roles":
        fixture.workflow.roles = []
    elif drift == "table_policy":
        fixture.table.access = {"policies": []}
    if drift:
        with pytest.raises(NativeAuthoredSourceMismatch):
            await native_authored_install_readback(fixture.db, fixture.solution, fixture.source,
                expected_active_deployment_id=SID, expected_active_manifest_hash=fixture.manifest_hash,
                _verified_runtime=collected)
    else:
        result = await native_authored_install_readback(fixture.db, fixture.solution, fixture.source,
            expected_active_deployment_id=SID, expected_active_manifest_hash=fixture.manifest_hash,
            _verified_runtime=collected)
        assert result["workflow_ids"] == [WID]
    base.assert_awaited_once()
    registrations.assert_awaited_once()
    fixture.storage.read_source_artifact.assert_not_awaited()
    fixture.storage.read_runtime_file.assert_not_awaited()
