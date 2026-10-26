"""Security boundaries for external Cowork skill bundles."""

import importlib.util
from pathlib import Path

import pytest

PACKAGER_PATH = (
    Path(__file__).parents[3]
    / ".claude"
    / "skills"
    / "bifrost-copilot-cowork-package"
    / "pack.py"
)


def _load_packager():
    spec = importlib.util.spec_from_file_location("cowork_packager", PACKAGER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _skill_source(tmp_path: Path, name: str) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test\n---\n",
        encoding="utf-8",
    )
    return source


@pytest.mark.parametrize(
    "name",
    [
        "/home/victim/.ssh",
        "../../outside",
        "nested/skill",
        "nested\\skill",
        ".",
        "UPPERCASE",
        "double--hyphen",
    ],
)
def test_load_skill_source_rejects_unsafe_frontmatter_name(
    tmp_path: Path,
    name: str,
) -> None:
    packager = _load_packager()

    with pytest.raises(RuntimeError, match="lowercase kebab-case slug"):
        packager.load_skill_source(_skill_source(tmp_path, name))


def test_load_skill_source_accepts_safe_frontmatter_name(tmp_path: Path) -> None:
    packager = _load_packager()
    source = _skill_source(tmp_path, "safe-skill-2")

    skill_dir, name, description, cleanup = packager.load_skill_source(source)

    assert skill_dir == source
    assert name == "safe-skill-2"
    assert description == "test"
    assert cleanup is None
