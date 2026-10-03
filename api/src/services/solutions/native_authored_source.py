"""Read back authored metadata separately from an immutable workflow closure.

This is the native adapter for Solution deploy accountability. It cannot deploy,
change controls, or manufacture a legacy deploy job. Apps and other entity
families retain their own delivery contracts.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import yaml
from bifrost.manifest import ManifestTable, ManifestWorkflow
from bifrost.manifest_codec import Destination
from bifrost.solution_descriptor import SolutionDescriptor
from bifrost.solution_source_closure import source_closure
from bifrost.workspace_release import canonical_digest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.contracts.solution_deployments import (
    SolutionSourceRevisionInspectRequest,
)
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.models.orm.workflows import Workflow
from src.repositories.solution_deployments import SolutionDeploymentRepository
from src.services.solution_deploy_obligations import _effective_entity_id_map
from src.services.solutions.deployment_manifest import validate_runtime_closure
from src.services.solutions.deployment_storage import SolutionDeploymentStorage
from src.services.solutions.github_delivery_source import VerifiedAuthoredSolution
from src.services.solutions.source_revision import (
    SolutionSourceRevisionService,
    _archive_files,
)


class NativeAuthoredSourceMismatch(ValueError):
    """The intended authored definition is not the installed native definition."""


class _UniqueYaml(yaml.SafeLoader):
    pass


def _unique_mapping(loader: _UniqueYaml, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str) or key in result:
            raise NativeAuthoredSourceMismatch("Authored YAML keys are ambiguous")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_UniqueYaml.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _require_no_dropped_fields(authored: Any, canonical: Any) -> None:
    if isinstance(authored, dict):
        if not isinstance(canonical, dict) or set(authored) - set(canonical):
            raise NativeAuthoredSourceMismatch("Authored metadata contains unsupported nested fields")
        for key, value in authored.items():
            _require_no_dropped_fields(value, canonical[key])
    elif isinstance(authored, list):
        if not isinstance(canonical, list) or len(authored) != len(canonical):
            raise NativeAuthoredSourceMismatch("Authored metadata list is not represented faithfully")
        for value, converted in zip(authored, canonical, strict=True):
            _require_no_dropped_fields(value, converted)


def _yaml_document(content: bytes) -> dict[str, Any]:
    if len(content) > 512 * 1024:
        raise NativeAuthoredSourceMismatch("Authored metadata exceeds its size bound")
    try:
        # An alias/merge is not an extra source of authority for the first
        # native metadata adapter. Reject it before constructing recursive data.
        if any(isinstance(token, (yaml.AliasToken, yaml.AnchorToken)) for token in yaml.scan(content)):
            raise NativeAuthoredSourceMismatch("Authored metadata must not use YAML aliases")
        document = yaml.load(content, Loader=_UniqueYaml)
    except yaml.YAMLError as exc:
        raise NativeAuthoredSourceMismatch("Authored metadata is invalid YAML") from exc
    if not isinstance(document, dict):
        raise NativeAuthoredSourceMismatch("Authored metadata must be a mapping")
    return document


def _entries(content: bytes | None, key: str, model: type[BaseModel]) -> list[dict[str, Any]]:
    if content is None:
        return []
    document = _yaml_document(content)
    if set(document) != {key} or not isinstance(document[key], dict):
        raise NativeAuthoredSourceMismatch("Authored manifest has unsupported sections")
    allowed = set(model.model_fields)
    allowed.update(field.alias for field in model.model_fields.values() if field.alias)
    entries = []
    for identity, fields in document[key].items():
        if (not isinstance(fields, dict) or set(fields) - allowed
                or fields.get("id", identity) != identity):
            raise NativeAuthoredSourceMismatch("Authored manifest fields or identity are unsupported")
        converted = model.model_validate({**fields, "id": identity}).model_dump(mode="json", by_alias=True)
        _require_no_dropped_fields(fields, converted)
        if model is ManifestTable:
            # INSTALL validates policies but persists their authored dictionaries
            # verbatim, including absent optional fields rather than model defaults.
            converted["policies"] = fields.get("policies")
        entries.append(converted)
    return entries


@dataclass(frozen=True)
class NativeAuthoredMetadata:
    descriptor: SolutionDescriptor
    readme: str | None
    workflows: list[dict[str, Any]]
    tables: list[dict[str, Any]]


def native_authored_metadata(authored: VerifiedAuthoredSolution) -> NativeAuthoredMetadata:
    files = authored.files
    allowed = {"bifrost.solution.yaml", "README.md", ".bifrost/workflows.yaml", ".bifrost/tables.yaml"}
    if any(path not in allowed and not path.endswith(".py") for path in files):
        raise NativeAuthoredSourceMismatch("Authored files require a different delivery component")
    if "bifrost.solution.yaml" not in files:
        raise NativeAuthoredSourceMismatch("Authored Solution descriptor is missing")
    raw = _yaml_document(files["bifrost.solution.yaml"])
    if set(raw) - (set(SolutionDescriptor.model_fields) | {"global_repo_access"}):
        raise NativeAuthoredSourceMismatch("Authored descriptor has unsupported fields")
    if "global_repo_access" in raw and "allow_outbound_access" in raw:
        raise NativeAuthoredSourceMismatch("Authored descriptor repeats outbound access aliases")
    descriptor = SolutionDescriptor.model_validate(raw)
    if (descriptor.slug != authored.solution_slug or descriptor.repo_subpath != authored.repo_subpath
            or descriptor.logo is not None):
        raise NativeAuthoredSourceMismatch("Authored descriptor identity or component differs")
    readme_bytes = files.get("README.md")
    # Match existing UTF-8 text import semantics, retaining original byte hashes
    # separately in the protected authored manifest.
    readme = readme_bytes.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n") if readme_bytes is not None else None
    return NativeAuthoredMetadata(descriptor, readme,
        _entries(files.get(".bifrost/workflows.yaml"), "workflows", ManifestWorkflow),
        _entries(files.get(".bifrost/tables.yaml"), "tables", ManifestTable))


def require_native_python_closure(
    authored: VerifiedAuthoredSolution, runtime_files: dict[str, bytes],
    entry_paths: set[str], *, has_table_bindings: bool,
    has_root_file_bindings: bool = False,
) -> list[str]:
    """Unused empty initializers may be authored without becoming runtime files."""
    python = {path: content for path, content in authored.files.items() if path.endswith(".py")}
    if any(path not in python or python[path] != content for path, content in runtime_files.items()):
        raise NativeAuthoredSourceMismatch("Native runtime bytes differ from protected authored files")
    omitted = sorted(set(python) - set(runtime_files))
    if any(not path.endswith("/__init__.py") or python[path] != b"" for path in omitted):
        raise NativeAuthoredSourceMismatch("Unmapped authored Python has not been delivered")
    closure = source_closure(python, entry_paths, has_table_bindings=has_table_bindings,
        has_root_file_bindings=has_root_file_bindings)
    if set(closure) != set(runtime_files):
        raise NativeAuthoredSourceMismatch("Runtime files differ from the complete authored dependency closure")
    return omitted


async def native_authored_install_readback(
    db: AsyncSession, solution: Solution, authored: VerifiedAuthoredSolution,
    *, expected_active_deployment_id: UUID, expected_active_manifest_hash: str,
) -> dict[str, Any]:
    """Caller holds the install writer and row locks; no mutation is performed."""
    metadata = native_authored_metadata(authored)
    descriptor = metadata.descriptor.model_dump(exclude={"logo"})
    if any(getattr(solution, key) != value for key, value in descriptor.items()):
        raise NativeAuthoredSourceMismatch("Installed Solution descriptor differs from protected Git")
    if solution.readme != metadata.readme:
        raise NativeAuthoredSourceMismatch("Installed README differs from protected Git")
    request = SolutionSourceRevisionInspectRequest(
        expected_active_deployment_id=expected_active_deployment_id,
        expected_active_manifest_hash=expected_active_manifest_hash)
    await SolutionSourceRevisionService(db).verify_current_source(solution.id, request)
    deployment = await SolutionDeploymentRepository(db).get_runtime_closure(
        expected_active_deployment_id, solution.organization_id, solution.id)
    if deployment is None:
        raise NativeAuthoredSourceMismatch("Active native deployment is missing")
    _manifest, resolution = validate_runtime_closure(deployment.compiled_manifest,
        deployment.resolution_map, deployment.dependencies,
        expected_manifest_hash=expected_active_manifest_hash,
        expected_resolution_hash=deployment.resolution_map_hash)
    if resolution.resources:
        raise NativeAuthoredSourceMismatch("Authored resources require their explicit delivery mapping")
    storage = SolutionDeploymentStorage(solution.id, deployment.id)
    archive = await storage.read_source_artifact()
    runtime_files = _archive_files(archive, set(resolution.sources))
    for path, content in runtime_files.items():
        if await storage.read_runtime_file(path) != content:
            raise NativeAuthoredSourceMismatch("Immutable runtime and archive bytes differ")
    rows = list((await db.scalars(select(Workflow).options(selectinload(Workflow.roles))
        .where(Workflow.solution_id == solution.id).execution_options(populate_existing=True))).all())
    mapping = await _effective_entity_id_map(db, model=Workflow, solution_id=solution.id,
        entries=metadata.workflows, preserve_owned_ids=True)
    if {row.id for row in rows} != set(mapping.values()):
        raise NativeAuthoredSourceMismatch("Owned workflows differ from authored manifest")
    by_id = {row.id: row for row in rows}
    for entry in metadata.workflows:
        row = by_id[mapping[UUID(entry["id"])]]
        codec = ManifestWorkflow.model_validate(entry)
        if codec.role_names:
            raise NativeAuthoredSourceMismatch("Named role mappings require explicit installation evidence")
        expected = codec.to_orm_values(Destination.INSTALL).direct
        try:
            # INSTALL parses UUIDs and writes one junction row per distinct role.
            # An explicit role_names=[] overrides any carried UUID grants.
            expected_roles = set() if codec.role_names is not None else {UUID(role) for role in codec.roles}
        except ValueError as exc:
            raise NativeAuthoredSourceMismatch("Authored workflow role UUID is invalid") from exc
        if (row.organization_id != solution.organization_id
                or any(getattr(row, key) != value for key, value in expected.items())
                or {role.id for role in row.roles} != expected_roles):
            raise NativeAuthoredSourceMismatch("Installed workflow controls differ from authored manifest")
    tables = list((await db.scalars(select(Table).where(Table.solution_id == solution.id)
        .execution_options(populate_existing=True))).all())
    table_map = await _effective_entity_id_map(db, model=Table, solution_id=solution.id,
        entries=metadata.tables, preserve_owned_ids=True)
    if {row.id for row in tables} != set(table_map.values()):
        raise NativeAuthoredSourceMismatch("Owned tables differ from authored manifest")
    table_by_id = {row.id: row for row in tables}
    from shared.policies.probe import make_seed_admin_bypass
    from shared.policy_rules import resolve_policy_refs

    from src.models.contracts.policies import TablePolicies
    from src.repositories.policy_rule import PolicyRuleRepository
    from src.routers.tables import _validate_table_policy_claim_refs
    for entry in metadata.tables:
        row = table_by_id[table_map[UUID(entry["id"])]]
        codec = ManifestTable.model_validate(entry)
        expected = codec.to_orm_values(Destination.INSTALL).direct
        access = {"policies": entry["policies"]} if entry["policies"] is not None else make_seed_admin_bypass()
        policy = TablePolicies.model_validate(access)
        await _validate_table_policy_claim_refs(db, solution.organization_id, policy, solution.id)
        await resolve_policy_refs(policy.model_copy(deep=True),
            repo=PolicyRuleRepository(db, org_id=solution.organization_id, is_superuser=True),
            action_domain="table", solution_id=solution.id)
        if (row.organization_id != solution.organization_id or row.access != access
                or any(getattr(row, key) != value for key, value in expected.items())):
            raise NativeAuthoredSourceMismatch("Installed table metadata or policies differ from authored manifest")
    omitted = require_native_python_closure(authored, runtime_files,
        {row.path for row in rows}, has_table_bindings=bool(tables or resolution.shared_tables),
        has_root_file_bindings=bool(resolution.root_file_bindings))
    result = {"schema_version": "bifrost.native-solution-authored-readback/v1",
        "solution_id": str(solution.id), "organization_id": str(solution.organization_id) if solution.organization_id else None,
        "deployment_id": str(deployment.id), "manifest_hash": deployment.compiled_manifest_hash,
        "resolution_hash": deployment.resolution_map_hash, "source_content_id": authored.source_content_id,
        "source_archive_sha256": hashlib.sha256(archive).hexdigest(),
        "runtime_paths": sorted(runtime_files), "omitted_empty_initializers": omitted,
        "workflow_ids": sorted(str(row.id) for row in rows), "table_ids": sorted(str(row.id) for row in tables),
        "descriptor": descriptor,
        "readme_sha256": hashlib.sha256(metadata.readme.encode()).hexdigest() if metadata.readme is not None else None}
    result["evidence_id"] = canonical_digest(result)
    return result
