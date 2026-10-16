"""Share fresh-interpreter package discovery across API and worker callers.

A subprocess is required: pip installs can create the user site after this
process starts, and editable installs can change startup path configuration.
"""

import json
import logging
import subprocess
import sys

logger = logging.getLogger(__name__)


def get_installed_packages() -> list[dict[str, str]]:
    """Read pip's current inventory without caching runtime installations."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=json"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return json.loads(result.stdout)
    except Exception as exc:
        logger.warning("Failed to read installed package inventory: %s", exc)
        return []
