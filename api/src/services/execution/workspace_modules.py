"""Generation-aware workspace module isolation at execution boundaries."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkspaceModuleRefresh:
    """Evidence from the execution-start workspace coherence check."""

    generation: str
    cleared: int
    kept: int
    generation_mismatch: bool


def _workspace_module_maps(
    module_index: set[str],
) -> tuple[set[str], dict[str, str]]:
    """Map cached workspace paths to import names and namespace prefixes."""
    from src.core.module_cache_sync import get_workspace_release_context

    release_ctx = get_workspace_release_context()
    release_prefix = (
        release_ctx.runtime_storage_prefix if release_ctx is not None else None
    )
    workspace_names: set[str] = set()
    name_to_path: dict[str, str] = {}
    for path in module_index:
        logical_path = (
            path[len(release_prefix) :]
            if release_prefix and path.startswith(release_prefix)
            else path
        )
        mod_name = (
            logical_path.replace("/", ".")
            .removesuffix(".py")
            .removesuffix(".__init__")
        )
        parts = mod_name.split(".")
        for i in range(1, len(parts) + 1):
            prefix = ".".join(parts[:i])
            workspace_names.add(prefix)
        name_to_path[mod_name] = logical_path
    return workspace_names, name_to_path


def _loaded_workspace_modules(
    workspace_names: set[str],
    *,
    virtual_loader: type,
    namespace_loader: type,
) -> list[tuple[str, Any]]:
    """Classify the currently loaded modules owned by workspace source."""
    return [
        (name, module)
        for name, module in list(sys.modules.items())
        if module is not None
        and (
            isinstance(
                getattr(module, "__loader__", None),
                (virtual_loader, namespace_loader),
            )
            or name in workspace_names
        )
    ]


def _workspace_module_assessment(
    name: str,
    module: Any,
    *,
    current_generation: str,
    name_to_path: dict[str, str],
    cached_modules: dict[str, Any],
    namespace_loader: type,
) -> tuple[bool, bool]:
    """Return whether one loaded module is stale and whether its pin mismatched."""
    if getattr(module, "__workspace_generation__", None) != current_generation:
        return True, True

    cached_hash = getattr(module, "__content_hash__", None)
    if not cached_hash:
        return (
            not isinstance(getattr(module, "__loader__", None), namespace_loader),
            False,
        )

    file_path = name_to_path.get(name)
    if not file_path:
        return True, False

    cached = cached_modules.get(file_path)
    return not cached or cached.get("hash") != cached_hash, False


def _workspace_cached_modules(
    workspace_modules: list[tuple[str, Any]],
    *,
    current_generation: str,
    name_to_path: dict[str, str],
) -> dict[str, Any]:
    """Batch-fetch hashes needed to validate loaded workspace modules."""
    paths = [
        name_to_path[name]
        for name, module in workspace_modules
        if getattr(module, "__workspace_generation__", None) == current_generation
        and getattr(module, "__content_hash__", None)
        and name in name_to_path
    ]
    from src.core.module_cache_sync import get_modules_sync

    return get_modules_sync(paths)


def _full_workspace_eviction_closure(
    workspace_modules: list[tuple[str, Any]],
    stale_names: list[str],
    *,
    namespace_loader: type,
) -> list[str]:
    """Expand any stale module to the complete loaded workspace import closure."""
    modules_to_clear = (
        [name for name, _module in workspace_modules] if stale_names else []
    )
    cleared_set = set(modules_to_clear)
    loaded_names = list(sys.modules)
    for name, module in workspace_modules:
        if not isinstance(getattr(module, "__loader__", None), namespace_loader):
            continue
        if name in cleared_set:
            continue
        prefix = name + "."
        has_surviving_child = any(
            loaded.startswith(prefix) and loaded not in cleared_set
            for loaded in loaded_names
        )
        if not has_surviving_child:
            cleared_set.add(name)
            modules_to_clear.append(name)
    return modules_to_clear


def clear_workspace_modules() -> WorkspaceModuleRefresh:
    """Validate the workspace generation and evict any stale import closure."""
    from src.core.module_cache_sync import (
        get_module_index_sync,
        wait_for_workspace_generation_sync,
    )
    from src.services.execution.virtual_import import (
        NamespacePackageLoader,
        VirtualModuleLoader,
    )

    current_generation = wait_for_workspace_generation_sync()
    workspace_names, name_to_path = _workspace_module_maps(
        get_module_index_sync()
    )
    workspace_modules = _loaded_workspace_modules(
        workspace_names,
        virtual_loader=VirtualModuleLoader,
        namespace_loader=NamespacePackageLoader,
    )
    cached_modules = _workspace_cached_modules(
        workspace_modules,
        current_generation=current_generation,
        name_to_path=name_to_path,
    )

    stale_names: list[str] = []
    modules_kept = 0
    generation_mismatch = False
    for name, module in workspace_modules:
        stale, mismatched = _workspace_module_assessment(
            name,
            module,
            current_generation=current_generation,
            name_to_path=name_to_path,
            cached_modules=cached_modules,
            namespace_loader=NamespacePackageLoader,
        )
        generation_mismatch = generation_mismatch or mismatched
        if stale:
            stale_names.append(name)
        elif not isinstance(
            getattr(module, "__loader__", None), NamespacePackageLoader
        ):
            modules_kept += 1

    modules_to_clear = _full_workspace_eviction_closure(
        workspace_modules,
        stale_names,
        namespace_loader=NamespacePackageLoader,
    )
    if stale_names:
        modules_kept = 0

    for name in modules_to_clear:
        sys.modules.pop(name, None)

    if modules_to_clear or modules_kept:
        logger.debug(
            f"Workspace modules: cleared={len(modules_to_clear)} kept={modules_kept}"
            + (f" (cleared: {modules_to_clear})" if modules_to_clear else "")
        )

    return WorkspaceModuleRefresh(
        generation=current_generation,
        cleared=len(set(modules_to_clear)),
        kept=modules_kept,
        generation_mismatch=generation_mismatch,
    )
