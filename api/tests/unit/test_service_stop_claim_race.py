"""Diagnostic only: reproduce the unchanged stop/claim source before repair.

This branch is not a merge candidate. The probe asserts the captured defect;
after its baseline receipt, replace these expectations with the real regression.
"""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete

from src.models.orm.organizations import Organization
from src.models.orm.services import ServiceAttempt, ServiceDefinition
from src.models.orm.workflows import Workflow
from src.services import service_lifecycle
from src.services.service_claim import OwnedAttempt, ServiceClaimLoop
from tests.unit.test_service_claim import FakeRedis, StubPool, _fake_redis_holder
from tests.unit.test_service_lifecycle import _ensure


@pytest.mark.asyncio
async def test_probe_stop_claim_window_on_unchanged_source(
    async_session_factory, monkeypatch
):
    """Commit a real claim after stop's empty lookup, without timing sleeps."""
    async with async_session_factory() as setup:
        definition, workflow = await _ensure(setup)
        service_id = definition.id
        workflow_id = workflow.id
        org_id = definition.organization_id
        # The stack worker uses wall-clock time and cannot consume this fixture.
        claim_time = service_lifecycle._now() + timedelta(days=1)
        definition.restart_eligible_at = claim_time
        await setup.commit()

    original_lookup = service_lifecycle.get_live_attempt_for_update
    claims = []

    async def claim_after_empty_lookup(db, requested_service_id):
        live = await original_lookup(db, requested_service_id)
        assert live is None
        async with async_session_factory(autoflush=False) as claimant:
            attempt = await service_lifecycle.claim_eligible_service(
                claimant, worker_id="stop-claim-probe", now=claim_time
            )
            assert attempt is not None and attempt.service_id == service_id
            claims.append((attempt.id, attempt.lease_token))
            await claimant.commit()
        return live

    monkeypatch.setattr(
        service_lifecycle, "get_live_attempt_for_update", claim_after_empty_lookup
    )
    try:
        async with async_session_factory(autoflush=False) as stopping:
            definition = await stopping.get(ServiceDefinition, service_id)
            await service_lifecycle.stop_service(stopping, definition)
            await stopping.commit()
        assert len(claims) == 1
        pool = StubPool()
        pool.stop_service_child = AsyncMock()
        loop = ServiceClaimLoop(worker_id="stop-claim-probe", pool=pool)
        attempt_id, lease_token = claims[0]
        loop._owned[str(attempt_id)] = OwnedAttempt(attempt_id, service_id, lease_token)
        redis = FakeRedis()
        with patch("src.core.cache.get_redis", lambda: _fake_redis_holder({"redis": redis})):
            async with async_session_factory(autoflush=False) as supervisor:
                await loop._beat_owned(supervisor)
                await supervisor.commit()
        async with async_session_factory() as observer:
            definition = await observer.get(ServiceDefinition, service_id)
            live = await service_lifecycle.get_live_attempt(observer, service_id)
            assert definition.desired_state == "stopped"
            assert live is not None and live.state == "starting"
            assert live.stop_requested_at is None
            observed = service_lifecycle.describe_observed_state(definition, live)
            assert observed == "starting"
            pool.stop_service_child.assert_not_awaited()
            # Red-capable acceptance seam: exactly the original failing API
            # assertion, captured as the baseline defect rather than waived.
            with pytest.raises(AssertionError):
                assert observed in ("stopping", "stopped")
            print("[STOPCLAIM-PROBE] desired=stopped observed=starting stop_signal=absent")
    finally:
        async with async_session_factory() as cleanup:
            await cleanup.execute(
                delete(ServiceAttempt).where(ServiceAttempt.service_id == service_id)
            )
            await cleanup.execute(
                delete(ServiceDefinition).where(ServiceDefinition.id == service_id)
            )
            await cleanup.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await cleanup.execute(delete(Organization).where(Organization.id == org_id))
            await cleanup.commit()
