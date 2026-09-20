from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from src.services.execution import backfill_tracker


class _JobResult:
    def __init__(self, job) -> None:
        self.job = job

    def scalar_one_or_none(self):
        return self.job


class _ScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar(self):
        return self.value


class _FakeSession:
    def __init__(self, job, *, cost=Decimal("0")) -> None:
        self.job = job
        self.cost = cost
        self.execute_calls = 0
        self.commits = 0

    async def execute(self, query):
        self.execute_calls += 1
        if self.execute_calls == 1:
            return _JobResult(self.job)
        return _ScalarResult(self.cost)

    async def commit(self) -> None:
        self.commits += 1


class _SessionContext:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SessionFactory:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session

    def __call__(self):
        return _SessionContext(self.session)


def _job(**overrides):
    values = {
        "status": "running",
        "succeeded": 0,
        "failed": 0,
        "total": 2,
        "processed_run_ids": [],
        "actual_cost_usd": Decimal("1.25"),
        "estimated_cost_usd": Decimal("3.50"),
        "completed_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_record_backfill_outcome_accumulates_success_cost_and_broadcasts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = uuid4()
    run_id = uuid4()
    existing_run_id = str(uuid4())
    job = _job(succeeded=1, total=2, processed_run_ids=[existing_run_id])
    session = _FakeSession(job, cost=Decimal("0.75"))
    broadcasts = []

    async def publish(updated_job_id, payload):
        broadcasts.append((updated_job_id, payload))

    monkeypatch.setattr(backfill_tracker, "publish_summary_backfill_update", publish)

    await backfill_tracker.record_backfill_outcome(
        job_id,
        run_id,
        True,
        _SessionFactory(session),
    )

    assert job.succeeded == 2
    assert job.failed == 0
    assert job.status == "complete"
    assert job.completed_at is not None
    assert job.actual_cost_usd == Decimal("2.00")
    assert job.processed_run_ids == sorted([existing_run_id, str(run_id)])
    assert session.commits == 1
    assert broadcasts == [
        (
            job_id,
            {
                "total": 2,
                "succeeded": 2,
                "failed": 0,
                "status": "complete",
                "actual_cost_usd": "2.00",
                "estimated_cost_usd": "3.50",
            },
        )
    ]


@pytest.mark.asyncio
async def test_record_backfill_outcome_records_failure_without_cost_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = uuid4()
    run_id = uuid4()
    job = _job(total=3, actual_cost_usd=None, estimated_cost_usd=None)
    session = _FakeSession(job, cost=Decimal("99"))
    broadcasts = []

    async def publish(updated_job_id, payload):
        broadcasts.append((updated_job_id, payload))

    monkeypatch.setattr(backfill_tracker, "publish_summary_backfill_update", publish)

    await backfill_tracker.record_backfill_outcome(
        job_id,
        run_id,
        False,
        _SessionFactory(session),
    )

    assert job.succeeded == 0
    assert job.failed == 1
    assert job.status == "running"
    assert job.actual_cost_usd is None
    assert job.processed_run_ids == [str(run_id)]
    assert session.execute_calls == 1
    assert session.commits == 1
    assert broadcasts[0][1]["actual_cost_usd"] == "None"
    assert broadcasts[0][1]["estimated_cost_usd"] == "None"


@pytest.mark.asyncio
async def test_record_backfill_outcome_ignores_missing_cancelled_and_duplicate_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broadcasts = []
    progress = AsyncMock()

    async def publish(updated_job_id, payload):
        broadcasts.append((updated_job_id, payload))

    monkeypatch.setattr(backfill_tracker, "publish_summary_backfill_update", publish)
    monkeypatch.setattr(
        "src.services.platform_jobs.update_deferred_platform_job_progress", progress
    )

    missing = _FakeSession(None)
    await backfill_tracker.record_backfill_outcome(
        uuid4(),
        uuid4(),
        True,
        _SessionFactory(missing),
    )

    cancelled_job = _job(status="cancelled")
    cancelled = _FakeSession(cancelled_job)
    await backfill_tracker.record_backfill_outcome(
        uuid4(),
        uuid4(),
        True,
        _SessionFactory(cancelled),
    )

    duplicate_run_id = uuid4()
    duplicate_job = _job(processed_run_ids=[str(duplicate_run_id)])
    duplicate = _FakeSession(duplicate_job)
    await backfill_tracker.record_backfill_outcome(
        uuid4(),
        duplicate_run_id,
        True,
        _SessionFactory(duplicate),
    )

    assert missing.commits == 0
    assert cancelled.commits == 0
    assert duplicate.commits == 1
    assert cancelled_job.succeeded == 0
    assert duplicate_job.succeeded == 0
    assert len(broadcasts) == 1
    assert broadcasts[0][1]["succeeded"] == 0
    assert broadcasts[0][1]["failed"] == 0
    progress.assert_awaited_once()


@pytest.mark.asyncio
async def test_sum_run_cost_returns_none_when_no_positive_cost() -> None:
    session = _FakeSession(None)
    session.execute_calls = 1

    assert await backfill_tracker._sum_run_cost(uuid4(), session) is None


@pytest.mark.asyncio
async def test_backfill_commit_failure_is_retryable_for_delivery_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingSession(_FakeSession):
        async def commit(self) -> None:
            raise RuntimeError("commit failed")

    session = FailingSession(_job())
    lease_token = backfill_tracker.current_delivery.set(object())
    try:
        from src.jobs.rabbitmq import RetryableConsumerError

        with pytest.raises(RetryableConsumerError, match="not confirmed"):
            await backfill_tracker.record_backfill_outcome(
                uuid4(), uuid4(), True, _SessionFactory(session)
            )
    finally:
        backfill_tracker.current_delivery.reset(lease_token)

    assert session.commits == 0
