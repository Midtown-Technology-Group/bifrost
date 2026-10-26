"""The normal and explicit acceptance modes must both fail closed on drift."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import contract_sources


class ContractSourcesTests(unittest.TestCase):
    def test_default_source_fixture_has_exact_manifest_file_set(self):
        index = json.loads((contract_sources.FIXTURES / 'sources.json').read_text())['consumer']
        with patch.dict(os.environ, {}, clear=True):
            with contract_sources.sources('consumer', index['commit'], 'EXPLICIT_CONTRACT_ROOT') as root:
                self.assertEqual({str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()},
                                 set(index['files']))
                retained = root
        self.assertFalse(retained.exists())

    def test_archive_corruption_refuses_instead_of_loading_unpinned_source(self):
        index = json.loads((contract_sources.FIXTURES / 'sources.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            fixtures = Path(directory)
            (fixtures / 'sources.json').write_text(json.dumps(index))
            (fixtures / index['consumer']['archive']).write_bytes(b'corrupt archive')
            with patch.dict(os.environ, {}, clear=True), patch.object(contract_sources, 'FIXTURES', fixtures):
                with self.assertRaisesRegex(ValueError, 'archive digest'):
                    with contract_sources.sources('consumer', index['consumer']['commit'], 'EXPLICIT_CONTRACT_ROOT'):
                        self.fail('corrupt source admitted')

    def test_wrong_pin_refuses_before_loading_source(self):
        with self.assertRaisesRegex(ValueError, 'pinned commit'):
            with contract_sources.sources('consumer', '0' * 40, 'EXPLICIT_CONTRACT_ROOT'):
                self.fail('wrong pin admitted')

    def test_explicit_checkout_drift_refuses_despite_matching_head(self):
        index = json.loads((contract_sources.FIXTURES / 'sources.json').read_text())['consumer']
        with patch.dict(os.environ, {}, clear=True):
            with contract_sources.sources('consumer', index['commit'], 'EXPLICIT_CONTRACT_ROOT') as root:
                result = contract_sources.subprocess.CompletedProcess([], 0, index['commit'] + '\n', '')
                with patch.dict(os.environ, {'EXPLICIT_CONTRACT_ROOT': str(root)}), \
                     patch.object(contract_sources.subprocess, 'run', return_value=result):
                    with contract_sources.sources('consumer', index['commit'], 'EXPLICIT_CONTRACT_ROOT') as selected:
                        self.assertEqual(selected, root)
                    (root / 'scripts/platform_release.py').write_text('substituted source')
                    with self.assertRaisesRegex(ValueError, 'source differs'):
                        with contract_sources.sources('consumer', index['commit'], 'EXPLICIT_CONTRACT_ROOT'):
                            self.fail('modified explicit source admitted')

    def test_explicit_wrong_checkout_never_falls_back_to_fixture(self):
        index = json.loads((contract_sources.FIXTURES / 'sources.json').read_text())['consumer']
        result = contract_sources.subprocess.CompletedProcess([], 0, '0' * 40 + '\n', '')
        with patch.dict(os.environ, {'EXPLICIT_CONTRACT_ROOT': '/explicit/checkout'}), \
             patch.object(contract_sources.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(ValueError, 'must be exactly'):
                with contract_sources.sources('consumer', index['commit'], 'EXPLICIT_CONTRACT_ROOT'):
                    self.fail('invalid explicit checkout admitted via fallback')


if __name__ == '__main__':
    unittest.main()
