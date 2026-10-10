"""Offline source contract for a complete Solution package, including Apps.

This is preparation evidence, not CI, installed-control or publication proof.
The same exact file inventory can be supplied by a protected Git reader later;
this module neither imports authored Python nor performs deployment effects.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Mapping
from typing import Any, Literal
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from bifrost.solution_descriptor import SolutionDescriptor
from bifrost.workspace_release import canonical_digest

PACKAGE_RECIPE_SCHEMA = "bifrost.solution-package-delivery/v1"
MAX_PACKAGE_FILES = 1000
MAX_PACKAGE_BYTES = 10 * 1024 * 1024


def _path(value: str) -> str:
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or ":" in value
        or "\0" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("Expected a normalized package-relative path")
    return value


class ReviewedSolutionPackageRecipe(BaseModel):
    """Explicit target and complete reviewed descriptor/entity contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["bifrost.solution-package-delivery/v1"]
    solution_id: UUID
    organization_id: UUID | None
    repo_subpath: str = Field(pattern=r"^solutions/[a-z0-9]+(?:-[a-z0-9]+)*$")
    manifest_hashes: dict[str, str] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def complete_manifest_contract(self):
        if "bifrost.solution.yaml" not in self.manifest_hashes:
            raise ValueError("The descriptor must be part of the reviewed contract")
        for path, digest in self.manifest_hashes.items():
            _path(path)
            if path != "bifrost.solution.yaml" and not (
                path.startswith(".bifrost/") and path.endswith(".yaml")
            ):
                raise ValueError("Only descriptor and entity manifests are contracts")
            if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
                raise ValueError("Expected an exact reviewed manifest SHA-256")
        return self


class _UniqueLoader(yaml.SafeLoader):
    pass


def load_solution_package_recipe(raw: bytes) -> ReviewedSolutionPackageRecipe:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Package recipe contains duplicate JSON keys")
            result[key] = value
        return result

    if len(raw) > 128 * 1024:
        raise ValueError("Package recipe exceeds its byte bound")
    return ReviewedSolutionPackageRecipe.model_validate(
        json.loads(raw, object_pairs_hook=unique)
    )


def _unique_mapping(loader, node):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in result:
            raise ValueError("Package manifest contains duplicate mapping keys")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping
)


def _document(raw: bytes) -> dict[str, Any]:
    try:
        value = yaml.load(raw, Loader=_UniqueLoader)
    except (yaml.YAMLError, UnicodeError, TypeError) as exc:
        raise ValueError("Package manifest is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("Package manifest must be a mapping")
    return value


def review_solution_package_source(
    recipe_value: dict[str, Any],
    files: Mapping[str, bytes],
    file_modes: Mapping[str, str],
    *,
    source_commit_sha: str,
    source_tree_sha: str,
) -> dict[str, Any]:
    """Bind the complete authored inventory, with no App-only filtering."""
    recipe = ReviewedSolutionPackageRecipe.model_validate(recipe_value)
    for value in (source_commit_sha, source_tree_sha):
        if re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise ValueError("Expected exact source commit and tree identities")
    if not 1 <= len(files) <= MAX_PACKAGE_FILES or set(files) != set(file_modes):
        raise ValueError("Package inventory or modes are incomplete")
    if any(not isinstance(raw, bytes) for raw in files.values()):
        raise ValueError("Package inventory must contain exact bytes")
    if sum(len(raw) for raw in files.values()) > MAX_PACKAGE_BYTES:
        raise ValueError("Package source exceeds its byte bound")
    for path in files:
        _path(path)
        if file_modes[path] not in {"100644", "100755"}:
            raise ValueError("Package source must contain only regular Git files")
        if path == ".bifrost/secrets.enc" or path.startswith(".git/"):
            raise ValueError("Package source cannot carry credentials or Git internals")
        if path.endswith(".py"):
            try:
                ast.parse(files[path], filename=path)
            except SyntaxError as exc:
                raise ValueError(f"Package Python syntax is invalid: {path}") from exc
    contracts = {
        path: "sha256:" + hashlib.sha256(raw).hexdigest()
        for path, raw in files.items()
        if path == "bifrost.solution.yaml"
        or path.startswith(".bifrost/")
        and path.endswith(".yaml")
    }
    if contracts != recipe.manifest_hashes:
        raise ValueError(
            "Complete descriptor/entity contracts differ from reviewed source"
        )
    descriptor = SolutionDescriptor.model_validate(
        _document(files["bifrost.solution.yaml"])
    )
    if recipe.repo_subpath != f"solutions/{descriptor.slug}":
        raise ValueError("Source descriptor differs from the enrolled subtree")
    entities: dict[str, list[str]] = {}
    for path in sorted(contracts):
        if path == "bifrost.solution.yaml":
            continue
        document = _document(files[path])
        if "authored_manifest" in document:
            raise ValueError("Protected source cannot supply a derived App overlay")
        for kind, rows in document.items():
            if kind == "locations":
                if not isinstance(rows, list) or any(
                    not isinstance(x, str) for x in rows
                ):
                    raise ValueError(
                        "File locations must retain their complete declarations"
                    )
                entities["file_locations"] = sorted(rows)
                continue
            if not isinstance(rows, dict):
                raise ValueError("Entity declarations must be keyed mappings")
            if kind in entities:
                raise ValueError("Entity kind is declared by multiple manifests")
            identities = []
            for key, item in rows.items():
                if not isinstance(item, dict) or str(item.get("id", key)) != str(key):
                    raise ValueError("Manifest entity identity differs from its key")
                if kind == "apps":
                    if item.get("app_model") != "standalone_v2":
                        raise ValueError(
                            "Full Solution App publication requires the V2 model"
                        )
                    if "dist_files" in item or "bin_dist_files" in item:
                        raise ValueError(
                            "Protected package Apps must compile from reviewed source"
                        )
                identities.append(str(key))
            entities[kind] = sorted(identities)
    proof = {
        "schema_version": PACKAGE_RECIPE_SCHEMA,
        "solution_id": str(recipe.solution_id),
        "organization_id": str(recipe.organization_id)
        if recipe.organization_id
        else None,
        "repo_subpath": recipe.repo_subpath,
        "source_commit_sha": source_commit_sha,
        "source_tree_sha": source_tree_sha,
        "reviewed_recipe": recipe.model_dump(mode="json"),
        "source_files": [
            {
                "path": path,
                "mode": file_modes[path],
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            }
            for path, raw in sorted(files.items())
        ],
        "source_archive_sha256": hashlib.sha256(
            build_solution_package_archive(files, file_modes)
        ).hexdigest(),
        "entities": entities,
        "source_ci_verified": False,
        "installed_controls_verified": False,
        "runtime_verified": False,
    }
    proof["artifact_digest"] = canonical_digest(proof)
    return proof


def build_solution_package_archive(
    files: Mapping[str, bytes], file_modes: Mapping[str, str]
) -> bytes:
    """Preserve every authored file, including empty initializers and assets."""
    if set(files) != set(file_modes):
        raise ValueError("Package modes are incomplete")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, raw in sorted(files.items()):
            _path(path)
            if file_modes[path] not in {"100644", "100755"}:
                raise ValueError("Package source must contain only regular Git files")
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = int(file_modes[path], 8) << 16
            archive.writestr(info, raw)
    return output.getvalue()
