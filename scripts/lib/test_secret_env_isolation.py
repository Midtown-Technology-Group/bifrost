"""Regression checks for host isolation of optional test secrets."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_SCRIPT = (ROOT / "test.sh").read_text()


class TestSecretEnvironmentIsolation(unittest.TestCase):
    def test_env_file_is_delegated_to_compose_without_sourcing(self) -> None:
        self.assertNotIn('source "$BIFROST_TEST_ENV_FILE"', TEST_SCRIPT)
        self.assertNotIn("set -a", TEST_SCRIPT)
        self.assertIn('COMPOSE_ENV_FILES="$BIFROST_TEST_ENV_FILE"', TEST_SCRIPT)
        self.assertIn("export COMPOSE_ENV_FILES", TEST_SCRIPT)

    def test_client_unit_tests_run_in_isolated_container(self) -> None:
        match = re.search(
            r"^client_unit\(\) \{(?P<body>.*?)^\}",
            TEST_SCRIPT,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match)
        body = match.group("body")
        self.assertIn("client-check-runner", body)
        self.assertIn("docker compose", body)
        self.assertNotIn("cd client", body)


if __name__ == "__main__":
    unittest.main()
