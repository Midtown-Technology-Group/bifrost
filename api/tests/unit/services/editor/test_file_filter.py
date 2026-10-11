from src.services.editor.file_filter import is_allowed_python_module_path


def test_python_module_policy_reuses_workspace_exclusions() -> None:
    assert is_allowed_python_module_path("modules/example.py")
    assert is_allowed_python_module_path("venv.py")

    for path in (
        "venv/example.py",
        ".venv/lib/example.py",
        "node_modules/package/example.py",
        "__pycache__/example.py",
        ".git/hooks/example.py",
        "../example.py",
        "/absolute/example.py",
        r"venv\example.py",
        "modules/example.pyc",
    ):
        assert not is_allowed_python_module_path(path), path
