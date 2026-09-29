"""Subprocess entry point for worker requirements setup."""

from __future__ import annotations

import json
import logging
import subprocess
import sys

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from src.services.execution.requirements_setup_result import RequirementsInstallResult

logger = logging.getLogger(__name__)


def _get_installed_packages() -> list[dict[str, str]]:
    try:
        result = subprocess.run(
            ["pip", "list", "--format=json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception as e:
        logger.warning(f"Failed to get installed packages: {e}")
    return []


def _strip_inline_comment(line: str) -> str:
    """Remove a ``#`` comment that starts after whitespace (space or tab)."""
    for index, char in enumerate(line):
        if char == "#" and index > 0 and line[index - 1] in (" ", "\t"):
            return line[:index]
    return line


def _requirement_name(line: str) -> str | None:
    """Canonical package name for one requirements line, or None if unparseable.

    Returns None (instead of raising) for lines packaging cannot parse so a
    single odd line cannot abort the whole heartbeat requirements count.
    """
    try:
        return canonicalize_name(Requirement(_strip_inline_comment(line).strip()).name)
    except (InvalidRequirement, ValueError):
        return None


def _egg_fragment_name(line: str) -> str | None:
    """Canonical name from a pip ``#egg=<name>`` fragment, if present.

    The installer feeds raw requirements content to ``pip install -r``, which
    accepts VCS and archive-URL lines (e.g.
    ``git+https://github.com/example/pkg.git#egg=sample``) that
    ``packaging.Requirement`` rejects. Matching on the egg fragment keeps
    those lines counted against the installed distributions.
    """
    _, _, fragment = line.partition("#")
    for param in fragment.split("&"):
        key, sep, value = param.partition("=")
        if sep and key.strip() == "egg":
            name = value.split("[", 1)[0].strip()
            if name:
                return canonicalize_name(name)
    return None


def _update_requirements_status(result: RequirementsInstallResult) -> None:
    from src.core.requirements_cache import get_requirements_sync
    from src.services.execution.simple_worker import _parse_requirement_lines

    content = get_requirements_sync()
    if not content:
        result.requirements_total = 0
        result.requirements_installed = 0
        return

    required: set[str] = set()
    unverifiable = 0
    for line in _parse_requirement_lines(content):
        name = _requirement_name(line) or _egg_fragment_name(line)
        if name is None:
            # No derivable package name (bare archive URL, local path, or
            # garbage): count it so the heartbeat reports unknown/incomplete
            # instead of a false-healthy total.
            unverifiable += 1
        else:
            required.add(name)
    result.requirements_total = len(required) + unverifiable

    installed = {canonicalize_name(p["name"]) for p in _get_installed_packages()}
    result.requirements_installed = len(required & installed)

    missing = required - installed
    if missing:
        logger.warning(f"[pool] Missing required packages: {', '.join(sorted(missing))}")
    if unverifiable:
        logger.warning(
            f"[pool] {unverifiable} requirement line(s) with no verifiable "
            "package name counted as not installed"
        )
    if not missing and not unverifiable:
        logger.info(f"[pool] All {result.requirements_total} required packages installed")


def run_requirements_setup() -> RequirementsInstallResult:
    from src.services.execution.simple_worker import install_requirements

    result = install_requirements()
    try:
        _update_requirements_status(result)
    except Exception as e:  # noqa: BLE001 - status counts are heartbeat-only
        logger.warning(f"Failed to check requirements status: {e}")
    return result


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    result = run_requirements_setup()
    sys.stdout.write(json.dumps(result.to_json_dict(), separators=(",", ":")))
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
