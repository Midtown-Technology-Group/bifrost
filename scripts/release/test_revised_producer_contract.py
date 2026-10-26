#!/usr/bin/env python3
"""Acceptance of revised producer policy using its existing offline Git fixture.

The original producer regression remains unchanged. This suite exercises the
new assembly provenance boundary with synthetic verified SLSA v1 results.
"""
from __future__ import annotations

import copy
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest

PRODUCER_SHA = '723e767a7ac3c21a1b2645f7b1e033a6a12c6a22'


class RevisedProducerAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(os.environ['BIFROST_PRODUCER_CONTRACT_ROOT']).resolve()
        head = subprocess.run(['git', '-C', str(cls.root), 'rev-parse', 'HEAD'],
                              check=True, capture_output=True, text=True).stdout.strip()
        if head != PRODUCER_SHA:
            raise ValueError(f'producer must be exactly {PRODUCER_SHA}, got {head}')
        for name in ('production_manifest.py', 'candidate_provenance.py', 'test_production_manifest.py'):
            expected = subprocess.run(['git', '-C', str(cls.root), 'show',
                                       f'{PRODUCER_SHA}:scripts/release/{name}'],
                                      check=True, capture_output=True).stdout
            if (cls.root / 'scripts/release' / name).read_bytes() != expected:
                raise ValueError(f'producer source differs from pinned commit: {name}')
        sys.path.insert(0, str(cls.root / 'scripts/release'))
        spec = importlib.util.spec_from_file_location('revised_fixture',
            cls.root / 'scripts/release/test_production_manifest.py')
        cls.owner_fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.owner_fixture)

    def setUp(self):
        self.fixture = self.owner_fixture.ProductionManifestTests(
            'test_ordinary_identity_is_not_a_formal_release')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        # Existing owner seam: run real provenance orchestration/Git comparisons;
        # substitute verified-results transport and suppress git fetch only.
        self.fixture.real_provenance(lambda image, digest: [self.fixture.slsa_result(image, digest)])

    def test_valid_queue_provenance_is_retained_with_original_build_identity(self):
        bundles = {}
        result = self.fixture.build(provenance_bundles=bundles)
        self.assertEqual(set(bundles), {'api', 'client', 'worker'})
        for image in result['images'].values():
            proof = image['candidate_provenance']
            self.assertEqual(proof['run_id'], 10)
            self.assertEqual(proof['run_attempt'], 1)
            self.assertEqual(proof['checkout_commit'], result['source']['candidate_commit'])
            self.assertEqual(proof['checkout_tree'], result['source']['tree'])
            self.assertEqual(proof['predicate_type'], 'https://slsa.dev/provenance/v1')
            self.assertNotEqual(proof['run_id'], result['build']['run_id'])

    def test_same_repo_signer_false_labels_cannot_hide_different_actual_invocation(self):
        f = self.fixture
        git = self.owner_fixture.manifest.git
        git('commit', '--allow-empty', '-qm', 'unrelated actual invocation with equal tree')
        wrong = git('rev-parse', 'HEAD')
        git('checkout', '--detach', f.source)
        f.real_provenance(lambda image, digest: [f.slsa_result(image, digest, wrong)])
        with self.assertRaisesRegex(ValueError, 'source-bound'):
            f.build()
        self.assertGreater(f.verify.call_count, 0)  # Matching labels were accepted first.

    def test_mixed_actual_candidate_attempts_reject(self):
        f = self.fixture
        def results(image, digest):
            result = copy.deepcopy(f.slsa_result(image, digest))
            if image.endswith('-worker'):
                uri = self.owner_fixture.manifest.candidate_provenance.ROOT + '/actions/runs/10/attempts/2'
                result['verificationResult']['signature']['certificate']['runInvocationURI'] = uri
                result['verificationResult']['statement']['predicate']['runDetails']['metadata']['invocationId'] = uri
            return [result]
        f.real_provenance(results)
        with self.assertRaisesRegex(ValueError, 'mixes'):
            f.build()


if __name__ == '__main__':
    unittest.main()
