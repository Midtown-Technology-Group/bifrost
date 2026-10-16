"""
Unit tests for per-source webhook rate limiting in the hooks router.

These tests verify that:
- Only adapter-accepted deliveries consume the per-source quota
- Requests past that quota return 429 with Retry-After header
- Invalid requests consume only the source-and-IP pre-auth flood bucket
- The 429 response contains a JSON body (not an Event/EventDelivery row)
- rate_limit_enabled=False bypasses the limiter
- rate_limit_per_minute=None bypasses the limiter
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.routers.hooks import receive_webhook
from src.services.webhooks.protocol import Rejected


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event_source(source_id=None):
    """Build a minimal EventSource-like object."""
    es = MagicMock()
    es.id = source_id or uuid4()
    es.is_active = True
    return es


def _make_webhook_source(
    *,
    rate_limit_enabled: bool = True,
    rate_limit_per_minute: int | None = 3,
    rate_limit_window_seconds: int = 60,
):
    """Build a minimal WebhookSource-like object."""
    ws = MagicMock()
    ws.rate_limit_enabled = rate_limit_enabled
    ws.rate_limit_per_minute = rate_limit_per_minute
    ws.rate_limit_window_seconds = rate_limit_window_seconds
    ws.adapter_name = None
    ws.config = {}
    ws.state = {}
    ws.integration = None
    return ws


def _make_request(source_id: str, body: bytes = b"{}") -> MagicMock:
    """Build a minimal FastAPI-like Request mock."""
    req = MagicMock()
    req.method = "POST"
    req.headers = {}
    req.query_params = {}
    req.client = None

    async def _body():
        return body

    req.body = _body
    return req


async def _fake_resolve(resolved):
    """Return a coroutine that yields *resolved*."""
    return resolved


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_returns_429_when_rate_limit_exceeded():
    """Adapter-accepted deliveries past the source quota return 429."""
    source_id = uuid4()
    event_source = _make_event_source(source_id=source_id)
    webhook_source = _make_webhook_source(rate_limit_per_minute=3, rate_limit_window_seconds=60)

    # In-memory counter for only the authenticated source quota. The separate
    # source-and-IP pre-auth limiter admits these requests.
    counter = {"n": 0}

    async def fake_check(endpoint: str, identifier: str, force: bool = False) -> None:
        from fastapi import HTTPException, status

        if endpoint == "webhook_ingress_ip":
            return
        assert endpoint == "webhook_ingress"
        assert identifier == str(source_id)
        counter["n"] += 1
        if counter["n"] > 3:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests.",
                headers={"Retry-After": "42"},
            )

    db = AsyncMock()

    with (
        patch(
            "src.routers.hooks.resolve_webhook_source",
            return_value=(event_source, webhook_source),
        ),
        patch("src.routers.hooks.RateLimiter") as MockLimiter,
    ):
        source_limiter = AsyncMock()
        source_limiter.check = fake_check
        preauth_limiter = AsyncMock()
        preauth_limiter.check = fake_check
        MockLimiter.side_effect = (
            lambda *, max_requests, window_seconds: (
                source_limiter if max_requests == 3 else preauth_limiter
            )
        )

        # First 3 calls should NOT be rate-limited (we need a non-429 path)
        # For this we patch the processor to return a Rejected so we don't
        # need to stand up DB/queue — we only care about rate limiting.
        with patch(
            "src.routers.hooks.EventProcessor"
        ) as MockProcessor:
            proc_instance = AsyncMock()

            async def accepted(*_args, before_delivery, **_kwargs):
                await before_delivery()
                return Rejected(message="ok", status_code=200)

            proc_instance.process_webhook = AsyncMock(side_effect=accepted)
            MockProcessor.return_value = proc_instance

            for _ in range(3):
                request = _make_request(str(source_id))
                response = await receive_webhook(str(source_id), request, db)
                assert response.status_code != 429, f"Expected non-429 on call {_ + 1}"

        # The fourth adapter-accepted call reaches admission but is rejected
        # before EventProcessor persists an event.
        with patch("src.routers.hooks.EventProcessor") as MockProcessor:
            proc_instance = AsyncMock()

            async def over_quota(*_args, before_delivery, **_kwargs):
                await before_delivery()
                raise AssertionError("source quota should have raised")

            proc_instance.process_webhook = AsyncMock(side_effect=over_quota)
            MockProcessor.return_value = proc_instance

            request = _make_request(str(source_id))
            response = await receive_webhook(str(source_id), request, db)

            assert response.status_code == 429
            assert "Retry-After" in response.headers
            assert int(response.headers["Retry-After"]) > 0

            # Body must be JSON
            body = json.loads(bytes(response.body))
            assert body["error"] == "rate_limit_exceeded"
            assert body["source_id"] == str(source_id)

            proc_instance.process_webhook.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_webhook_does_not_consume_shared_source_quota():
    """Unsigned traffic is isolated to its source-and-IP flood bucket."""
    source_id = uuid4()
    event_source = _make_event_source(source_id=source_id)
    webhook_source = _make_webhook_source(rate_limit_per_minute=3)
    request = _make_request(str(source_id))
    request.client = MagicMock(host="203.0.113.9")
    checks: list[tuple[str, str]] = []

    async def record_check(
        endpoint: str, identifier: str, force: bool = False
    ) -> None:
        assert force is True
        checks.append((endpoint, identifier))

    with (
        patch(
            "src.routers.hooks.resolve_webhook_source",
            return_value=(event_source, webhook_source),
        ),
        patch("src.routers.hooks.RateLimiter") as limiter_class,
        patch("src.routers.hooks.EventProcessor") as processor_class,
    ):
        source_limiter = AsyncMock()
        source_limiter.check = record_check
        preauth_limiter = AsyncMock()
        preauth_limiter.check = record_check
        limiter_class.side_effect = [source_limiter, preauth_limiter]
        processor_class.return_value.process_webhook = AsyncMock(
            return_value=Rejected(message="Invalid signature", status_code=401)
        )

        response = await receive_webhook(str(source_id), request, AsyncMock())

    assert response.status_code == 401
    assert checks == [
        ("webhook_ingress_ip", f"{source_id}:203.0.113.9")
    ]
    limiter_class.assert_any_call(max_requests=3, window_seconds=60)
    limiter_class.assert_any_call(max_requests=120, window_seconds=60)


@pytest.mark.asyncio
async def test_webhook_rate_limit_disabled_per_source():
    """When rate_limit_enabled=False, no throttling regardless of per_minute."""
    source_id = uuid4()
    event_source = _make_event_source(source_id=source_id)
    webhook_source = _make_webhook_source(
        rate_limit_enabled=False,
        rate_limit_per_minute=1,
        rate_limit_window_seconds=60,
    )

    db = AsyncMock()

    with (
        patch(
            "src.routers.hooks.resolve_webhook_source",
            return_value=(event_source, webhook_source),
        ),
        patch("src.routers.hooks.RateLimiter") as MockLimiter,
        patch("src.routers.hooks.EventProcessor") as MockProcessor,
    ):
        proc_instance = AsyncMock()
        proc_instance.process_webhook = AsyncMock(return_value=Rejected(message="ok", status_code=200))
        MockProcessor.return_value = proc_instance

        for _ in range(5):
            request = _make_request(str(source_id))
            response = await receive_webhook(str(source_id), request, db)
            assert response.status_code != 429, f"Disabled limiter should not throttle (call {_ + 1})"

        # RateLimiter should never have been instantiated
        MockLimiter.assert_not_called()


@pytest.mark.asyncio
async def test_rate_limit_hits_counter_incremented_on_429():
    """Each 429 increments bifrost:rate_limit_hits:{source_id} with 24h TTL."""
    from src.core.rate_limit import RateLimiter

    identifier = str(uuid4())
    limiter = RateLimiter(max_requests=2, window_seconds=60)

    # Build a mock redis pipeline
    mock_pipeline = AsyncMock()
    mock_pipeline.incr = MagicMock()
    mock_pipeline.expire = MagicMock()
    mock_pipeline.execute = AsyncMock(return_value=[1, True])

    mock_redis = AsyncMock()
    # incr: first two calls allowed (1, 2), third call exceeds (3)
    mock_redis.incr = AsyncMock(side_effect=[1, 2, 3])
    mock_redis.expire = AsyncMock()
    mock_redis.ttl = AsyncMock(return_value=55)
    mock_redis.pipeline = MagicMock(return_value=mock_pipeline)

    with patch("src.core.rate_limit.get_shared_redis", return_value=mock_redis):
        # First two calls: no 429
        await limiter.check("test_endpoint", identifier, force=True)
        await limiter.check("test_endpoint", identifier, force=True)
        mock_pipeline.incr.assert_not_called()

        # Third call: exceeds limit → pipeline.incr called
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await limiter.check("test_endpoint", identifier, force=True)

        assert exc_info.value.status_code == 429

        hit_key = f"bifrost:rate_limit_hits:{identifier}"
        mock_pipeline.incr.assert_called_once_with(hit_key)
        mock_pipeline.expire.assert_called_once_with(hit_key, 86400)
        mock_pipeline.execute.assert_called_once()


@pytest.mark.asyncio
async def test_webhook_rate_limit_none_per_minute_bypasses_limiter():
    """When rate_limit_per_minute is None, no throttling."""
    source_id = uuid4()
    event_source = _make_event_source(source_id=source_id)
    webhook_source = _make_webhook_source(
        rate_limit_enabled=True,
        rate_limit_per_minute=None,
    )

    db = AsyncMock()

    with (
        patch(
            "src.routers.hooks.resolve_webhook_source",
            return_value=(event_source, webhook_source),
        ),
        patch("src.routers.hooks.RateLimiter") as MockLimiter,
        patch("src.routers.hooks.EventProcessor") as MockProcessor,
    ):
        proc_instance = AsyncMock()
        proc_instance.process_webhook = AsyncMock(return_value=Rejected(message="ok", status_code=200))
        MockProcessor.return_value = proc_instance

        request = _make_request(str(source_id))
        response = await receive_webhook(str(source_id), request, db)
        assert response.status_code != 429

        # RateLimiter should never have been instantiated
        MockLimiter.assert_not_called()
