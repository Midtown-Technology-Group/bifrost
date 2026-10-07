"""Runtime file metadata is mutable; deployment identity and policy are not."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session, make_transient_to_detached
from src.models.orm.file_metadata import FileMetadata, FilePolicy
from src.services.solutions.guard import (
    SolutionManagedWriteError,
    install_solution_write_guard,
)


def _metadata(location: str = "reboot-operations") -> FileMetadata:
    solution_id = uuid.uuid4()
    return FileMetadata(
        id=uuid.uuid4(),
        solution_id=solution_id,
        organization_id=None,
        location=location,
        path="operations/pilot.json",
        s3_key=f"{location}/{solution_id}/operations/pilot.json",
        content_type="application/json",
        size_bytes=2,
        sha256="a" * 64,
        created_by=uuid.uuid4(),
        updated_by=uuid.uuid4(),
    )


def test_changed_runtime_content_metadata_passes_flush_guard() -> None:
    install_solution_write_guard()
    row = _metadata()
    make_transient_to_detached(row)
    with Session() as session:
        session.add(row)
        row.size_bytes = 42
        row.sha256 = "b" * 64
        row.updated_by = uuid.uuid4()
        session.dispatch.before_flush(session, None, None)


@pytest.mark.parametrize("content_changed", [False, True])
def test_runtime_update_can_fill_missing_creation_attribution(content_changed: bool) -> None:
    install_solution_write_guard()
    row = _metadata()
    row.created_by = None
    make_transient_to_detached(row)
    with Session() as session:
        session.add(row)
        row.created_by = uuid.uuid4()
        if content_changed:
            row.sha256 = "b" * 64
        session.dispatch.before_flush(session, None, None)


@pytest.mark.parametrize(
    "field",
    ["solution_id", "organization_id", "location", "path", "s3_key", "created_by"],
)
def test_runtime_file_identity_changes_remain_blocked(field: str) -> None:
    install_solution_write_guard()
    row = _metadata()
    make_transient_to_detached(row)
    with Session() as session:
        session.add(row)
        setattr(
            row,
            field,
            uuid.uuid4()
            if field.endswith("_id") or field == "created_by"
            else "changed",
        )
        row.sha256 = "b" * 64
        with pytest.raises(SolutionManagedWriteError):
            session.dispatch.before_flush(session, None, None)


def test_workspace_source_metadata_remains_blocked() -> None:
    install_solution_write_guard()
    row = _metadata("workspace")
    make_transient_to_detached(row)
    with Session() as session:
        session.add(row)
        row.sha256 = "b" * 64
        with pytest.raises(SolutionManagedWriteError):
            session.dispatch.before_flush(session, None, None)


def test_solution_file_policy_remains_blocked() -> None:
    install_solution_write_guard()
    row = FilePolicy(
        id=uuid.uuid4(),
        solution_id=uuid.uuid4(),
        organization_id=None,
        location="reboot-operations",
        path="",
        policies={"policies": []},
    )
    make_transient_to_detached(row)
    with Session() as session:
        session.add(row)
        row.policies = {"policies": [{"name": "unauthorized", "actions": ["write"]}]}
        with pytest.raises(SolutionManagedWriteError):
            session.dispatch.before_flush(session, None, None)
