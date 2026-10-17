"""Producer failure/preflight contracts; no common SQL or admission proof."""

import argparse
import json
from uuid import UUID

import pytest

from tests.helpers.common_cancel_fixture import (
    SEED_IDS,
    SEED_SCHEMA,
    FixturePreparationError,
)
from tests.helpers.common_start_fixture import (
    seed_prepared_pair,
    seed_start_native_history,
)
from tests.helpers.common_start_fixture_driver import _run


class NoDatabase:
    def __call__(self):
        pytest.fail("database stage reached after invalid preflight")


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [True, False, -1, 1, 31, "30", None])
async def test_invalid_timeout_cannot_create_native_history_or_intent(
    tmp_path, timeout
):
    output = tmp_path / "native.json"
    with pytest.raises(FixturePreparationError) as caught:
        await seed_start_native_history(
            NoDatabase(),
            expected_principal="bifrost",
            destination=output,
            timeout_seconds=timeout,
        )
    assert str(caught.value) == "Start native fixture failed; preserve files/database"
    assert caught.value.__suppress_context__
    assert not list(tmp_path.iterdir())


def write_seed(path, offset):
    value = {
        "schema": SEED_SCHEMA,
        **{
            name: str(UUID(int=offset + index))
            for index, name in enumerate(SEED_IDS, 1)
        },
    }
    path.write_text(json.dumps(value))


@pytest.mark.asyncio
async def test_overlapping_seed_histories_stop_before_database_or_new_intent(tmp_path):
    finite, unlimited = tmp_path / "finite.json", tmp_path / "unlimited.json"
    write_seed(finite, 0)
    write_seed(unlimited, 0)
    output = tmp_path / "prepared.json"
    with pytest.raises(FixturePreparationError):
        await seed_prepared_pair(
            NoDatabase(),
            NoDatabase(),
            expected_native_principal="bifrost",
            finite_seed=finite,
            no_timeout_seed=unlimited,
            destination=output,
        )
    assert not output.exists()
    assert not output.with_name(output.name + ".pending").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [".finite-prepared", ".no-timeout-prepared.pending"])
async def test_existing_child_evidence_prevents_any_new_database_stage(
    tmp_path, suffix
):
    finite, unlimited = tmp_path / "finite.json", tmp_path / "unlimited.json"
    write_seed(finite, 0)
    write_seed(unlimited, 100)
    output = tmp_path / "prepared.json"
    existing = output.with_name(output.name + suffix)
    existing.write_text("retained evidence")
    with pytest.raises(FixturePreparationError):
        await seed_prepared_pair(
            NoDatabase(),
            NoDatabase(),
            expected_native_principal="bifrost",
            finite_seed=finite,
            no_timeout_seed=unlimited,
            destination=output,
        )
    assert existing.read_text() == "retained evidence"
    assert not output.with_name(output.name + ".pending").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase,timeout,finite,unlimited",
    [
        ("native", None, None, None),
        ("native", 30, "seed", None),
        ("common", 30, "seed", "seed"),
        ("common", None, None, "seed"),
    ],
)
async def test_driver_rejects_phase_confusion_before_reading_dsn(
    phase,
    timeout,
    finite,
    unlimited,
):
    arguments = argparse.Namespace(
        phase=phase,
        timeout_seconds=timeout,
        finite_seed=finite,
        no_timeout_seed=unlimited,
        native_principal="bifrost",
        destination="unused",
    )
    with pytest.raises(FixturePreparationError):
        await _run(arguments)
