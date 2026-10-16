"""Path safety helpers for local workspace Git operations."""

from pathlib import Path, PurePosixPath, PureWindowsPath


def validate_workspace_relative_path(path: str) -> str:
    """Validate one canonical repository-relative path."""
    if not isinstance(path, str) or not path:
        raise ValueError("workspace path must be a non-empty string")
    if len(path) > 4096:
        raise ValueError("workspace path is too long")
    if "\x00" in path or any(ord(character) < 32 for character in path):
        raise ValueError("workspace path cannot contain control characters")
    if "\\" in path:
        raise ValueError("workspace path must use forward slashes")

    posix_path = PurePosixPath(path)
    if posix_path.is_absolute() or PureWindowsPath(path).is_absolute():
        raise ValueError("workspace path must be relative")
    if not posix_path.parts or path in {".", ".."}:
        raise ValueError("workspace path must identify a repository entry")
    if posix_path.as_posix() != path:
        raise ValueError("workspace path must be canonical")
    if any(part in {"", ".", ".."} for part in posix_path.parts):
        raise ValueError("workspace path cannot contain traversal segments")
    if any(part.casefold() == ".git" for part in posix_path.parts):
        raise ValueError("workspace path cannot access Git internals")
    return path


def resolve_workspace_path(root: Path, path: str) -> Path:
    """Resolve a validated path and reject filesystem/symlink escapes."""
    safe_path = validate_workspace_relative_path(path)
    root_resolved = root.resolve()
    candidate = root_resolved.joinpath(*PurePosixPath(safe_path).parts)
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root_resolved):
        raise ValueError("workspace path must remain within the repository")
    git_metadata = (root_resolved / ".git").resolve(strict=False)
    if resolved == git_metadata or resolved.is_relative_to(git_metadata):
        raise ValueError("workspace path cannot access Git internals")
    # Preserve the lexical leaf so unlink removes an allowed in-repository
    # symlink itself rather than its target. The resolved form is used only as
    # the containment and Git-metadata proof above.
    return candidate
