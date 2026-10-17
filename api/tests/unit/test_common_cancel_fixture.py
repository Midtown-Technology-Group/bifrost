"""Private fixture artifact contracts only; no DB/SQL qualification here."""

import json
import stat
from uuid import UUID

import pytest

from tests.helpers import common_cancel_fixture
from tests.helpers.common_cancel_fixture import (
    SEED_IDS,
    SEED_SCHEMA,
    FixturePreparationError,
    _private_output,
    _read_seed,
)


def test_private_output_refuses_replacement_and_symlink(tmp_path):
    path = tmp_path / "fixture.json"
    with _private_output(path, {"schema": "pending"}) as publish:
        publish({"schema": "complete"})
    pending_path = tmp_path / "fixture.json.pending"
    assert stat.S_IMODE(pending_path.stat().st_mode) == 0o600
    assert json.loads(pending_path.read_text()) == {"schema": "pending"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {"schema": "complete"}
    dangling = tmp_path / "dangling.json"
    dangling.symlink_to(tmp_path / "absent")
    with pytest.raises(FileExistsError):
        with _private_output(dangling, {}):
            pytest.fail("dangling symlink output was accepted")
    with pytest.raises(FileExistsError):
        with _private_output(path, {"schema": "replacement"}):
            pytest.fail("existing output was accepted")
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(FileExistsError):
        with _private_output(link, {}):
            pytest.fail("symlink output was accepted")
    assert json.loads(path.read_text()) == {"schema": "complete"}


def test_failed_setup_retains_private_pending_identity(tmp_path):
    path = tmp_path / "pending.json"
    pending = {"schema": "pending", "intent": "synthetic-test-id"}
    with pytest.raises(RuntimeError):
        with _private_output(path, pending):
            raise RuntimeError("synthetic setup interruption")
    pending_path = tmp_path / "pending.json.pending"
    assert not path.exists()
    assert stat.S_IMODE(pending_path.stat().st_mode) == 0o600
    assert json.loads(pending_path.read_text()) == pending
    with pytest.raises(FileExistsError):
        with _private_output(path, {}):
            pytest.fail("pending intent was replaced")
    assert json.loads(pending_path.read_text()) == pending


def test_failed_final_file_sync_preserves_pending_without_publication(
    tmp_path, monkeypatch
):
    path = tmp_path / "fixture.json"
    pending = {"schema": "pending", "intent": "synthetic-test-id"}
    with _private_output(path, pending) as publish:

        def fail_sync(_fd):
            raise OSError("synthetic final file sync failure")

        monkeypatch.setattr(common_cancel_fixture.os, "fsync", fail_sync)
        with pytest.raises(OSError, match="synthetic final file sync failure"):
            publish({"schema": "complete"})
    assert not path.exists()
    assert json.loads((tmp_path / "fixture.json.pending").read_text()) == pending
    assert list(tmp_path.glob(".fixture-*")) == []


def test_failed_pending_directory_sync_prevents_database_stage(tmp_path, monkeypatch):
    path = tmp_path / "fixture.json"
    pending = {"schema": "pending", "intent": "synthetic-test-id"}

    def fail_sync(_path):
        raise OSError("synthetic intent directory sync failure")

    monkeypatch.setattr(common_cancel_fixture, "_sync_directory", fail_sync)
    with pytest.raises(OSError, match="synthetic intent directory sync failure"):
        with _private_output(path, pending):
            pytest.fail("database stage reached without durable intent")
    assert not path.exists()
    assert json.loads((tmp_path / "fixture.json.pending").read_text()) == pending


def test_failed_publication_directory_sync_is_not_success(tmp_path, monkeypatch):
    path = tmp_path / "fixture.json"
    pending = {"schema": "pending", "intent": "synthetic-test-id"}
    with _private_output(path, pending) as publish:

        def fail_sync(_path):
            raise OSError("synthetic publication directory sync failure")

        monkeypatch.setattr(common_cancel_fixture, "_sync_directory", fail_sync)
        with pytest.raises(
            OSError, match="synthetic publication directory sync failure"
        ):
            publish({"schema": "complete"})
    # Final bytes may exist after a failed publication: never qualify by filename.
    assert json.loads(path.read_text()) == {"schema": "complete"}
    assert json.loads((tmp_path / "fixture.json.pending").read_text()) == pending
    assert list(tmp_path.glob(".fixture-*")) == []


def test_publication_refuses_a_target_created_after_reservation(tmp_path):
    path = tmp_path / "fixture.json"
    with _private_output(path, {"schema": "pending"}) as publish:
        path.write_text("unrelated output")
        with pytest.raises(FileExistsError):
            publish({"schema": "complete"})
    assert path.read_text() == "unrelated output"
    assert json.loads((tmp_path / "fixture.json.pending").read_text()) == {
        "schema": "pending"
    }


def test_native_seed_round_trip_is_closed_canonical_and_distinct(tmp_path):
    document = {
        "schema": SEED_SCHEMA,
        **{name: str(UUID(int=index)) for index, name in enumerate(SEED_IDS, 1)},
    }
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(document))
    assert _read_seed(path) == {name: UUID(document[name]) for name in SEED_IDS}
    for changes in (
        {"unknown": "value"},
        {"schema": "pending"},
        {SEED_IDS[0]: 1},
        {SEED_IDS[0]: str(UUID(int=2))},
        {SEED_IDS[0]: "00000000-0000-0000-0000-00000000000A"},
    ):
        path.write_text(json.dumps({**document, **changes}))
        with pytest.raises(
            FixturePreparationError, match="native seed identity is invalid"
        ):
            _read_seed(path)
