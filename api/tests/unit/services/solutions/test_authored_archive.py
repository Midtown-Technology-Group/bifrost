"""Retained authored input remains distinct from native executable delivery."""

import copy
import hashlib
import stat
import struct
from contextlib import asynccontextmanager
from io import BytesIO
from types import MappingProxyType, SimpleNamespace
from typing import Any, cast
from uuid import UUID
from zipfile import ZIP_STORED, ZipFile, ZipInfo

import pytest
from bifrost.solution_source_closure import source_archive
from src.services.solution_deploy_obligations import solution_source_content_id
from src.services.solutions.authored_archive import (
    AuthoredArchiveIntegrityError,
    read_authored_archive,
    reconstruct_authored_archive,
    retain_authored_archive,
)
from src.services.solutions.deployment_storage import (
    DeploymentArtifactIntegrityError,
    SolutionDeploymentStorage,
)
from src.services.solutions.github_delivery_source import (
    VerifiedAuthoredSolution,
    VerifiedAuthoredSolutionFile,
)

COMMIT, TREE = "a" * 40, "b" * 40
ROOT = "solutions/fixture"


def git_hash(kind: str, raw: bytes) -> str:
    return hashlib.sha1(kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw,
        usedforsecurity=False).hexdigest()


def authored(readme: bytes = b"# Reviewed README\n") -> VerifiedAuthoredSolution:
    files = {"README.md": readme, "bifrost.solution.yaml": b"slug: fixture\nname: Fixture\n",
        "modules/__init__.py": b"", "modules/run.py": b"run = True\n"}
    manifest = tuple(VerifiedAuthoredSolutionFile(path=ROOT + "/" + path,
        mode="100755" if path == "modules/run.py" else "100644",
        sha256=hashlib.sha256(raw).hexdigest(), size=len(raw)) for path, raw in sorted(files.items()))
    # Explicit Git tree serialization supplies an independent nested/mode oracle.
    module_raw = (b"100644 __init__.py\0" + bytes.fromhex(git_hash("blob", b""))
        + b"100755 run.py\0" + bytes.fromhex(git_hash("blob", files["modules/run.py"])))
    root_raw = (b"100644 README.md\0" + bytes.fromhex(git_hash("blob", readme))
        + b"100644 bifrost.solution.yaml\0" + bytes.fromhex(git_hash("blob", files["bifrost.solution.yaml"]))
        + b"40000 modules\0" + bytes.fromhex(git_hash("tree", module_raw)))
    values: list[dict[str, object]] = [{"path": item.path, "mode": item.mode, "sha256": item.sha256,
        "size": item.size} for item in manifest]
    return VerifiedAuthoredSolution(commit_sha=COMMIT, tree_sha=TREE, subtree_sha=git_hash("tree", root_raw),
        solution_slug="fixture", repo_subpath=ROOT,
        source_content_id=solution_source_content_id(solution_slug="fixture", repo_subpath=ROOT, source_files=values),
        source_files=manifest, files=MappingProxyType(files))


class ResourceExistsError(Exception):
    pass


class Body:
    def __init__(self, raw: bytes, chunk: int = 3):
        self.raw, self.chunk, self.offset, self.closed = raw, chunk, 0, False

    async def __aenter__(self):
        # The underlying response is not the size-aware StreamingBody.
        return SimpleNamespace(read=None)

    async def __aexit__(self, *_args):
        self.closed = True

    async def read(self, size: int = -1) -> bytes:
        assert size > 0, "authored reads must remain bounded, including duplicate-write retries"
        raw = self.raw[self.offset:self.offset + min(size, self.chunk)]
        self.offset += len(raw)
        return raw


class Client:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.calls: list[dict[str, Any]] = []
        self.bodies: list[Body] = []

    async def put_object(self, *, Key, Body, IfNoneMatch, **_kwargs):
        assert IfNoneMatch == "*"
        if Key in self.objects:
            raise ResourceExistsError(Key)
        self.objects[Key] = Body

    async def get_object(self, **kwargs):
        self.calls.append(kwargs)
        body = Body(self.objects[kwargs["Key"]])
        self.bodies.append(body)
        return {"Body": body}


def storage_for(client: Client) -> SolutionDeploymentStorage:
    @asynccontextmanager
    async def factory():
        yield client

    settings = SimpleNamespace(object_storage_provider="s3", s3_bucket="test", azure_blob_container=None)
    return SolutionDeploymentStorage(UUID(int=10), UUID(int=20), settings=cast(Any, settings), client_factory=factory)


@pytest.mark.asyncio
async def test_complete_authored_archive_is_deterministic_and_metadata_successor_keeps_runtime():
    client = Client()
    storage = storage_for(client)
    client.objects[storage.source_artifact_key] = b"native seven-file runtime"
    original = authored()
    proof = await retain_authored_archive(storage, original)
    assert client.objects[proof["artifact_key"]] == source_archive(dict(original.files))
    assert await retain_authored_archive(storage, original) == proof
    restored = await read_authored_archive(storage, proof, expected_commit_sha=COMMIT, expected_tree_sha=TREE)
    assert restored == original
    successor = await retain_authored_archive(storage, authored(b"# Reviewed metadata successor\n"))
    assert successor["artifact_key"] != proof["artifact_key"]
    assert client.objects[storage.source_artifact_key] == b"native seven-file runtime"
    assert all(call["Range"].startswith("bytes=0-") for call in client.calls)
    assert all(body.closed for body in client.bodies)
    assert not hasattr(restored, "deployment_id")


@pytest.mark.asyncio
async def test_authored_create_only_retry_rejects_different_or_oversized_stored_bytes(monkeypatch):
    from src.services.solutions import deployment_storage

    client = Client()
    storage = storage_for(client)
    identity = authored().source_content_id
    await storage.write_authored_artifact(identity, b"original", idempotent=True)
    with pytest.raises(DeploymentArtifactIntegrityError):
        await storage.write_authored_artifact(identity, b"changed", idempotent=True)
    monkeypatch.setattr(deployment_storage, "MAX_AUTHORED_ARCHIVE_BYTES", 16)
    client.objects[storage.authored_artifact_key(identity)] = b"x" * 100
    with pytest.raises(DeploymentArtifactIntegrityError, match="byte bound"):
        await storage.write_authored_artifact(identity, b"original", idempotent=True)
    assert client.bodies[-1].offset == 17 and client.bodies[-1].closed
    with pytest.raises(DeploymentArtifactIntegrityError, match="byte bound"):
        await storage.write_authored_artifact(identity, b"x" * 17)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["commit", "tree", "subtree", "slug", "root", "content_id", "mode",
    "size", "digest", "missing", "duplicate", "unsafe", "unsorted", "archive_hash", "artifact_key"])
async def test_retained_authored_provenance_cannot_change_manifest_or_protected_anchors(fault):
    client = Client()
    storage = storage_for(client)
    proof = await retain_authored_archive(storage, authored())
    proof = copy.deepcopy(proof)
    if fault in {"commit", "tree", "subtree"}:
        proof[fault + "_sha"] = "d" * 40
    elif fault == "slug":
        proof["solution_slug"] = "other"
    elif fault == "root":
        proof["repo_subpath"] = "solutions/fixture/nested"
    elif fault == "content_id":
        proof["source_content_id"] = "sha256:" + "f" * 64
    elif fault == "mode":
        proof["source_files"][0]["mode"] = "120000"
    elif fault == "size":
        proof["source_files"][0]["size"] += 1
    elif fault == "digest":
        proof["source_files"][0]["sha256"] = "f" * 64
    elif fault == "missing":
        proof["source_files"].pop()
    elif fault == "duplicate":
        proof["source_files"].append(dict(proof["source_files"][0]))
    elif fault == "unsafe":
        proof["source_files"][0]["path"] = ROOT + "/../README.md"
    elif fault == "unsorted":
        proof["source_files"].reverse()
    elif fault == "archive_hash":
        proof["archive_sha256"] = "f" * 64
    elif fault == "artifact_key":
        proof["artifact_key"] = storage.source_artifact_key
    with pytest.raises(AuthoredArchiveIntegrityError):
        await read_authored_archive(storage, proof, expected_commit_sha=COMMIT, expected_tree_sha=TREE)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["duplicate", "unexpected", "symlink", "bytes", "directory_count"])
async def test_authored_zip_reconstruction_rejects_unsafe_or_unaccounted_members(fault):
    storage = storage_for(Client())
    original = authored()
    proof = await retain_authored_archive(storage, original)
    output = BytesIO()
    with ZipFile(output, "w") as zipped:
        for path, raw in original.files.items():
            info = ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_STORED
            if fault == "symlink" and path == "README.md":
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zipped.writestr(info, b"forged" if fault == "bytes" and path == "README.md" else raw)
        if fault == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate name"):
                zipped.writestr("README.md", original.files["README.md"])
        elif fault == "unexpected":
            zipped.writestr("extra.py", b"print('unaccounted')")
    archive = output.getvalue()
    if fault == "directory_count":
        footer = list(struct.unpack("<4s4H2LH", archive[-22:]))
        footer[3] = footer[4] = 1001
        archive = archive[:-22] + struct.pack("<4s4H2LH", *footer)
    proof["archive_sha256"] = hashlib.sha256(archive).hexdigest()
    with pytest.raises(AuthoredArchiveIntegrityError):
        reconstruct_authored_archive(archive, proof, expected_commit_sha=COMMIT, expected_tree_sha=TREE)


@pytest.mark.asyncio
async def test_regular_mode_change_cannot_reuse_protected_subtree_identity():
    storage = storage_for(Client())
    original = authored()
    proof = await retain_authored_archive(storage, original)
    proof["source_files"][-1]["mode"] = "100644"
    proof["source_content_id"] = solution_source_content_id(solution_slug="fixture", repo_subpath=ROOT,
        source_files=proof["source_files"])
    with pytest.raises(AuthoredArchiveIntegrityError, match="Git subtree"):
        reconstruct_authored_archive(source_archive(dict(original.files)), proof,
            expected_commit_sha=COMMIT, expected_tree_sha=TREE)
