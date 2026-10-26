"""Verify existing GitHub candidate SLSA provenance against checkout and workflow.

Only gh's cryptographically verified result enters the policy check. Certificate
extensions come from GitHub OIDC, not image labels or the SLSA predicate author.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess

from release_manifest import REPOSITORY, WORKFLOW, SHA, git

SLSA = "https://slsa.dev/provenance/v1"
ROOT = f"https://github.com/{REPOSITORY}"
INVOCATION = re.compile(re.escape(ROOT) + r"/actions/runs/([1-9]\d*)/attempts/([1-9]\d*)")
PR_REF = re.compile(r"refs/pull/[1-9]\d*/merge")
QUEUE_REF = re.compile(r"refs/heads/gh-readonly-queue/main/.+")


def qualify(result: dict, image: str, digest: str, source: str, tree: str,
            protected_main: str) -> dict:
    """Bind verified bytes, OIDC identity, invocation Git and actual checkout."""
    verified = result['verificationResult']
    statement = verified['statement']
    cert = verified['signature']['certificate']
    if statement.get('predicateType') != SLSA or statement.get('subject') != [
        {'name': image, 'digest': {'sha256': digest.removeprefix('sha256:')}}
    ]:
        raise ValueError('candidate SLSA subject differs from selected digest')
    predicate = statement['predicate']
    definition = predicate['buildDefinition']
    workflow = definition['externalParameters']['workflow']
    ref = workflow['ref']
    event = definition['internalParameters']['github']['event_name']
    if not ((event == 'pull_request' and PR_REF.fullmatch(ref)) or
            (event == 'merge_group' and QUEUE_REF.fullmatch(ref))):
        raise ValueError('candidate must come from the authorized PR/queue workflow')
    identity = f'{ROOT}/{WORKFLOW}@{ref}'
    invocation_sha = cert.get('sourceRepositoryDigest', '')
    if not SHA.fullmatch(invocation_sha):
        raise ValueError('candidate certificate has no exact invocation revision')
    expected_certificate = {
        'issuer': 'https://token.actions.githubusercontent.com',
        'sourceRepositoryURI': ROOT, 'sourceRepositoryRef': ref,
        'subjectAlternativeName': identity, 'buildSignerURI': identity,
        'buildSignerDigest': invocation_sha,
    }
    if any(cert.get(key) != value for key, value in expected_certificate.items()):
        raise ValueError('candidate certificate producer/workflow revision differs')
    invocation_uri = cert.get('runInvocationURI', '')
    match = INVOCATION.fullmatch(invocation_uri)
    if match is None or predicate['runDetails']['metadata']['invocationId'] != invocation_uri:
        raise ValueError('candidate invocation must match its OIDC certificate')
    if (definition.get('buildType') != 'https://actions.github.io/buildtypes/workflow/v1' or
        workflow != {'repository': ROOT, 'path': WORKFLOW, 'ref': ref} or
        predicate['runDetails']['builder']['id'] != identity or
        definition.get('resolvedDependencies') != [
            {'uri': f'git+{ROOT}@{ref}', 'digest': {'gitCommit': invocation_sha}}
        ]):
        raise ValueError('candidate SLSA source/workflow differs from verified identity')
    # PR OIDC identifies GitHub's synthetic merge; CI explicitly checks out its
    # second parent. Queue invocations check out their invocation SHA directly.
    git('fetch', '--no-tags', 'origin', invocation_sha)
    parents = git('rev-list', '--parents', '-n', '1', invocation_sha).split()
    if invocation_sha != source and not (
        event == 'pull_request' and len(parents) == 3 and parents[2] == source
    ):
        raise ValueError('candidate invocation is unrelated to the checked-out source')
    if git('rev-parse', f'{source}^{{tree}}') != tree:
        raise ValueError('candidate checkout tree differs')
    if git('rev-parse', f'{invocation_sha}:{WORKFLOW}') != git('rev-parse', f'{protected_main}:{WORKFLOW}'):
        raise ValueError('candidate workflow revision differs from protected-main workflow')
    bundle = json.dumps(result['attestation'], sort_keys=True, separators=(',', ':')).encode()
    return {
        'predicate_type': SLSA, 'invocation_uri': invocation_uri,
        'run_id': int(match[1]), 'run_attempt': int(match[2]),
        'invocation_commit': invocation_sha, 'workflow_commit': invocation_sha,
        'workflow_ref': ref, 'checkout_commit': source, 'checkout_tree': tree,
        'bundle_sha256': hashlib.sha256(bundle).hexdigest(),
    }


def verified_results(image: str, digest: str) -> list:
    """Fetch cryptographically verified SLSA bundles, scoped to the producer."""
    completed = subprocess.run([
        'gh', 'attestation', 'verify', f'oci://{image}@{digest}',
        '--repo', REPOSITORY, '--signer-workflow', f'{REPOSITORY}/{WORKFLOW}',
        '--predicate-type', SLSA, '--deny-self-hosted-runners', '--format', 'json',
    ], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def verify(image: str, digest: str, source: str, tree: str,
           protected_main: str) -> tuple[dict, dict]:
    """Use only verified results; unsigned labels do not establish source."""
    audits = []
    for result in verified_results(image, digest):
        try:
            audits.append((qualify(result, image, digest, source, tree, protected_main), result))
        except (KeyError, TypeError, ValueError, subprocess.CalledProcessError):
            continue
    identities = {json.dumps({k: v for k, v in audit.items() if k != 'bundle_sha256'}, sort_keys=True)
                  for audit, _ in audits}
    if len(identities) != 1:
        raise ValueError('exactly one source-bound candidate provenance identity is required')
    # The existing attestation retry may sign the same invocation twice. Those
    # are equivalent proofs, not permission to mix different producer attempts.
    return min(audits, key=lambda item: item[0]['bundle_sha256'])
