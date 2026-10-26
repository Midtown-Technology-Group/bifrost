"""Load reviewed source fixtures or explicitly supplied exact Git checkouts.

Snapshots are test inputs copied byte-for-byte from the pinned owner commits.
They are never an application runtime or a substitute for signed transfer proof.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from unittest.mock import patch

FIXTURES = Path(__file__).resolve().parent / 'fixtures/protected-main'


@contextmanager
def sources(kind, expected_commit, variable):
    index = json.loads((FIXTURES / 'sources.json').read_text())[kind]
    if index['commit'] != expected_commit:
        raise ValueError(f'{kind} source fixture does not match pinned commit')
    configured = os.environ.get(variable)
    if configured:
        root = Path(configured).resolve()
        head = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                              check=True, capture_output=True, text=True).stdout.strip()
        if head != expected_commit:
            raise ValueError(f'{kind} must be exactly {expected_commit}, got {head}')
        for name, digest in index['files'].items():
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
                raise ValueError(f'{kind} source differs from pinned commit: {name}')
        yield root
        return

    raw = (FIXTURES / index['archive']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != index['sha256']:
        raise ValueError(f'{kind} source archive digest differs')
    with tempfile.TemporaryDirectory(prefix=f'bifrost-{kind}-source-') as directory:
        root = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
            files = [member for member in archive.getmembers() if not member.isdir()]
            if {member.name for member in files} != set(index['files']) or len(files) != len(index['files']):
                raise ValueError(f'{kind} archive file set differs')
            for member in files:
                path = Path(member.name)
                if not member.isfile() or path.is_absolute() or '..' in path.parts:
                    raise ValueError(f'{kind} archive contains unsafe entry')
                content = archive.extractfile(member).read()
                if hashlib.sha256(content).hexdigest() != index['files'][member.name]:
                    raise ValueError(f'{kind} archived source digest differs: {member.name}')
                destination = root / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
        yield root


def producer_fixture(root):
    """Isolate the owner's script-style imports from normal pytest discovery."""
    names = ('production_manifest', 'candidate_provenance', 'candidate_image',
             'release_manifest', 'accept_release_images', 'test_release_manifest')
    previous_path = sys.path[:]
    try:
        with patch.dict(sys.modules):
            for name in names:
                sys.modules.pop(name, None)
            sys.path.insert(0, str(root / 'scripts/release'))
            spec = importlib.util.spec_from_file_location(
                'pinned_producer_fixture', root / 'scripts/release/test_production_manifest.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    finally:
        sys.path[:] = previous_path
