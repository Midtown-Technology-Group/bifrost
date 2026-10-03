"""Hosted-only custody characterization using synthetic credentials.

Runs the actual source conductor's credential commands; never invoke this on
the physical Proxmox host. No product modules, database or network are used.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "workflow-sql-source.sh"
SYNTHETIC_TOKEN = "synthetic_action_token_for_custody_tests"


class CredentialCustodyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sql-credential-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runner = self.root / "runner"
        self.runner.mkdir(mode=0o700)
        self.output = self.root / "step-output"
        # Do not inherit real workflow credentials into synthetic tests.
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "LANG", "LC_ALL"}
        }
        self.environment.update(
            {
                "RUNNER_TEMP": str(self.runner),
                "GITHUB_RUN_ID": "101",
                "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_OUTPUT": str(self.output),
            }
        )
        self.directory = self.runner / "workflow-sql-action-credential-101-1"
        self.token_file = self.directory / "token"

    def run_command(self, mode, *, materialize=False):
        environment = self.environment.copy()
        if materialize:
            environment["GITHUB_TOKEN"] = SYNTHETIC_TOKEN
        result = subprocess.run(
            ["bash", str(SCRIPT), mode],
            cwd=self.root,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertNotIn(SYNTHETIC_TOKEN, result.stdout + result.stderr)
        return result

    def materialize(self):
        result = self.run_command("credential-materialize", materialize=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.output.read_text()
        self.assertNotIn(SYNTHETIC_TOKEN, output)
        fields = dict(line.split("=", 1) for line in output.splitlines())
        self.environment.update(
            {
                "BIFROST_ACTION_PIN_TOKEN_FILE": fields["path"],
                "SQL_SOURCE_ACTION_CREDENTIAL_IDENTITY": fields["identity"],
            }
        )
        return json.loads(fields["identity"])

    def test_private_materialization_and_no_start_cleanup(self):
        identity = self.materialize()
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.token_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.token_file.stat().st_uid, os.getuid())
        self.assertEqual(identity["file"]["ino"], self.token_file.stat().st_ino)
        self.assertEqual(self.token_file.read_text(), SYNTHETIC_TOKEN)
        result = self.run_command("credential-cleanup")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.directory.exists())
        # Workflow fallback after successful conductor disposal is idempotent.
        self.assertEqual(self.run_command("credential-cleanup").returncode, 0)

    def test_partial_output_failure_removes_created_credential(self):
        self.output.mkdir()
        result = self.run_command("credential-materialize", materialize=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.directory.exists())
        self.assertIn("action credential custody failed", result.stderr)

    def test_existing_directory_is_not_removed(self):
        self.directory.mkdir(mode=0o700)
        foreign = self.directory / "foreign"
        foreign.write_text("fixture-owned replacement")
        result = self.run_command("credential-materialize", materialize=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(foreign.exists())

    def test_file_identity_replacement_stops_without_deletion(self):
        self.materialize()
        original = self.root / "held-original"
        self.token_file.rename(original)
        self.token_file.write_text("fixture-owned replacement")
        self.token_file.chmod(0o600)
        self.assertNotEqual(original.stat().st_ino, self.token_file.stat().st_ino)
        result = self.run_command("credential-cleanup")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.token_file.exists())
        self.assertTrue(original.exists())

    def test_directory_identity_replacement_stops_without_deletion(self):
        self.materialize()
        original = self.runner / "held-original-directory"
        self.directory.rename(original)
        self.directory.mkdir(mode=0o700)
        replacement = self.directory / "foreign"
        replacement.write_text("fixture-owned replacement")
        result = self.run_command("credential-cleanup")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(replacement.exists())
        self.assertTrue((original / "token").exists())

    def test_early_conductor_failure_disposes_credential(self):
        self.materialize()
        # Cwd has no Git repository. Validation succeeds, then the actual early
        # Git check fails before the later project/container trap is installed.
        result = self.run_command("verify")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.directory.exists())
        self.assertIn("credential_disposed", result.stdout)

    def run_fault(self, injection):
        # Execute the actual embedded helper with narrow OS fault injection.
        # This is not a replacement cleanup implementation or OS-failure claim.
        source = SCRIPT.read_text().split("python3 - \"$1\" <<'PY'\n", 1)[1]
        source = source.split("\nPY\n", 1)[0]
        driver = (
            "import os,sys\n"
            "before=len(os.listdir('/proc/self/fd'))\n" + injection + "\n"
            "sys.argv=['credential-helper','materialize']\n"
            "code=sys.stdin.read()\n"
            "try: exec(compile(code,'actual-credential-helper','exec'))\n"
            "except SystemExit as error: status=error.code\n"
            "else: status=0\n"
            "if len(os.listdir('/proc/self/fd')) != before: status=99\n"
            "sys.exit(status)\n"
        )
        environment = self.environment.copy()
        environment["GITHUB_TOKEN"] = SYNTHETIC_TOKEN
        result = subprocess.run(
            [sys.executable, "-c", driver],
            input=source,
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertNotIn(SYNTHETIC_TOKEN, result.stdout + result.stderr)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(self.directory.exists())

    def test_fstat_failure_closes_open_directory(self):
        self.run_fault(
            "real_fstat=os.fstat\ncalls=0\n"
            "def fail_first_fstat(fd):\n"
            " global calls\n calls+=1\n"
            " if calls==1: raise OSError('synthetic fstat fault')\n"
            " return real_fstat(fd)\n"
            "os.fstat=fail_first_fstat"
        )

    def test_close_failure_does_not_skip_removal(self):
        self.run_fault(
            "real_close=os.close\ncalls=0\n"
            "def fail_after_first_close(fd):\n"
            " global calls\n real_close(fd)\n calls+=1\n"
            " if calls==1: raise OSError('synthetic close fault')\n"
            "os.close=fail_after_first_close"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
