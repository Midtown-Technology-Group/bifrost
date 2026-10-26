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
REAL_PROVENANCE_VERIFY = manifest.candidate_provenance.verify
from test_release_manifest import ManifestFixture


class ProductionManifestTests(ManifestFixture):
    # Reuse the exact Git/migration fixture, but not the stable suite's methods.
    def setUp(self):
        super().setUp()
        self.parent = self.source
        self.write('.github/workflows/ci.yml', 'reviewed workflow fixture\n')
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
        self.provenance = patch.object(manifest.candidate_provenance, 'verify', return_value=(
            {'run_id': 10, 'run_attempt': 1, 'invocation_commit': self.source}, {})).start()
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

    def slsa_result(self, image, digest, invocation=None, ref='refs/heads/gh-readonly-queue/main/test'):
        p = manifest.candidate_provenance
        invocation = invocation or self.source
        uri = p.ROOT + '/actions/runs/10/attempts/1'
        identity = p.ROOT + '/' + p.WORKFLOW + '@' + ref
        return {'attestation': {'verified-bundle': 'fixture'}, 'verificationResult': {
            'signature': {'certificate': {
                'issuer': 'https://token.actions.githubusercontent.com',
                'sourceRepositoryURI': p.ROOT, 'sourceRepositoryDigest': invocation,
                'sourceRepositoryRef': ref, 'subjectAlternativeName': identity,
                'buildSignerURI': identity, 'buildSignerDigest': invocation, 'runInvocationURI': uri,
            }}, 'statement': {'predicateType': p.SLSA,
                'subject': [{'name': image, 'digest': {'sha256': digest[7:]}}],
                'predicate': {'buildDefinition': {
                    'buildType': 'https://actions.github.io/buildtypes/workflow/v1',
                    'externalParameters': {'workflow': {'repository': p.ROOT, 'path': p.WORKFLOW, 'ref': ref}},
                    'internalParameters': {'github': {'event_name': 'pull_request' if ref.startswith('refs/pull/') else 'merge_group'}},
                    'resolvedDependencies': [{'uri': 'git+' + p.ROOT + '@' + ref, 'digest': {'gitCommit': invocation}}],
                }, 'runDetails': {'builder': {'id': identity}, 'metadata': {'invocationId': uri}}}}}}

    def real_provenance(self, results):
        # Exercise the real policy/verifier orchestration with already-verified
        # cryptographic results. Keep Git object/source comparisons real.
        self.provenance.side_effect = REAL_PROVENANCE_VERIFY
        original_git = manifest.git
        def local_git(*args):
            return '' if args[0] == 'fetch' else original_git(*args)
        patch.object(manifest.candidate_provenance, 'git', side_effect=local_git).start()
        patch.object(manifest.candidate_provenance, 'verified_results', side_effect=results).start()

    def test_correct_labels_and_allowed_signer_cannot_hide_wrong_source_invocation(self):
        manifest.git('commit', '--allow-empty', '-qm', 'wrong source invocation')
        wrong = manifest.git('rev-parse', 'HEAD')
        manifest.git('checkout', '--detach', self.source)
        self.real_provenance(lambda image, digest: [self.slsa_result(image, digest, wrong)])
        with self.assertRaisesRegex(ValueError, 'source-bound'):
            self.build()

    def test_verified_queue_provenance_is_retained(self):
        self.real_provenance(lambda image, digest: [self.slsa_result(image, digest)])
        bundles = {}
        result = self.build(provenance_bundles=bundles)
        self.assertEqual(set(bundles), set(manifest.COMPONENTS))
        for image in result['images'].values():
            self.assertEqual(image['candidate_provenance']['run_id'], 10)
            self.assertEqual(image['candidate_provenance']['run_attempt'], 1)
            self.assertEqual(image['candidate_provenance']['checkout_tree'], self.tree)

    def test_verified_pr_merge_invocation_binds_checked_out_second_parent(self):
        head = self.source
        manifest.git('checkout', '--detach', self.parent)
        self.write('base-only.txt', 'merge side')
        self.commit()
        manifest.git('merge', '--no-ff', '-qm', 'synthetic PR merge', head)
        merge = manifest.git('rev-parse', 'HEAD')
        manifest.git('checkout', '--detach', head)
        self.real_provenance(lambda image, digest: [self.slsa_result(image, digest, merge, 'refs/pull/7/merge')])
        result = self.build()
        self.assertEqual(result['images']['api']['candidate_provenance']['invocation_commit'], merge)
        self.assertEqual(result['images']['api']['candidate_provenance']['checkout_commit'], head)

    def test_candidate_provenance_subject_certificate_and_invocation_substitutions_refuse(self):
        for field in ['subject', 'sourceRepositoryURI', 'buildSignerDigest', 'invocationId', 'workflow']:
            def results(image, digest):
                r = self.slsa_result(image, digest)
                v = r['verificationResult']
                if field == 'subject': v['statement']['subject'][0]['digest']['sha256'] = '0'*64
                elif field == 'invocationId': v['statement']['predicate']['runDetails']['metadata']['invocationId'] += '0'
                elif field == 'workflow': v['statement']['predicate']['buildDefinition']['externalParameters']['workflow']['path'] = '.github/workflows/other.yml'
                else: v['signature']['certificate'][field] = 'foreign'
                return [r]
            self.real_provenance(results)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'source-bound'):
                self.build()

    def test_attestation_retry_accepts_equivalent_proofs_without_mixing_invocations(self):
        def results(image, digest):
            first = self.slsa_result(image, digest)
            second = copy.deepcopy(first)
            second['attestation'] = {'verified-bundle': 'retry fixture'}
            return [first, second]
        self.real_provenance(results)
        self.build()

    def test_verified_candidate_attempts_cannot_be_mixed_across_components(self):
        def results(image, digest):
            r = self.slsa_result(image, digest)
            if image.endswith('-worker'):
                uri = manifest.candidate_provenance.ROOT + '/actions/runs/10/attempts/2'
                r['verificationResult']['signature']['certificate']['runInvocationURI'] = uri
                r['verificationResult']['statement']['predicate']['runDetails']['metadata']['invocationId'] = uri
            return [r]
        self.real_provenance(results)
        with self.assertRaisesRegex(ValueError, 'mixes'):
            self.build()

    def test_other_workflow_revision_refuses_even_for_equal_checkout_tree(self):
        head = self.source
        manifest.git('checkout', '--detach', self.parent)
        self.write('.github/workflows/ci.yml', 'unreviewed workflow revision')
        self.commit()
        # Merge takes this workflow edit, while the candidate checkout is head.
        manifest.git('merge', '--no-ff', '-X', 'ours', '-qm', 'PR invocation with other workflow', head)
        merge = manifest.git('rev-parse', 'HEAD')
        manifest.git('checkout', '--detach', head)
        self.real_provenance(lambda image, digest: [self.slsa_result(image, digest, merge, 'refs/pull/7/merge')])
        with self.assertRaisesRegex(ValueError, 'source-bound'):
            self.build()

    def test_invalid_run_identifiers_refuse(self):
        for field in ('run_id', 'attempt'):
            for value in (0, -1, True, '2'):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.build(**{field: value})


if __name__ == '__main__':
    unittest.main()
