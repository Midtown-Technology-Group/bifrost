"""The debug credential bootstrap preserves existing databases and private state."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "debug.sh").read_text()
START = SOURCE.index("configure_debug_database() {")
FUNCTION = SOURCE[START:SOURCE.index("\n}\n", START) + 3]


class DebugCredentials(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = {**os.environ, "XDG_STATE_HOME": str(self.root),
                    "COMPOSE_PROJECT_NAME": "bifrost-debug-credential-fixture",
                    "FIXTURE_VOLUME_PRESENT": "1"}
        self.env.pop("POSTGRES_PASSWORD", None)
        self.env.pop("POSTGRES_PASSWORD_URLENCODED", None)
        self.secret = self.root / "bifrost/debug/bifrost-debug-credential-fixture/postgres-secret"

    def invoke(self, assertions=""):
        script = "set -euo pipefail\ndocker() { return \"$FIXTURE_VOLUME_PRESENT\"; }\n" + FUNCTION
        script += "\nconfigure_debug_database\n" + assertions
        return subprocess.run(["bash", "-c", script], env=self.env, capture_output=True, text=True, check=False)

    def test_status_dispatch_does_not_bootstrap_database_credentials(self):
        dispatch = SOURCE[SOURCE.index("\nload_env_files\n", SOURCE.index("# Dispatch")):]
        script = "set -euo pipefail\n" + FUNCTION
        script += "\ndocker() { return 0; }; load_env_files() { :; }; configure_debug_admin() { :; }\n"
        script += "cmd_status() { printf 'status-readonly'; }\n" + dispatch
        self.env["FIXTURE_VOLUME_PRESENT"] = "0"
        result = subprocess.run(["bash", "-c", script, "fixture", "status"],
                                env=self.env, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "status-readonly")
        self.assertFalse(self.secret.exists())

    def test_down_interpolation_does_not_need_or_change_database_credentials(self):
        start = SOURCE.index("cmd_down() {")
        down = SOURCE[start:SOURCE.index("\n}\n", start) + 3]
        script = "set -euo pipefail\nCOMPOSE_FILE=fixture\nprint_header() { :; }\n"
        script += "docker() { python3 -c 'import os; assert os.environ[\"POSTGRES_PASSWORD\"] == \"unused-for-teardown\"; assert os.environ[\"POSTGRES_PASSWORD_URLENCODED\"] == \"unused-for-teardown\"'; }\n"
        result = subprocess.run(["bash", "-c", script + down + "\ncmd_down\n"],
                                env=self.env, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.secret.exists())

    def test_fresh_database_generates_private_stable_credential(self):
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        value = self.secret.read_bytes()
        self.assertEqual(len(value.strip()), 64)
        self.assertEqual(self.secret.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.secret.parent.stat().st_mode & 0o777, 0o700)
        self.env["FIXTURE_VOLUME_PRESENT"] = "0"
        self.assertEqual(self.invoke().returncode, 0)
        self.assertEqual(self.secret.read_bytes(), value)

    def test_failed_generation_never_publishes_partial_credential(self):
        script = "set -euo pipefail\ndocker() { return 1; }\n"
        script += "openssl() { printf 'partial'; return 1; }\n" + FUNCTION
        result = subprocess.run(["bash", "-c", script + "\nconfigure_debug_database\n"],
                                env=self.env, capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.secret.exists())
        self.assertEqual(list(self.secret.parent.iterdir()), [])

    def test_competing_publication_never_overwrites_existing_credential(self):
        script = "set -euo pipefail\ndocker() { return 1; }\n"
        script += "openssl() { printf 'generated'; }\n"
        script += 'ln() { printf \'concurrent-existing\' > "$2"; command ln "$@"; }\n'
        result = subprocess.run(["bash", "-c", script + FUNCTION + "\nconfigure_debug_database\n"],
                                env=self.env, capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.secret.read_text(), "concurrent-existing")
        self.assertEqual(list(self.secret.parent.iterdir()), [self.secret])

    def test_existing_volume_without_credential_stops_without_creating_state(self):
        self.env["FIXTURE_VOLUME_PRESENT"] = "0"
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Existing debug database", result.stderr)
        self.assertFalse(self.secret.exists())

    def test_explicit_existing_password_is_preserved_and_url_encoded(self):
        self.env["FIXTURE_VOLUME_PRESENT"] = "0"
        self.env["POSTGRES_PASSWORD"] = "fixture@existing/value"
        result = self.invoke("python3 -c 'import os; assert os.environ[\"POSTGRES_PASSWORD_URLENCODED\"] == \"fixture%40existing%2Fvalue\"'")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.secret.exists())

    def test_symlink_parent_does_not_create_or_chmod_outside_state(self):
        outside = self.root / "outside"
        outside.mkdir(mode=0o755)
        (self.root / "bifrost").symlink_to(outside, target_is_directory=True)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(outside.stat().st_mode & 0o777, 0o755)
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlink_secret_is_not_read_or_overwritten(self):
        self.secret.parent.mkdir(parents=True)
        target = self.root / "outside"
        target.write_text("preserve")
        self.secret.symlink_to(target)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(target.read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
