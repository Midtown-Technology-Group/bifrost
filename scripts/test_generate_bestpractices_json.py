"""The fork's unresolved DCO disposition survives stale upstream assurance."""

import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class DcoDispositionTest(unittest.TestCase):
    def test_regeneration_does_not_restore_upstream_met(self):
        source = Path(__file__).with_name("generate-bestpractices-json.py")
        spec = importlib.util.spec_from_file_location("badge_generator", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        upstream = {
            "dco_status": "Met",
            "dco_justification": "All commits have personal sign-offs.",
            "license_location_status": "Met",
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / ".bestpractices.json"
            response = io.BytesIO(json.dumps(upstream).encode())
            with patch.object(module, "OUTPUT", output), patch.object(
                module.urllib.request, "urlopen", return_value=response
            ):
                module.main()
            generated = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(generated["dco_status"], "?")
        self.assertIn("Unresolved", generated["dco_justification"])
        self.assertIn("no DCO equivalence", generated["dco_justification"])
        self.assertNotIn("All commits", generated["dco_justification"])
        self.assertEqual(generated["license_location_status"], "Met")


if __name__ == "__main__":
    unittest.main()
