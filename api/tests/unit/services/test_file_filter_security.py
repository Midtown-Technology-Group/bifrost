import pytest
from src.services.editor.file_filter import (
    is_excluded_path,
    validate_workspace_path,
)


@pytest.mark.parametrize(
    "path",
    [
        ".git/HEAD",
        ".git/objects/aa/bb",
        "nested/.git/config",
    ],
)
def test_repository_metadata_is_excluded(path):
    assert is_excluded_path(path) is True
    with pytest.raises(ValueError, match="excluded"):
        validate_workspace_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "../outside.py",
        "modules/../outside.py",
        "/absolute.py",
        "C:\\absolute.py",
        "modules//ambiguous.py",
        "modules/./ambiguous.py",
    ],
)
def test_ambiguous_workspace_paths_are_rejected(path):
    with pytest.raises(ValueError):
        validate_workspace_path(path)


def test_workspace_prefix_and_file_paths_are_canonicalized():
    assert validate_workspace_path("", allow_empty=True) == ""
    assert (
        validate_workspace_path(
            "workflows/",
            allow_directory_prefix=True,
        )
        == "workflows/"
    )
    assert validate_workspace_path("workflows/run.py") == "workflows/run.py"
