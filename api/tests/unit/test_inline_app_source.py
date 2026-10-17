"""Captured inline App builds must remain independent of mutable editor state."""

import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.services.app_bundler import BundlerService
from src.services.inline_app_source import (
    MAX_INLINE_SOURCE_BYTES,
    MAX_INLINE_SOURCE_FILES,
    InlineAppSourceSnapshot,
)


def sha(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def test_snapshot_retains_input_when_callers_change_the_original_map(tmp_path: Path) -> None:
    authored = {"app.yaml": b"scope: global\n", "pages/index.tsx": b"original"}
    snapshot = InlineAppSourceSnapshot(authored)
    authored["pages/index.tsx"] = b"later editor change"
    authored["later.tsx"] = b"not captured"
    assert snapshot.materialize(tmp_path) == ["pages/index.tsx"]
    assert (tmp_path / "pages/index.tsx").read_bytes() == b"original"
    assert not (tmp_path / "app.yaml").exists()
    assert not (tmp_path / "later.tsx").exists()
    assert snapshot.hashes() == {"app.yaml": sha(b"scope: global\n"), "pages/index.tsx": sha(b"original")}
    with pytest.raises(TypeError):
        snapshot.files["pages/index.tsx"] = b"changed"  # type: ignore[index]


@pytest.mark.parametrize("path", [
    "../escape.tsx", "/absolute.tsx", "pages//index.tsx", "pages/./index.tsx",
    "pages/../../escape.tsx", "pages\\index.tsx", "C:escape.tsx", "bad\0path",
    "node_modules/package/index.js", ".git/config", "pages/.tmp.index.tsx",
    "_entry.tsx", "__bifrost_tailwind.css",
])
def test_snapshot_rejects_unsafe_and_generated_paths(path: str) -> None:
    with pytest.raises(ValueError):
        InlineAppSourceSnapshot({"app.yaml": b"{}", path: b"source"})


def test_snapshot_requires_metadata_and_bounded_immutable_bytes() -> None:
    with pytest.raises(ValueError):
        InlineAppSourceSnapshot({"pages/index.tsx": b"source"})
    with pytest.raises(ValueError):
        InlineAppSourceSnapshot({"app.yaml": bytearray(b"{}")})  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        InlineAppSourceSnapshot({"app.yaml": b"", **{
            f"file-{i}.tsx": b"" for i in range(MAX_INLINE_SOURCE_FILES)
        }})
    with pytest.raises(ValueError):
        InlineAppSourceSnapshot({"app.yaml": b"", "large.tsx": b"x" * (MAX_INLINE_SOURCE_BYTES + 1)})


def test_snapshot_rejects_nonempty_build_tree_and_symlink_root(tmp_path: Path) -> None:
    snapshot = InlineAppSourceSnapshot({"app.yaml": b"{}", "pages/index.tsx": b"source"})
    root = tmp_path / "build"
    root.mkdir()
    (root / "existing.tsx").write_bytes(b"editor state")
    with pytest.raises(ValueError):
        snapshot.materialize(root)
    assert (root / "existing.tsx").read_bytes() == b"editor state"
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError):
        snapshot.materialize(alias)
    assert not (root / "pages").exists()


async def test_capture_build_uses_real_migrator_only_in_private_tree() -> None:
    # The SDK rescues platform-wrapped navigation imports from react-router-dom.
    # It returns transformed text; the bundler must actually write that text
    # before compiling, without changing the captured source or editor storage.
    original = (
        b'import { useNavigate } from "react-router-dom";\n'
        b'export default function Page() { useNavigate(); return null; }\n'
    )
    authored = {"app.yaml": b"scope: global\n", "pages/index.tsx": original}
    snapshot = InlineAppSourceSnapshot(authored)
    authored["pages/index.tsx"] = b"later editor source"
    bundler = BundlerService()
    materialize = AsyncMock(side_effect=AssertionError("Editor source must not be read"))
    preview_write = AsyncMock(side_effect=AssertionError("Preview must not be written"))
    live_write = AsyncMock(side_effect=AssertionError("Live must not be written"))
    compiler_inputs: dict[str, bytes] = {}

    async def esbuild(cfg: dict) -> dict:
        assert cfg["mode"] == "live"
        source_dir = Path(cfg["source_dir"])
        assert not (source_dir / "app.yaml").exists()
        compiler_inputs["pages/index.tsx"] = (source_dir / "pages/index.tsx").read_bytes()
        assert b'from "bifrost"' in compiler_inputs["pages/index.tsx"]
        assert b'from "react-router-dom"' not in compiler_inputs["pages/index.tsx"]
        (Path(cfg["out_dir"]) / "entry.js").write_bytes(b"captured build output")
        return {"success": True, "outputs": [{"path": "entry.js"}], "entry_file": "entry.js",
                "css_file": None, "duration_ms": 1, "warnings": []}

    with patch.object(bundler, "_materialize_source", new=materialize), \
         patch.object(bundler, "_generate_app_tailwind", new=AsyncMock(return_value=(False, set()))), \
         patch.object(bundler, "_run_esbuild", new=esbuild), \
         patch.object(bundler._app_storage, "write_preview_file", new=preview_write), \
         patch.object(bundler, "_write_live", new=live_write):
        result = await bundler.build("app", "apps/retained/", "capture", source_snapshot=snapshot)

    assert result.success and result.publication_files is not None
    materialize.assert_not_called()
    preview_write.assert_not_called()
    live_write.assert_not_called()
    assert snapshot.files["pages/index.tsx"] == original
    manifest = json.loads(result.publication_files["manifest.json"])
    assert manifest["source_snapshot_evidence"] == {
        "schema_version": "bifrost.inline-app-source-snapshot/v1",
        "authored_source_hashes": {"app.yaml": sha(b"scope: global\n"), "pages/index.tsx": sha(original)},
        "compiler_source_hashes": {"pages/index.tsx": sha(compiler_inputs["pages/index.tsx"])},
        "migration_changed_paths": ["pages/index.tsx"],
        "metadata_not_applied": ["app.yaml"],
    }
    assert manifest["build_evidence"]["output_hashes"] == {"entry.js": sha(b"captured build output")}


@pytest.mark.parametrize("mode,with_snapshot", [
    ("capture", False), ("preview", True), ("live", True), ("unknown", False),
])
async def test_capture_mode_cannot_accidentally_publish(mode: str, with_snapshot: bool) -> None:
    bundler = BundlerService()
    snapshot = InlineAppSourceSnapshot({"app.yaml": b"{}", "pages/index.tsx": b"source"})
    with patch.object(bundler, "_build", new=AsyncMock()) as build:
        with pytest.raises(ValueError):
            await bundler.build("app", "apps/test", mode, source_snapshot=snapshot if with_snapshot else None)  # type: ignore[arg-type]
    build.assert_not_called()
