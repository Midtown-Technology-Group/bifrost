from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import ModuleType

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
MIGRATION = VERSIONS / "20261012_work_delivery_started.py"


class _RecordingOp:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


def _load(monkeypatch) -> tuple[dict, _RecordingOp]:
    recorder = _RecordingOp()
    alembic = ModuleType("alembic")
    alembic.op = recorder
    monkeypatch.setitem(sys.modules, "alembic", alembic)
    return runpy.run_path(str(MIGRATION)), recorder


def test_repair_extends_the_current_head_and_fits_the_version_column(monkeypatch) -> None:
    scope, _recorder = _load(monkeypatch)

    assert scope["revision"] == "20261012_work_delivery_started"
    assert len(scope["revision"]) <= 32
    assert scope["down_revision"] == "20261006_mtg_upstream_sdk"


def test_upgrade_repairs_both_legacy_and_fresh_database_histories(monkeypatch) -> None:
    scope, recorder = _load(monkeypatch)

    scope["upgrade"]()
    scope["downgrade"]()

    assert recorder.statements == [
        (
            "ALTER TABLE work_deliveries "
            "ADD COLUMN IF NOT EXISTS started_at TIMESTAMP WITH TIME ZONE"
        )
    ]
    historical = (VERSIONS / "20260919_postgres_delivery.py").read_text(
        encoding="utf-8"
    )
    assert 'sa.Column("started_at", sa.DateTime(timezone=True))' in historical
