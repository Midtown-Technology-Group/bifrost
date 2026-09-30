"""Explicit checkout resources for CLI execution, never a server fallback."""

from __future__ import annotations

import json
import os
import stat
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterator, Mapping


class LocalResourceError(ValueError):
    """A local resource cannot be read from its declared checkout."""


def _regular_file(root: Path, relative: str) -> tuple[Path, os.stat_result]:
    path = root
    try:
        for part in relative.split("/"):
            path = path / part
            if path.is_symlink():
                raise LocalResourceError("Local resource paths cannot contain symlinks")
        path.resolve(strict=True).relative_to(root)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise LocalResourceError("Local resources require regular checkout files")
        return path, info
    except (OSError, ValueError) as exc:
        if isinstance(exc, LocalResourceError):
            raise
        raise LocalResourceError("Local resource file is missing or outside its checkout") from exc


def _read_regular(root: Path, relative: str, limit: int) -> bytes:
    path, before = _regular_file(root, relative)
    if before.st_size > limit:
        raise LocalResourceError("Local resource exceeds its byte bound")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
                raise LocalResourceError("Local resource changed during open")
            # Recheck containment and parent symlinks after opening, before read.
            _, current = _regular_file(root, relative)
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                raise LocalResourceError("Local resource changed during open")
            raw = stream.read(limit + 1)
    except OSError as exc:
        raise LocalResourceError("Local resource could not be opened") from exc
    if len(raw) > limit:
        raise LocalResourceError("Local resource exceeds its byte bound")
    return raw


@dataclass(frozen=True)
class LocalResources:
    root: Path | None
    paths: Mapping[str, str]

    def read(self, path: str) -> bytes:
        from bifrost.solution_delivery_review import (
            MAX_DEPLOYMENT_RESOURCE_BYTES, MAX_DEPLOYMENT_RESOURCES_BYTES,
        )

        if self.root is None or path not in self.paths:
            raise LocalResourceError(
                "Local resource is undeclared; pass --resource-recipe with its workflow delivery recipe"
            )
        # Recheck the entire map on each read so edits cannot exceed the total.
        total = 0
        target_sizes = []
        for source in self.paths.values():
            _, info = _regular_file(self.root, source)
            if not 0 < info.st_size <= MAX_DEPLOYMENT_RESOURCE_BYTES:
                raise LocalResourceError("Local resources require nonempty bounded files")
            total += info.st_size
            if source == self.paths[path]:
                target_sizes.append(info.st_size)
        if total > MAX_DEPLOYMENT_RESOURCES_BYTES:
            raise LocalResourceError("Local resources exceed their total byte bound")
        raw = _read_regular(self.root, self.paths[path], MAX_DEPLOYMENT_RESOURCE_BYTES)
        if not raw:
            raise LocalResourceError("Local resources require nonempty bounded files")
        if total - sum(target_sizes) + len(raw) * len(target_sizes) > MAX_DEPLOYMENT_RESOURCES_BYTES:
            raise LocalResourceError("Local resources exceed their total byte bound")
        return raw


_local_resources: ContextVar[LocalResources | None] = ContextVar("bifrost_local_resources", default=None)


def current_local_resources() -> LocalResources | None:
    return _local_resources.get()


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LocalResourceError("Local recipe contains duplicate keys")
        result[key] = value
    return result


def _load(workflow_file: Path | None, recipe_path: Path | None) -> LocalResources:
    if recipe_path is None:
        return LocalResources(None, MappingProxyType({}))
    if workflow_file is None:
        raise LocalResourceError("Local resource recipe requires an explicit workflow file")
    recipe_path = Path(os.path.abspath(recipe_path))
    root = next((candidate for candidate in recipe_path.parents if (candidate / ".git").exists()), None)
    if root is None:
        raise LocalResourceError("Local resource recipe must be inside a Git checkout")
    root = root.resolve()
    try:
        recipe_relative = recipe_path.relative_to(root).as_posix()
        source_relative = Path(os.path.abspath(workflow_file)).relative_to(root).as_posix()
        from bifrost.solution_delivery_review import ReviewedWorkflowRecipe, delivery_path

        delivery_path(recipe_relative)
        delivery_path(source_relative)
        raw = _read_regular(root, recipe_relative, 128 * 1024)
        recipe = ReviewedWorkflowRecipe.model_validate(json.loads(raw, object_pairs_hook=_unique_keys))
        if source_relative not in recipe.files.values():
            raise LocalResourceError("Local workflow is absent from the selected resource recipe")
        _regular_file(root, source_relative)
    except LocalResourceError:
        raise
    except (ValueError, TypeError) as exc:
        # Validation errors may contain resource or recipe input values.
        raise LocalResourceError("Local resource recipe is invalid") from exc
    return LocalResources(root, MappingProxyType(dict(recipe.resources)))


@contextmanager
def local_resource_context(workflow_file: Path | None, recipe_path: Path | None = None) -> Iterator[None]:
    """Bind dirty declared checkout bytes for one local run, then restore context.

    Even an empty local map prevents HTTP fallback. Recipe UUIDs never select a
    server install, and local byte reads are not deployment or runtime proof.
    """
    token = _local_resources.set(_load(workflow_file, recipe_path))
    try:
        yield
    finally:
        _local_resources.reset(token)
