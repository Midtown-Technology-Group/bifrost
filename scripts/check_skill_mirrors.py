#!/usr/bin/env python3
"""Host-side guard for Codex skill mirrors.

The Docker pytest runner intentionally does not mount the whole repository or a
usable .git directory, so mirror hygiene belongs in the host repository checks.
This script compares mirrors without changing files. Explicit synchronization
uses its ownership preflight before replacing generated files.
"""
from __future__ import annotations

import hashlib
import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MIRRORS = ("plugins/bifrost/skills", ".agents/skills")
SYNC_SCRIPT = REPO / "scripts" / "sync-codex-skills.sh"
PUBLIC_ROOT = REPO / "plugins" / "bifrost" / "skills"
DESCRIPTION_WORD_BUDGET = 35


def _tree_digest(root: Path) -> str:
    """Stable hash of every file path + content under root."""
    h = hashlib.sha256()
    if not root.exists():
        return h.hexdigest()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            h.update(str(path.relative_to(root)).encode())
            h.update(b"\0")
            h.update(path.read_bytes())
            h.update(b"\0")
            h.update(b"x" if path.stat().st_mode & 0o111 else b"-")
    return h.hexdigest()


def _tree_manifest(root: Path) -> dict[str, tuple[bytes, bool]]:
    if root.is_symlink():
        raise ValueError(f"linked mirror/source root: {root}")
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"linked mirror/source entry: {path}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = (path.read_bytes(), bool(path.stat().st_mode & 0o111))
    return result


def _expected_mirrors() -> dict[str, dict[str, tuple[bytes, bool]]]:
    canonical = REPO / ".claude/skills"
    local = _tree_manifest(canonical)
    public_names = set()
    # Git identifies symlink files on Windows too, where checkout may materialize
    # their target text instead of a filesystem symlink.
    listing = subprocess.check_output(["git", "ls-files", "-s", "skills"], cwd=REPO, text=True)
    for line in listing.splitlines():
        metadata, relative = line.split("\t", 1)
        if metadata.split()[0] != "120000":
            continue
        link = REPO / relative
        target = link.readlink() if link.is_symlink() else Path(link.read_text(encoding="utf-8").strip())
        source = (link.parent / target).resolve()
        if source.parent != canonical.resolve() or not (source / "SKILL.md").is_file():
            raise ValueError(f"public skill target escapes or is missing: {relative}")
        public_names.add(source.name)
    if not public_names or not local:
        raise ValueError("canonical/public skill sources are missing")
    return {".agents/skills": local,
            "plugins/bifrost/skills": {path: record for path, record in local.items()
                                      if path.split("/", 1)[0] in public_names}}


def _check_mirror_sync() -> list[str]:
    try:
        expected = _expected_mirrors()
        stale = [mirror for mirror, tree in expected.items() if _tree_manifest(REPO / mirror) != tree]
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        return [str(error)]
    if stale:
        return ["Codex skill mirrors are out of sync with .claude/skills/: " + ", ".join(stale)
                + ". Run scripts/sync-codex-skills.sh and commit the result."]
    return []


def _preflight_sync() -> list[str]:
    """Replace only unchanged tracked files or content already equal to source."""
    try:
        expected = _expected_mirrors()
        errors = []
        listing = subprocess.check_output(["git", "ls-tree", "-r", "HEAD", "--", *MIRRORS],
                                          cwd=REPO, text=True)
        tracked = {}
        for line in listing.splitlines():
            metadata, relative = line.split("\t", 1)
            mode, _, object_id = metadata.split()
            tracked[relative] = (object_id, mode == "100755")
        for mirror, tree in expected.items():
            for relative, actual in _tree_manifest(REPO / mirror).items():
                if tree.get(relative) == actual:
                    continue
                owned = tracked.get(f"{mirror}/{relative}")
                if owned and (subprocess.check_output(["git", "cat-file", "blob", owned[0]], cwd=REPO), owned[1]) == actual:
                    continue
                errors.append(f"edited or unmanaged mirror file; preserve and reconcile: {mirror}/{relative}")
        return errors
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        return [str(error)]


def _check_public_skill_names() -> list[str]:
    errors: list[str] = []
    skill_files = sorted(PUBLIC_ROOT.glob("*/SKILL.md"))
    if not skill_files:
        return [f"no public plugin skills found under {PUBLIC_ROOT}"]

    for skill_file in skill_files:
        text = skill_file.read_text(encoding="utf-8")
        match = re.search(r"^name:\s*(\S+)\s*$", text, flags=re.MULTILINE)
        if match is None:
            errors.append(f"{skill_file} has no name frontmatter")
            continue
        if match.group(1).startswith("bifrost:"):
            errors.append(
                f"{skill_file} repeats the plugin namespace in skill name "
                f"{match.group(1)!r}"
            )
    return errors


def _check_local_discovery() -> list[str]:
    """Keep auto-discovered IDs unique, concise and sourced from one root."""
    errors: list[str] = []
    names: dict[str, Path] = {}
    for root in (REPO / ".agents/skills", REPO / ".codex/skills"):
        for path in sorted(root.glob("*/SKILL.md")):
            text = path.read_text(encoding="utf-8")
            frontmatter = re.match(r"\A---\n(.*?)\n---(?:\n|$)", text, re.S)
            if not frontmatter:
                errors.append(f"{path} has no YAML frontmatter")
                continue
            name = re.search(r"^name:\s*(\S+)\s*$", frontmatter[1], re.M)
            description = re.search(r"^description:[ \t]*(.*?)(?=\n[^ \t]|\Z)", frontmatter[1], re.M | re.S)
            if not name:
                errors.append(f"{path} has no skill name")
                continue
            skill_id = name[1].strip("\"'")
            if skill_id in names:
                errors.append(f"duplicate repository skill {skill_id!r}: {names[skill_id]} and {path}")
            names[skill_id] = path
            if root.name == "skills" and root.parent.name == ".codex":
                errors.append(f"retired .codex/skills discovery copy: {path}")
            if skill_id.startswith("bifrost:"):
                errors.append(f"{path} repeats the plugin namespace in its skill name")
            if description is None or not description[1].strip():
                errors.append(f"{path} has no description")
            elif len(description[1].lstrip("|>").split()) > DESCRIPTION_WORD_BUDGET:
                errors.append(f"{path} description exceeds {DESCRIPTION_WORD_BUDGET} words")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-sync", action="store_true")
    args = parser.parse_args()
    errors = (_preflight_sync() if args.preflight_sync else
              [*_check_local_discovery(), *_check_mirror_sync(), *_check_public_skill_names()])
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Codex skill mirrors are fresh and public plugin skill names are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
