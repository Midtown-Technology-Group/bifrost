#!/usr/bin/env python3
"""Offline negative tests for the existing-pipeline protected-main receipt."""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import production_manifest as manifest
from test_release_manifest import ManifestFixture


class ProductionManifestTests(ManifestFixture):
    # Reuse the exact Git/migration fixture, but not the stable suite's methods.
    def setUp(self):
        super().setUp()
        self.parent = self.source
        self.write('source.txt', 'ordinary merge')
        self.source = self.commit()
        self.tree = manifest.git('rev-parse', 'HEAD^{tree}')
        self.version = '3.0.1-dev.7'
        self.outputs = {}
        self.receipts = {}
        self.indexes = {}
        for name in manifest.COMPONENTS:
            image = f'ghcr.io/{manifest.REPOSITORY.lower()}-{name}'
            self.outputs[name] = {'run_id': '12', 'run_attempt': '2', 'digest': self.digests[name],
                                  'candidate_source_sha': self.source}
            self.receipts[name] = {
                'schema_version': 'bifrost.image-promotion/v1', 'component': name,
                'repository': manifest.REPOSITORY, 'protected_main_source_sha': self.source,
                'source_tree_sha': self.tree, 'candidate_source_sha': self.source,
                'candidate_digest': self.digests[name], 'candidate_ref': f'{image}:candidate-tree-{self.tree}',
                'version': self.version, 'promoted_tags': [self.version, 'dev'],
            }
            self.indexes[image + '@' + self.digests[name]] = {'manifests': [
                {'digest': 'sha256:' + ('e' if arch == 'amd64' else 'f') * 64,
                 'platform': {'os': 'linux', 'architecture': arch}}
                for arch in manifest.PLATFORMS[name]
            ] + [{'digest': 'sha256:' + 'a' * 64, 'platform': {'os': 'unknown', 'architecture': 'unknown'}}]}
        def verify(**kwargs):
            self.assertEqual(kwargs['tree_sha'], self.tree)
            name = kwargs['image'].rsplit('-', 1)[1]
            self.assertEqual(kwargs['source_sha'], self.outputs[name]['candidate_source_sha'])
            self.assertEqual(kwargs['version'], self.receipts[name]['version'])
            self.assertEqual(kwargs['expected_digest'], self.digests[name])
            return manifest.candidate.CandidateImage(kwargs['image'], '', kwargs['expected_digest'],
                self.tree, kwargs['source_sha'], self.version, manifest.REPOSITORY)
        self.verify = patch.object(manifest.candidate, 'verify_candidate', side_effect=verify).start()
        self.addCleanup(patch.stopall)
        patch.object(manifest.candidate, '_inspect_json', side_effect=lambda ref, _: self.indexes[ref]).start()

    def build(self, **changes):
        args = dict(source=self.source, run_id=12, attempt=2, outputs=self.outputs, receipts=self.receipts)
        args.update(changes)
        return manifest.build(**args)

    def test_ordinary_identity_is_not_a_formal_release(self):
        result = self.build()
        self.assertEqual(result['schema_version'], 2)
        self.assertEqual(result['channel'], 'protected-main')
        self.assertEqual(result['version'], self.version)
        self.assertIsNone(result['tag'])
        self.assertEqual(result['source']['tree'], self.tree)
        self.assertEqual(result['source']['parent'], self.parent)
        self.assertEqual(result['build']['run_attempt'], 2)
        self.assertEqual(result['compatibility']['rollback']['supported'], False)
        self.assertEqual(set(result['images']['worker']['platforms']), {'linux/amd64', 'linux/arm64'})
        self.assertEqual(self.verify.call_count, 3)

    def test_substituted_or_foreign_promotion_fields_refuse(self):
        for field, value in [('component', 'worker'), ('repository', 'attacker/bifrost'),
                             ('protected_main_source_sha', 'a'*40), ('source_tree_sha', 'b'*40),
                             ('candidate_digest', 'sha256:'+'0'*64), ('candidate_ref', 'attacker:dev')]:
            with self.subTest(field=field):
                receipts = copy.deepcopy(self.receipts)
                receipts['api'][field] = value
                with self.assertRaises(ValueError):
                    self.build(receipts=receipts)

    def test_failed_legs_only_attempt_mix_and_foreign_run_refuse(self):
        for field, value in [('run_attempt', '1'), ('run_id', '13'), ('digest', 'dev')]:
            with self.subTest(field=field):
                outputs = copy.deepcopy(self.outputs)
                outputs['worker'][field] = value
                with self.assertRaises(ValueError):
                    self.build(outputs=outputs)

    def test_partial_component_set_refuses(self):
        for field in ('outputs', 'receipts'):
            value = copy.deepcopy(getattr(self, field))
            del value['worker']
            with self.assertRaises(ValueError):
                self.build(**{field: value})

    def test_unknown_or_stable_baked_version_refuses(self):
        for version in ('3.0.1', 'v3.0.1', 'dev', '3.0.1-dev.7\n'):
            receipts = copy.deepcopy(self.receipts)
            receipts['api']['version'] = version
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.build(receipts=receipts)

    def test_foreign_verified_candidate_refuses(self):
        self.verify.side_effect = None
        self.verify.return_value = manifest.candidate.CandidateImage('', '', '', '', '', '', 'attacker/bifrost')
        with self.assertRaisesRegex(ValueError, 'foreign'):
            self.build()

    def test_missing_or_duplicate_worker_architecture_refuses(self):
        worker = next(k for k in self.indexes if 'bifrost-worker@' in k)
        self.indexes[worker]['manifests'].pop(1)
        with self.assertRaisesRegex(ValueError, 'architecture'):
            self.build()
        self.indexes[worker]['manifests'].append(self.indexes[worker]['manifests'][0])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.build()

    def test_rapid_merges_pin_old_source_instead_of_following_main(self):
        original = self.build()
        self.write('source.txt', 'next rapid merge')
        newer = self.commit()
        with self.assertRaisesRegex(ValueError, 'checkout'):
            self.build()
        manifest.git('checkout', '--detach', self.source)
        self.assertEqual(self.build(), original)
        self.assertNotEqual(newer, original['source']['commit'])

    def test_revert_gets_a_distinct_source_identity_for_equal_tree(self):
        original = self.build()
        self.write('source.txt', 'temporary change')
        self.commit()
        self.write('source.txt', 'ordinary merge')
        reverted = self.commit()
        self.assertEqual(manifest.git('rev-parse', 'HEAD^{tree}'), original['source']['tree'])
        # Tree equality alone cannot relabel old receipts as the new revert commit.
        with self.assertRaisesRegex(ValueError, 'source'):
            self.build(source=reverted)

    def test_mixed_baked_versions_refuse_even_with_valid_individual_candidates(self):
        self.receipts['worker']['version'] = '3.0.1-dev.8'
        with self.assertRaisesRegex(ValueError, 'mixes'):
            self.build()

    def test_equal_tree_candidates_cannot_be_mixed_between_components(self):
        manifest.git('commit', '--allow-empty', '-qm', 'another candidate identity')
        other = manifest.git('rev-parse', 'HEAD')
        manifest.git('checkout', '--detach', self.source)
        self.outputs['worker']['candidate_source_sha'] = other
        self.receipts['worker']['candidate_source_sha'] = other
        with self.assertRaisesRegex(ValueError, 'mixes'):
            self.build()

    def test_cli_refuses_foreign_repository_event_or_ref_before_reading_inputs(self):
        baseline = {'GITHUB_REPOSITORY': manifest.REPOSITORY, 'GITHUB_EVENT_NAME': 'push',
                    'GITHUB_REF': 'refs/heads/main'}
        for key, value in [('GITHUB_REPOSITORY', 'attacker/bifrost'),
                           ('GITHUB_EVENT_NAME', 'pull_request'), ('GITHUB_REF', 'refs/heads/feature')]:
            with self.subTest(key=key), patch.dict(os.environ, {**baseline, key: value}, clear=True), \
                 patch.object(sys, 'argv', ['production_manifest.py', '--output', 'receipt.json']):
                with self.assertRaisesRegex(ValueError, 'protected-main'):
                    manifest.main()
                self.assertFalse(Path('receipt.json').exists())

    def test_invalid_run_identifiers_refuse(self):
        for field in ('run_id', 'attempt'):
            for value in (0, -1, True, '2'):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.build(**{field: value})




    # Independent acceptance additions exercise the existing producer fixture.

    def test_portable_producer_mutations_refuse(self):
        corpus = json.loads((Path(__file__).resolve().parent /
                             'fixtures/protected-main/cases.json').read_text())
        for case in corpus['cases']:
            if case['stage'] != 'producer':
                continue
            outputs, receipts = copy.deepcopy(self.outputs), copy.deepcopy(self.receipts)
            for key, value in case['changes'].items():
                if key == 'delete':
                    del outputs[value.split('.')[1]]
                else:
                    kind, component, field = key.split('.')
                    (outputs if kind == 'output' else receipts)[component][field] = value
            with self.subTest(case=case['id']), self.assertRaises(ValueError):
                self.build(outputs=outputs, receipts=receipts)

    def test_parent_baseline_does_not_certify_installed_database_or_rollback(self):
        result = self.build()
        self.assertEqual(result['source']['parent'], self.parent)
        self.assertFalse(result['compatibility']['database']['upgrade_required'])
        self.assertFalse(result['compatibility']['rollback']['supported'])
        self.assertIn('retirement evidence', result['compatibility']['rollback']['reason'])
        # This fixture's parent already contains retirement, whereas v2.0.0 does not.
        # A consumer installed on v2.0.0 still needs migration evaluation.
        self.assertNotEqual(manifest.compatibility(self.source, 'v2.0.0'),
                            result['compatibility'])

    def test_signed_candidate_with_false_source_labels_must_be_rejected(self):
        """Blocking regression: valid signature is not actual build provenance."""
        # Exercise the REAL inherited verifier, replacing external transports only.
        patch.stopall()
        image = f'ghcr.io/{manifest.REPOSITORY.lower()}-api'
        digest = self.digests['api']
        labels = {
            'org.opencontainers.image.source': f'https://github.com/{manifest.REPOSITORY}',
            'org.opencontainers.image.revision': self.source,
            'org.opencontainers.image.version': self.version,
            'com.midtowntg.bifrost.source-tree': self.tree,
            'com.midtowntg.bifrost.candidate': 'true',
        }
        # Existing BuildKit SLSA v0.2 shape: source material contradicts labels.
        provenance = {'predicateType': 'https://slsa.dev/provenance/v0.2',
                      'subject': [{'name': image, 'digest': {'sha256': digest[7:]}}],
                      'predicate': {'materials': [{
                          'uri': f'git+https://github.com/{manifest.REPOSITORY}.git',
                          'digest': {'sha1': self.parent}}]}}
        commands = []

        def transport(argv):
            commands.append(argv)
            if argv[:2] == ['cosign', 'verify']:
                # Synthetic successful same-repo signature verification.
                return json.dumps([{'critical': {'identity': {'docker-reference': image},
                                                 'image': {'docker-manifest-digest': digest}}}])
            if argv[:4] == ['docker', 'buildx', 'imagetools', 'inspect']:
                if argv[-1] == '{{json .Manifest}}':
                    return json.dumps({'digest': digest})
                if argv[-1] == '{{json .Image.Config.Labels}}':
                    return json.dumps(labels)
                return json.dumps(provenance)
            if argv[:2] == ['cosign', 'verify-attestation']:
                return json.dumps(provenance)
            raise AssertionError(f'unexpected offline command: {argv}')

        with patch.object(manifest.candidate, '_run', side_effect=transport):
            with self.assertRaises(manifest.candidate.CandidateImageError):
                manifest.candidate.verify_candidate(
                    image=image, tree_sha=self.tree, version=self.version,
                    source_sha=self.source, expected_digest=digest)
        self.assertTrue(any(argv[:2] == ['cosign', 'verify'] for argv in commands))


if __name__ == '__main__':
    unittest.main()
