"""Private fixture artifact contracts only; no DB/SQL qualification here."""

import json
import stat
from uuid import UUID

import pytest

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
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {"schema": "complete"}
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
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == pending


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
