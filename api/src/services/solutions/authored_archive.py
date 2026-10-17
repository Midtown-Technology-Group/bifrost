"""Retain protected authored bytes for accounting recovery, never runtime delivery."""

from __future__ import annotations

import hashlib
import re
import stat
import struct
from io import BytesIO
from types import MappingProxyType
from typing import Any
from zipfile import ZIP_STORED, BadZipFile, ZipFile

from bifrost.solution_source_closure import source_archive

from src.core.solution_delivery_policy import delivery_path
from src.services.solutions.deployment_storage import (
    MAX_AUTHORED_ARCHIVE_BYTES,
    SolutionDeploymentStorage,
)
from src.services.solutions.github_delivery_source import (
    MAX_AUTHORED_FILES,
    MAX_SOURCE_BYTES,
    VerifiedAuthoredSolution,
    VerifiedAuthoredSolutionFile,
)

AUTHORED_ARCHIVE_SCHEMA = "bifrost.solution-authored-archive/v1"


class AuthoredArchiveIntegrityError(ValueError):
    """Retained authored input differs from its protected inventory."""


def _git_subtree_sha(files: dict[str, bytes], manifest: tuple[VerifiedAuthoredSolutionFile, ...], prefix: str) -> str:
    """Rebuild Git's tree identity using bytes and protected regular-file modes."""
    directories: dict[str, dict[str, tuple[str, str]]] = {"": {}}
    for item in manifest:
        path = item.path.removeprefix(prefix)
        parts = path.split("/")
        parent = "/".join(parts[:-1])
        for depth in range(1, len(parts)):
            directories.setdefault("/".join(parts[:depth]), {})
        raw = files[path]
        blob_sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw,
            usedforsecurity=False).hexdigest()
        directories[parent][parts[-1]] = (item.mode, blob_sha)
    root_sha = ""
    for path in sorted(directories, key=lambda value: len(value.split("/")) if value else 0, reverse=True):
        entries = directories[path]
        names = sorted(entries, key=lambda name: (name + ("/" if entries[name][0] == "40000" else "")).encode("utf-8"))
        raw = b"".join(mode.encode() + b" " + name.encode("utf-8") + b"\0" + bytes.fromhex(sha)
            for name in names for mode, sha in [entries[name]])
        root_sha = hashlib.sha1(b"tree " + str(len(raw)).encode() + b"\0" + raw,
            usedforsecurity=False).hexdigest()
        if path:
            parent, _, name = path.rpartition("/")
            directories[parent][name] = ("40000", root_sha)
    return root_sha


def reconstruct_authored_archive(
    archive: bytes, proof: dict[str, Any], *, expected_commit_sha: str, expected_tree_sha: str,
) -> VerifiedAuthoredSolution:
    """Validate retained input against independently trusted Git proof anchors."""
    from src.services.solution_deploy_obligations import solution_source_content_id

    fields = {"schema_version", "artifact_key", "archive_sha256", "commit_sha", "tree_sha", "subtree_sha",
        "solution_slug", "repo_subpath", "source_content_id", "source_files"}
    if not isinstance(proof, dict) or set(proof) != fields or proof["schema_version"] != AUTHORED_ARCHIVE_SCHEMA:
        raise AuthoredArchiveIntegrityError("Authored archive provenance is unsupported")
    for value in (expected_commit_sha, expected_tree_sha, proof["commit_sha"], proof["tree_sha"], proof["subtree_sha"]):
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise AuthoredArchiveIntegrityError("Authored archive Git identity is invalid")
    if proof["commit_sha"] != expected_commit_sha or proof["tree_sha"] != expected_tree_sha:
        raise AuthoredArchiveIntegrityError("Authored archive differs from protected commit/tree")
    root = proof["repo_subpath"]
    if (not isinstance(root, str) or re.fullmatch(r"solutions/[a-z0-9]+(?:-[a-z0-9]+)*", root) is None
            or proof["solution_slug"] != root.split("/")[1]):
        raise AuthoredArchiveIntegrityError("Authored archive Solution identity is invalid")
    identity = proof["source_content_id"]
    if not isinstance(identity, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", identity) is None:
        raise AuthoredArchiveIntegrityError("Authored archive content identity is invalid")
    if (len(archive) > MAX_AUTHORED_ARCHIVE_BYTES
            or not isinstance(proof["archive_sha256"], str)
            or hashlib.sha256(archive).hexdigest() != proof["archive_sha256"]):
        raise AuthoredArchiveIntegrityError("Authored archive bytes differ or exceed their bound")
    values = proof["source_files"]
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_AUTHORED_FILES:
        raise AuthoredArchiveIntegrityError("Authored archive inventory exceeds its bound")
    prefix = root + "/"
    manifest = []
    total = 0
    for entry in values:
        if (not isinstance(entry, dict) or set(entry) != {"path", "mode", "sha256", "size"}
                or not isinstance(entry["path"], str) or not entry["path"].startswith(prefix)
                or not isinstance(entry["mode"], str) or entry["mode"] not in {"100644", "100755"}
                or not isinstance(entry["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None
                or type(entry["size"]) is not int or not 0 <= entry["size"] <= MAX_SOURCE_BYTES):
            raise AuthoredArchiveIntegrityError("Authored archive file manifest is invalid")
        try:
            delivery_path(entry["path"][len(prefix):])
            entry["path"].encode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise AuthoredArchiveIntegrityError("Authored archive path is unsafe") from exc
        total += entry["size"]
        manifest.append(VerifiedAuthoredSolutionFile(**entry))
    paths = [item.path for item in manifest]
    if total > MAX_SOURCE_BYTES or paths != sorted(set(paths)):
        raise AuthoredArchiveIntegrityError("Authored archive manifest is unbounded or ambiguous")
    if solution_source_content_id(solution_slug=proof["solution_slug"], repo_subpath=root, source_files=values) != identity:
        raise AuthoredArchiveIntegrityError("Authored archive manifest differs from its content identity")
    expected = {item.path[len(prefix):]: item for item in manifest}
    # A Git tree cannot contain both a file and a child below that file.
    if any("/".join(path.split("/")[:depth]) in expected
           for path in expected for depth in range(1, len(path.split("/")))):
        raise AuthoredArchiveIntegrityError("Authored archive paths conflict")
    # Our deterministic writer emits a single-disk, comment-free, non-ZIP64
    # archive. Bound the central-directory count before ZipFile allocates entries.
    if len(archive) < 22 or archive[-22:-18] != b"PK\x05\x06":
        raise AuthoredArchiveIntegrityError("Authored archive ZIP footer is unsupported")
    _, disk, central_disk, disk_count, count, central_size, central_offset, comment = struct.unpack("<4s4H2LH", archive[-22:])
    if (disk or central_disk or disk_count != count or count != len(expected) or count > MAX_AUTHORED_FILES
            or comment or central_offset + central_size != len(archive) - 22):
        raise AuthoredArchiveIntegrityError("Authored archive ZIP directory is invalid or unbounded")
    try:
        with ZipFile(BytesIO(archive)) as zipped:
            members = zipped.infolist()
            names = [item.filename for item in members]
            if len(names) != len(set(names)) or set(names) != set(expected) or len(names) > MAX_AUTHORED_FILES:
                raise AuthoredArchiveIntegrityError("Authored archive ZIP inventory differs")
            files = {}
            for item in members:
                mode = stat.S_IFMT(item.external_attr >> 16)
                if (item.is_dir() or item.orig_filename != item.filename or item.extra
                        or mode not in {0, stat.S_IFREG} or item.flag_bits & 1
                        or item.compress_type != ZIP_STORED or item.file_size != expected[item.filename].size
                        or item.compress_size != item.file_size):
                    raise AuthoredArchiveIntegrityError("Authored archive ZIP entry is unsafe or differs")
                raw = zipped.read(item)
                if len(raw) != item.file_size or hashlib.sha256(raw).hexdigest() != expected[item.filename].sha256:
                    raise AuthoredArchiveIntegrityError("Authored archive file bytes differ")
                files[item.filename] = raw
    except (BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise AuthoredArchiveIntegrityError("Authored archive ZIP is invalid") from exc
    frozen_manifest = tuple(manifest)
    if _git_subtree_sha(files, frozen_manifest, prefix) != proof["subtree_sha"]:
        raise AuthoredArchiveIntegrityError("Authored archive Git subtree identity differs")
    return VerifiedAuthoredSolution(commit_sha=proof["commit_sha"], tree_sha=proof["tree_sha"],
        subtree_sha=proof["subtree_sha"], solution_slug=proof["solution_slug"], repo_subpath=root,
        source_content_id=identity, source_files=frozen_manifest,
        files=MappingProxyType({path: files[path] for path in sorted(files)}))


async def retain_authored_archive(storage: SolutionDeploymentStorage, authored: VerifiedAuthoredSolution) -> dict[str, Any]:
    """Attach complete protected input; this is not installed completion evidence."""
    archive = source_archive(dict(authored.files))
    proof = {"schema_version": AUTHORED_ARCHIVE_SCHEMA,
        "artifact_key": storage.authored_artifact_key(authored.source_content_id),
        "archive_sha256": hashlib.sha256(archive).hexdigest(), "commit_sha": authored.commit_sha,
        "tree_sha": authored.tree_sha, "subtree_sha": authored.subtree_sha,
        "solution_slug": authored.solution_slug, "repo_subpath": authored.repo_subpath,
        "source_content_id": authored.source_content_id, "source_files": authored.file_manifest()}
    reconstruct_authored_archive(archive, proof, expected_commit_sha=authored.commit_sha, expected_tree_sha=authored.tree_sha)
    await storage.write_authored_artifact(authored.source_content_id, archive, idempotent=True)
    return proof


async def read_authored_archive(
    storage: SolutionDeploymentStorage, proof: dict[str, Any], *, expected_commit_sha: str, expected_tree_sha: str,
) -> VerifiedAuthoredSolution:
    identity = proof.get("source_content_id") if isinstance(proof, dict) else None
    if (not isinstance(identity, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", identity) is None
            or proof.get("artifact_key") != storage.authored_artifact_key(identity)):
        raise AuthoredArchiveIntegrityError("Authored archive storage identity differs")
    archive = await storage.read_authored_artifact(identity)
    return reconstruct_authored_archive(archive, proof,
        expected_commit_sha=expected_commit_sha, expected_tree_sha=expected_tree_sha)
