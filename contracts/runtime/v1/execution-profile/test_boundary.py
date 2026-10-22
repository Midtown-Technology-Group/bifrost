"""Red-capable source-tripwire controls; no actual runtime/security execution."""
import tempfile
import unittest
from pathlib import Path

from boundary import fingerprint, verify


class SourceTripwireTests(unittest.TestCase):
    def test_unsafe_source_changes_require_review(self):
        mutations = {
            "pre-start-init": "import tenant_module\n",
            "lifecycle-sql-redis": "db.execute('UPDATE executions SET status=1')\nredis.set('terminal', 1)\n",
            "credential-fallback": "token = scoped_token or resolve_credentials()\n",
            "independent-finalization": "finalize_execution(result)\n",
            "mutable-install": "subprocess.run(['pip', 'install', requirement])\n",
            "python-only-shared-field": "shared_start['python_module'] = module_name\n",
        }
        for name, source in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / "api/src/services/execution/worker.py"
                path.parent.mkdir(parents=True)
                path.write_text("# inert baseline for a source-tripwire test\n")
                baseline = fingerprint(root)
                verify(root, baseline)
                path.write_text(source)
                with self.assertRaisesRegex(ValueError, "SourceReviewRequired"):
                    verify(root, baseline)

    def test_added_writer_file_requires_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "api").mkdir()
            baseline = fingerprint(root)
            (root / "api/new_writer.py").write_text("finalize_execution(result)\n")
            with self.assertRaisesRegex(ValueError, "SourceReviewRequired"):
                verify(root, baseline)

    def test_deleted_source_requires_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "api").mkdir()
            path = root / "api/guard.py"
            path.write_text("deny_lifecycle_writes()\n")
            baseline = fingerprint(root)
            path.unlink()
            with self.assertRaisesRegex(ValueError, "SourceReviewRequired"):
                verify(root, baseline)

    def test_path_is_part_of_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "api").mkdir()
            path = root / "api/inert.py"
            path.write_text("# unchanged bytes\n")
            baseline = fingerprint(root)
            path.rename(root / "api/startup.py")
            with self.assertRaisesRegex(ValueError, "SourceReviewRequired"):
                verify(root, baseline)


if __name__ == "__main__":
    unittest.main()
