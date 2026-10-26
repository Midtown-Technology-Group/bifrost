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
        index = json.loads((contract_sources.FIXTURES / 'sources.json').read_text())['producer']
        with patch.dict(os.environ, {}, clear=True):
            with contract_sources.sources('producer', index['commit'], 'EXPLICIT_CONTRACT_ROOT') as root:
                self.assertEqual({str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()},
                                 set(index['files']))
                retained = root
        self.assertFalse(retained.exists())

    def test_archive_corruption_refuses_instead_of_loading_unpinned_source(self):
        index = json.loads((contract_sources.FIXTURES / 'sources.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            fixtures = Path(directory)
            (fixtures / 'sources.json').write_text(json.dumps(index))
            (fixtures / index['producer']['archive']).write_bytes(b'corrupt archive')
            with patch.dict(os.environ, {}, clear=True), patch.object(contract_sources, 'FIXTURES', fixtures):
                with self.assertRaisesRegex(ValueError, 'archive digest'):
                    with contract_sources.sources('producer', index['producer']['commit'], 'EXPLICIT_CONTRACT_ROOT'):
                        self.fail('corrupt source admitted')

    def test_wrong_pin_refuses_before_loading_source(self):
        with self.assertRaisesRegex(ValueError, 'pinned commit'):
            with contract_sources.sources('producer', '0' * 40, 'EXPLICIT_CONTRACT_ROOT'):
                self.fail('wrong pin admitted')

    def test_explicit_checkout_drift_refuses_despite_matching_head(self):
        index = json.loads((contract_sources.FIXTURES / 'sources.json').read_text())['producer']
        with patch.dict(os.environ, {}, clear=True):
            with contract_sources.sources('producer', index['commit'], 'EXPLICIT_CONTRACT_ROOT') as root:
                def git(command, **kwargs):
                    content = (index['commit'] + '\n') if command[3] == 'rev-parse' else original[command[4].split(':', 1)[1]]
                    return contract_sources.subprocess.CompletedProcess(command, 0, content, '')
                original = {name: (root / name).read_bytes() for name in index['files']}
                with patch.dict(os.environ, {'EXPLICIT_CONTRACT_ROOT': str(root)}), \
                     patch.object(contract_sources.subprocess, 'run', side_effect=git):
                    with contract_sources.sources('producer', index['commit'], 'EXPLICIT_CONTRACT_ROOT') as selected:
                        self.assertEqual(selected, root)
                    (root / 'scripts/release/production_manifest.py').write_text('substituted source')
                    with self.assertRaisesRegex(ValueError, 'source differs'):
                        with contract_sources.sources('producer', index['commit'], 'EXPLICIT_CONTRACT_ROOT'):
                            self.fail('modified explicit source admitted')

    def test_private_consumer_requires_explicit_external_checkout(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'private consumer source is not bundled'):
                with contract_sources.sources('consumer', '0' * 40, 'BIFROST_INFRA_CONTRACT_ROOT'):
                    self.fail('private source loaded without an external checkout')

    def test_explicit_wrong_checkout_never_falls_back_to_fixture(self):
        index = json.loads((contract_sources.FIXTURES / 'sources.json').read_text())['producer']
        result = contract_sources.subprocess.CompletedProcess([], 0, '0' * 40 + '\n', '')
        with patch.dict(os.environ, {'EXPLICIT_CONTRACT_ROOT': '/explicit/checkout'}), \
             patch.object(contract_sources.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(ValueError, 'must be exactly'):
                with contract_sources.sources('producer', index['commit'], 'EXPLICIT_CONTRACT_ROOT'):
                    self.fail('invalid explicit checkout admitted via fallback')


if __name__ == '__main__':
    unittest.main()
