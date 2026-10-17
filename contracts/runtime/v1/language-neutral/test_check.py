"""Regression controls for complete, unambiguous retained fixture provenance."""
import copy
import tempfile
import unittest
from pathlib import Path

from check import EXPECTED_SOURCE_PATHS, validate_fixture_inventory


class FixtureInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.provenance = {"files": [{"path": path} for path in sorted(EXPECTED_SOURCE_PATHS)]}
        for record in self.provenance["files"]:
            (self.directory / Path(record["path"]).name).write_text("{}", encoding="utf-8")
        (self.directory / "provenance.json").write_text("{}", encoding="utf-8")

    def assert_rejected(self, provenance):
        with self.assertRaisesRegex(ValueError, "fixture provenance set mismatch"):
            validate_fixture_inventory(provenance, self.directory)

    def test_exact_inventory(self):
        validate_fixture_inventory(self.provenance, self.directory)

    def test_missing_source_path(self):
        self.provenance["files"].pop()
        self.assert_rejected(self.provenance)

    def test_duplicate_source_path(self):
        self.provenance["files"][-1] = copy.deepcopy(self.provenance["files"][0])
        self.assert_rejected(self.provenance)

    def test_foreign_path_with_same_basename(self):
        record = self.provenance["files"][0]
        record["path"] = "foreign/scenarios/" + Path(record["path"]).name
        self.assert_rejected(self.provenance)

    def test_extra_local_fixture(self):
        (self.directory / "extra.json").write_text("{}", encoding="utf-8")
        self.assert_rejected(self.provenance)

    def test_missing_local_fixture(self):
        (self.directory / Path(self.provenance["files"][0]["path"]).name).unlink()
        self.assert_rejected(self.provenance)

    def test_extra_provenance_entry(self):
        self.provenance["files"].append({"path": "foreign/extra.json"})
        self.assert_rejected(self.provenance)


if __name__ == "__main__":
    unittest.main()
