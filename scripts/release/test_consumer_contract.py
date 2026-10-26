#!/usr/bin/env python3
"""Offline cross-repo acceptance at pinned source; no CLI main or live operations.

BIFROST_INFRA_CONTRACT_ROOT must name an unmodified checkout of CONSUMER_SHA.
Run with Python's existing unittest runner. External processes are fail-closed
simulations, not signed integration proof.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

CONSUMER_SHA = 'ecea7f7f07a0c79041cb9d7bb12987862b47ecf4'
FIXTURES = Path(__file__).resolve().parent / 'fixtures/protected-main'


class ConsumerContractAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(os.environ['BIFROST_INFRA_CONTRACT_ROOT']).resolve()
        head = subprocess.run(['git', '-C', str(cls.root), 'rev-parse', 'HEAD'],
                              check=True, capture_output=True, text=True).stdout.strip()
        if head != CONSUMER_SHA:
            raise ValueError(f'consumer must be exactly {CONSUMER_SHA}, got {head}')
        for name in ('platform_release.py', 'promote-platform-images.py'):
            path = cls.root / 'scripts' / name
            committed = subprocess.run(['git', '-C', str(cls.root), 'show',
                                        f'{CONSUMER_SHA}:scripts/{name}'],
                                       check=True, capture_output=True).stdout
            if path.read_bytes() != committed:
                raise ValueError(f'consumer source differs from pinned commit: {name}')
        spec = importlib.util.spec_from_file_location(
            'independent_consumer', cls.root / 'scripts/promote-platform-images.py')
        cls.promote = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.promote)
        cls.release = cls.promote.release

    def setUp(self):
        self.fixture = json.loads((FIXTURES / 'aligned-producer.json').read_text())
        self.value = self.fixture['manifest']
        self.retained = copy.deepcopy(self.fixture['candidate_provenance'])
        self.fresh = copy.deepcopy(self.retained)
        self.candidate_damage = None
        source, build = self.value['source'], self.value['build']
        self.run_info = {'headSha': source['commit'], 'databaseId': build['run_id'],
                         'runAttempt': build['run_attempt']}
        self.artifact = f"production-manifest-{source['commit']}-attempt-{build['run_attempt']}"
        self.inventory = {'total_count': 1, 'artifacts': [{'name': self.artifact, 'expired': False}]}
        self.metadata = {
            f"actions/runs/{build['run_id']}": {
                'id': build['run_id'], 'run_attempt': build['run_attempt'],
                'status': 'completed', 'conclusion': 'success', 'event': 'push',
                'head_branch': 'main', 'head_sha': source['commit'],
                'path': build['workflow'], 'repository': {'full_name': self.release.PLATFORM_REPO},
                'head_repository': {'full_name': self.release.PLATFORM_REPO}},
            f"commits/{source['commit']}": {'sha': source['commit'], 'parents': [{'sha': source['parent']}],
                'commit': {'verification': {'verified': True}, 'tree': {'sha': source['tree']}}},
            f"git/commits/{source['candidate_commit']}": {'sha': source['candidate_commit'],
                                                         'tree': {'sha': source['tree']}},
            f"compare/{source['commit']}...main": {'status': 'ahead'},
        }
        self.raw = (json.dumps(self.value, sort_keys=True) + '\n').encode()
        self.commands = []
        self.signer = self.release.MAIN_IDENTITY
        self.issuer = 'https://token.actions.githubusercontent.com'
        self.registry_damage = None
        self.architecture_damage = False
        self.bundle_failure = False
        self.checksum_failure = False
        # All process/HTTP seams used by these entrypoints are replaced. An
        # unexpected command raises rather than reaching Docker/GitHub/Azure.
        self.addCleanup(patch.stopall)
        patch.object(self.release, 'github_json', side_effect=self.metadata.__getitem__).start()
        patch.object(self.promote, 'github_json', side_effect=self.artifacts).start()
        patch.object(self.promote, 'run', side_effect=self.external).start()
        patch.object(self.release.subprocess, 'run', side_effect=self.external).start()

    def artifacts(self, path, query):
        self.assertEqual(path, f"/repos/{self.release.PLATFORM_REPO}/actions/runs/{self.run_info['databaseId']}/artifacts")
        self.assertEqual(query, {'name': self.artifact, 'per_page': '100'})
        return self.inventory

    def external(self, command, **kwargs):
        self.commands.append(command)
        output = ''
        if command[:3] == ['gh', 'run', 'download']:
            self.assertEqual(command[3], str(self.run_info['databaseId']))
            self.assertEqual(command[command.index('--name') + 1], self.artifact)
            directory = Path(command[command.index('--dir') + 1])
            (directory / 'production-manifest.json').write_bytes(self.raw)
            (directory / 'production-manifest.json.sigstore.json').write_text('{}')
            (directory / self.release.CANDIDATE_PROVENANCE_NAME).write_text(json.dumps(self.retained))
            checksum = '0' * 64 if self.checksum_failure else hashlib.sha256(self.raw).hexdigest()
            (directory / 'production-manifest.json.sha256').write_text(checksum + '  production-manifest.json\n')
        elif command[:3] == ['gh', 'attestation', 'verify'] and command[3].startswith('oci://'):
            role = next(role for role, record in self.value['images'].items()
                        if command[3] == 'oci://' + record['repository'] + '@' + record['digest'])
            proof = self.value['images'][role]['candidate_provenance']
            self.assertEqual(json.loads(Path(command[command.index('--bundle') + 1]).read_text()),
                             self.retained[role]['attestation']['bundle'])
            self.assertEqual(command[command.index('--source-digest') + 1], proof['invocation_commit'])
            self.assertEqual(command[command.index('--signer-digest') + 1], proof['workflow_commit'])
            self.assertIn('--deny-self-hosted-runners', command)
            result = copy.deepcopy(self.fresh[role])
            fresh = result['verificationResult']
            if self.candidate_damage == 'invocation':
                fresh['signature']['certificate']['sourceRepositoryDigest'] = '0' * 40
            elif self.candidate_damage == 'material':
                fresh['statement']['predicate']['buildDefinition']['resolvedDependencies'][0]['digest']['gitCommit'] = '0' * 40
            elif self.candidate_damage == 'signature':
                raise subprocess.CalledProcessError(1, command)
            output = json.dumps([result])
        elif command[:3] == ['gh', 'attestation', 'verify']:
            self.assertEqual(command[command.index('--source-ref') + 1], 'refs/heads/main')
            self.assertEqual(command[command.index('--source-digest') + 1], self.value['source']['commit'])
            if self.bundle_failure:
                return subprocess.CompletedProcess(command, 1, '', 'synthetic verification refusal')
            self.assertEqual(command[command.index('--cert-identity') + 1], self.signer)
        elif command[:2] == ['cosign', 'verify-attestation']:
            # Simulate the verifier enforcing the exact identity/issuer requested
            # by the REAL consumer; no synthetic signature is called real proof.
            if (command[command.index('--certificate-identity') + 1] != self.signer or
                command[command.index('--certificate-oidc-issuer') + 1] != self.issuer):
                raise subprocess.CalledProcessError(1, command)
            self.assertEqual(command[command.index('--certificate-github-workflow-sha') + 1],
                             self.value['source']['commit'])
            self.assertEqual(command[command.index('--certificate-github-workflow-trigger') + 1], 'push')
            record = next(r for r in self.value['images'].values()
                          if r['repository'] + '@' + r['digest'] == command[-1])
            statement = {'_type': 'https://in-toto.io/Statement/v1',
                         'predicateType': self.release.PRODUCTION_PREDICATE,
                         'subject': [{'name': record['repository'], 'digest': {'sha256': record['digest'][7:]}}],
                         'predicate': copy.deepcopy(self.value)}
            if self.registry_damage == 'attempt':
                statement['predicate']['build']['run_attempt'] -= 1
            elif self.registry_damage == 'source':
                statement['predicate']['source']['commit'] = '0' * 40
            elif self.registry_damage == 'subject':
                statement['subject'][0]['digest']['sha256'] = '0' * 64
            output = json.dumps([{'payloadType': 'application/vnd.in-toto+json',
                'payload': base64.b64encode(json.dumps(statement).encode()).decode()}])
        elif command[:4] == ['docker', 'buildx', 'imagetools', 'inspect'] and command[-1] == '--raw':
            ref = command[4]
            for record in self.value['images'].values():
                if ref == record['repository'] + '@' + record['digest']:
                    descriptors = [{'platform': {'os': p.split('/')[0], 'architecture': p.split('/')[1]},
                                    'digest': digest} for p, digest in record['platforms'].items()]
                    if self.architecture_damage and record['repository'].endswith('-worker'):
                        descriptors[-1]['digest'] = 'sha256:' + '0' * 64
                    output = json.dumps({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.index.v1+json',
                                         'manifests': descriptors})
                    break
                if ref in {record['repository'] + '@' + d for d in record['platforms'].values()}:
                    output = json.dumps({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                        'config': {'mediaType': 'application/vnd.oci.image.config.v1+json'},
                        'layers': [{'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip'}]})
                    break
            else:
                raise AssertionError(f'unexpected image: {ref}')
        else:
            raise AssertionError(f'forbidden offline command: {command}')
        return subprocess.CompletedProcess(command, 0, output, '')

    def validate(self, value=None):
        value = self.value if value is None else value
        return self.release.validate_production_manifest(
            value, source_sha=self.run_info['headSha'], run_id=self.run_info['databaseId'],
            run_attempt=self.run_info['runAttempt'])

    def test_producer_tuple_through_real_consumer_admission(self):
        raw, bundle, images, retained = self.promote.verified_production_manifest(self.run_info)
        self.assertEqual(raw, self.raw)
        self.assertEqual(bundle, b'{}')
        self.assertEqual(json.loads(retained), self.retained)
        self.assertEqual(len([c for c in self.commands if c[:3] == ['gh', 'attestation', 'verify'] and c[3].startswith('oci://')]), 3)
        self.assertEqual(json.loads(raw)['version'], '3.0.1-dev.7')
        self.assertIsNone(json.loads(raw)['tag'])
        self.assertEqual(len([c for c in self.commands if c[:2] == ['cosign', 'verify-attestation']]), 3)
        for role, record in images.items():
            self.assertEqual(record['tag'], record['digest'])
            self.assertEqual(record['digest'], self.value['images'][role]['repository'] + '@' + self.value['images'][role]['digest'])

    def test_revised_producer_tuple_must_pass_consumer_wire_contract(self):
        fixture = json.loads((FIXTURES / 'revised-producer.json').read_text())
        value = fixture['manifest']
        self.assertEqual(fixture['producer_commit'], '723e767a7ac3c21a1b2645f7b1e033a6a12c6a22')
        # Diagnostic control only: isolate the newly required field as the
        # rejection cause. Never use this stripped document for admission.
        control = copy.deepcopy(value)
        for image in control['images'].values():
            image.pop('candidate_provenance')
        with self.assertRaises(ValueError):
            self.release.validate_production_manifest(control, source_sha=control['source']['commit'])
        try:
            self.release.validate_production_manifest(value,
                source_sha=value['source']['commit'], run_id=value['build']['run_id'],
                run_attempt=value['build']['run_attempt'])
        except ValueError as exc:
            self.fail(f"producer {fixture['producer_commit']} -> consumer {CONSUMER_SHA}: {exc}")

    def test_bad_schema_versions_unknown_fields_and_stable_relabel_refuse(self):
        mutations = [(('schema_version',), 1), (('schema_version',), 3),
                     (('unknown',), True), (('build', 'unknown'), True),
                     (('version',), '3.0.1'), (('tag',), 'v3.0.1'),
                     (('source', 'repository'), 'attacker/bifrost'),
                     (('build', 'run_attempt'), 1), (('build', 'run_id'), 13),
                     (('compatibility', 'rollback', 'supported'), True),
                     (('compatibility', 'database', 'upgrade_required'), 'false'),
                     (('compatibility', 'database', 'migration_heads'), ['duplicate', 'duplicate']),
                     (('compatibility', 'cli_contract', 'current'), True)]
        for path, replacement in mutations:
            value = copy.deepcopy(self.value)
            parent = value
            for key in path[:-1]:
                parent = parent[key]
            parent[path[-1]] = replacement
            with self.subTest(path=path, replacement=replacement), self.assertRaises(ValueError):
                self.validate(value)

    def test_all_ten_proof_fields_required_and_candidate_attempts_coherent(self):
        for field in self.value['images']['api']['candidate_provenance']:
            value = copy.deepcopy(self.value)
            del value['images']['api']['candidate_provenance'][field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                self.validate(value)
        value = copy.deepcopy(self.value)
        proof = value['images']['worker']['candidate_provenance']
        proof['run_attempt'] += 1
        proof['invocation_uri'] = proof['invocation_uri'].rsplit('/', 1)[0] + '/' + str(proof['run_attempt'])
        with self.assertRaisesRegex(ValueError, 'mixes candidate'):
            self.validate(value)

    def test_legacy_stable_validator_rejects_v2_dev_tuple(self):
        with self.assertRaises(ValueError):
            self.release.validate_manifest(self.value, tag='v3.0.1', source_sha=self.run_info['headSha'])

    def test_foreign_pr_signer_or_wrong_issuer_rejected(self):
        for signer, issuer in [
            ('https://github.com/attacker/bifrost/.github/workflows/ci.yml@refs/heads/main', self.issuer),
            (self.release.MAIN_IDENTITY.replace('refs/heads/main', 'refs/pull/7/merge'), self.issuer),
            (self.release.MAIN_IDENTITY, 'https://attacker.invalid')]:
            self.signer, self.issuer = signer, issuer
            with self.subTest(signer=signer, issuer=issuer), self.assertRaises(subprocess.CalledProcessError):
                self.release.verify_registry_tuple(self.value)

    def test_mixed_registry_source_attempt_or_subject_refuses(self):
        for damage in ('source', 'attempt', 'subject'):
            self.registry_damage = damage
            with self.subTest(damage=damage), self.assertRaises(ValueError):
                self.promote.verified_production_manifest(self.run_info)

    def test_unknown_interrupted_or_superseded_run_refuses(self):
        key = f"actions/runs/{self.run_info['databaseId']}"
        original = copy.deepcopy(self.metadata[key])
        for field, value in [('status', 'unknown'), ('status', 'in_progress'),
                             ('conclusion', None), ('conclusion', 'cancelled'),
                             ('run_attempt', 3), ('head_sha', '0' * 40)]:
            self.metadata[key] = {**original, field: value}
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.promote.verified_production_manifest(self.run_info)
        self.metadata[key] = original

    def test_expired_artifact_fails_closed_even_with_registry_evidence(self):
        self.inventory['artifacts'][0]['expired'] = True
        with self.assertRaisesRegex(ValueError, 'unavailable or ambiguous'):
            self.promote.verified_production_manifest(self.run_info)
        self.assertEqual(self.commands, [])

    def test_unknown_artifact_fails_closed(self):
        self.inventory = {'total_count': 0, 'artifacts': []}
        with self.assertRaises(ValueError):
            self.promote.verified_production_manifest(self.run_info)
        self.assertEqual(self.commands, [])

    def test_bad_checksum_bundle_and_actual_architecture_refuse(self):
        for field in ('checksum_failure', 'bundle_failure', 'architecture_damage'):
            setattr(self, field, True)
            with self.subTest(field=field), self.assertRaises((ValueError, RuntimeError)):
                self.promote.verified_production_manifest(self.run_info)
            setattr(self, field, False)

    def test_rapid_merge_does_not_replace_explicit_source(self):
        original = copy.deepcopy(self.value)
        self.release.qualify_production_manifest(self.value)
        self.assertEqual(self.value, original)
        raw, _, _, _ = self.promote.verified_production_manifest(self.run_info)
        self.assertEqual(json.loads(raw)['source']['commit'], original['source']['commit'])

    def test_equal_tree_revert_cannot_reuse_prior_source_receipt(self):
        value = copy.deepcopy(self.value)
        value['source']['commit'] = '0' * 40
        with self.assertRaises(ValueError):
            self.validate(value)

    def test_actual_candidate_tree_cannot_differ_from_selected_tree(self):
        candidate = self.value['source']['candidate_commit']
        self.metadata[f'git/commits/{candidate}']['tree']['sha'] = '0' * 40
        with self.assertRaisesRegex(ValueError, 'candidate tree'):
            self.promote.verified_production_manifest(self.run_info)

    def test_false_cached_source_cannot_override_fresh_candidate_verification(self):
        for damage in ('invocation', 'material', 'signature'):
            self.candidate_damage = damage
            with self.subTest(damage=damage), self.assertRaises((ValueError, subprocess.CalledProcessError)):
                self.promote.verified_production_manifest(self.run_info)

    def test_cached_verification_result_is_not_trusted(self):
        for record in self.retained.values():
            record['verificationResult'] = {'untrusted': 'contradictory cached claim'}
        self.promote.verified_production_manifest(self.run_info)

    def test_inner_bundle_substitution_is_rejected_before_reverification(self):
        self.retained['worker']['attestation']['bundle']['substitution'] = True
        with self.assertRaisesRegex(ValueError, 'signed hash'):
            self.promote.verified_production_manifest(self.run_info)

    def test_retained_manifest_and_bundle_load_with_exact_lock(self):
        with tempfile.TemporaryDirectory(prefix='offline-contract-lock-') as directory:
            root = Path(directory)
            lock = root / 'images.lock.yml'
            source, build = self.value['source'], self.value['build']
            lines = ['environment: offline-fixture', 'source:',
                     f'  repository: {self.release.PLATFORM_REPO}', '  kind: protected-main',
                     f"  ref: {source['commit']}", f"  commit: {source['commit']}", '  workflow: ci.yml',
                     f"  run_id: {build['run_id']}", f"  run_attempt: {build['run_attempt']}",
                     f"  run_url: https://github.com/{self.release.PLATFORM_REPO}/actions/runs/{build['run_id']}",
                     f'  release_manifest_sha256: {hashlib.sha256(self.raw).hexdigest()}',
                     'promoted_at: offline-fixture', 'images:']
            for role, record in self.value['images'].items():
                identity = record['repository'] + '@' + record['digest']
                lines.extend([f'  {role}:', f'    tag: {identity}', f'    digest: {identity}'])
            lock.write_text('\n'.join(lines) + '\n')
            (root / self.release.MANIFEST_NAME).write_bytes(self.raw)
            (root / self.release.BUNDLE_NAME).write_text('{}')
            (root / self.release.CANDIDATE_PROVENANCE_NAME).write_text(json.dumps(self.retained))
            result = self.release.load_release_record(lock, required=True, verify=True)
            self.assertEqual(result['manifest'], self.value)
            retained = {p.name: p.read_bytes() for p in root.iterdir()}
            self.bundle_failure = True
            with self.assertRaises(ValueError):
                self.release.load_release_record(lock, required=True, verify=True)
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, retained)


if __name__ == '__main__':
    unittest.main()
