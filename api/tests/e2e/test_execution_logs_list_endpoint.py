"""
Integration tests for the execution logs list endpoint.

Tests the admin-only endpoint GET /api/executions/logs for listing logs
across all executions with filtering and pagination.

These tests make real HTTP requests to the running API.
"""

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete
from src.models.orm.executions import Execution, ExecutionLog

import httpx
import pytest

from tests.fixtures.auth import create_test_jwt, auth_headers


# API URL (from docker-compose.test.yml)
TEST_API_URL = os.getenv("TEST_API_URL", "http://api:8000")


@pytest.fixture(scope="module")
def http_client():
    """HTTP client for making API requests."""
    with httpx.Client(base_url=TEST_API_URL, timeout=30.0) as client:
        yield client


@pytest.fixture
def admin_token():
    """JWT token for a platform admin (superuser)."""
    return create_test_jwt(
        email="admin@test.com",
        name="Test Admin",
        is_superuser=True,
    )


@pytest.fixture
def regular_user_token():
    """JWT token for a regular org user (non-superuser)."""
    return create_test_jwt(
        email="user@test.com",
        name="Test User",
        is_superuser=False,
    )


@pytest.fixture
async def seeded_logs(db_session):
    """Create deterministic rows so list and filter assertions cannot be vacuous."""
    execution_id = uuid4()
    workflow_name = f"security-log-fixture-{uuid4().hex}"
    message_token = f"needle-error-{uuid4().hex}"
    timestamp = datetime(2026, 9, 2, 12, tzinfo=timezone.utc)
    db_session.add(Execution(
        id=execution_id,
        workflow_name=workflow_name,
        executed_by_name="Execution log endpoint fixture",
    ))
    await db_session.flush()
    logs = [
        ExecutionLog(
            execution_id=execution_id, level="ERROR",
            message=f"{message_token} primary", timestamp=timestamp, sequence=1,
        ),
        ExecutionLog(
            execution_id=execution_id, level="INFO",
            message="deterministic informational entry",
            timestamp=timestamp + timedelta(seconds=1), sequence=2,
        ),
    ]
    db_session.add_all(logs)
    await db_session.commit()

    try:
        yield {
            "workflow_name": workflow_name,
            "message_token": message_token,
            "log_ids": {str(log.id) for log in logs},
        }
    finally:
        await db_session.execute(delete(ExecutionLog).where(
            ExecutionLog.execution_id == execution_id,
        ))
        await db_session.execute(delete(Execution).where(Execution.id == execution_id))
        await db_session.commit()


@pytest.mark.e2e
class TestLogsListEndpoint:
    """Integration tests for GET /api/executions/logs endpoint."""

    def test_list_logs_requires_admin(
        self,
        http_client: httpx.Client,
        regular_user_token: str,
    ):
        """Non-admin users should get 403 Forbidden."""
        response = http_client.get(
            "/api/executions/logs",
            headers=auth_headers(regular_user_token),
        )
        assert response.status_code == 403
        # Gated by the declarative RequirePlatformAdmin dependency, which
        # returns the canonical "Superuser privileges required" detail.
        detail = response.json().get("detail", "").lower()
        assert "superuser" in detail or "admin" in detail

    @pytest.mark.asyncio
    async def test_list_logs_returns_paginated_results(
        self,
        http_client: httpx.Client,
        admin_token: str,
        seeded_logs,
    ):
        """Admin can list logs with pagination."""
        response = http_client.get(
            "/api/executions/logs",
            headers=auth_headers(admin_token),
            params={"limit": 1, "workflow_name": seeded_logs["workflow_name"]},
        )
        assert response.status_code == 200
        data = response.json()

        assert len(data["logs"]) == 1
        assert data["logs"][0]["id"] in seeded_logs["log_ids"]
        assert data["continuation_token"]

    @pytest.mark.asyncio
    async def test_list_logs_filters_by_level(
        self,
        http_client: httpx.Client,
        admin_token: str,
        seeded_logs,
    ):
        """Admin can filter logs by level."""
        response = http_client.get(
            "/api/executions/logs",
            headers=auth_headers(admin_token),
            params={
                "levels": "ERROR,WARNING",
                "workflow_name": seeded_logs["workflow_name"],
                "limit": 50,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["logs"]) == 1
        assert data["logs"][0]["level"] == "ERROR"

    @pytest.mark.asyncio
    async def test_list_logs_filters_by_workflow_name(
        self,
        http_client: httpx.Client,
        admin_token: str,
        seeded_logs,
    ):
        """Admin can filter logs by workflow name (partial match)."""
        response = http_client.get(
            "/api/executions/logs",
            headers=auth_headers(admin_token),
            params={"workflow_name": seeded_logs["workflow_name"], "limit": 50},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["logs"]) == 2
        assert {log["id"] for log in data["logs"]} == seeded_logs["log_ids"]
        assert all(log["workflow_name"] == seeded_logs["workflow_name"] for log in data["logs"])

    @pytest.mark.asyncio
    async def test_list_logs_message_search(
        self,
        http_client: httpx.Client,
        admin_token: str,
        seeded_logs,
    ):
        """Admin can search in log message content."""
        response = http_client.get(
            "/api/executions/logs",
            headers=auth_headers(admin_token),
            params={
                "message_search": seeded_logs["message_token"],
                "workflow_name": seeded_logs["workflow_name"],
                "limit": 50,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["logs"]) == 1
        assert seeded_logs["message_token"] in data["logs"][0]["message"]

    @pytest.mark.asyncio
    async def test_list_logs_pagination_with_token(
        self,
        http_client: httpx.Client,
        admin_token: str,
        db_session,
    ):
        """Equal timestamps and arriving logs cannot duplicate or omit older rows."""
        execution_id = uuid4()
        workflow_name = f"log-cursor-{uuid4().hex}"
        timestamp = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
        db_session.add(Execution(
            id=execution_id,
            workflow_name=workflow_name,
            executed_by_name="Log pagination fixture",
        ))
        await db_session.flush()
        seeded = [ExecutionLog(
            execution_id=execution_id, level="INFO",
            message=f"seed-{index}", timestamp=timestamp, sequence=index,
        ) for index in range(5)]
        db_session.add_all(seeded)
        await db_session.commit()
        expected_ids = sorted((log.id for log in seeded), reverse=True)
        headers = auth_headers(admin_token)
        params = {"limit": 2, "workflow_name": workflow_name}

        try:
            first_response = http_client.get(
                "/api/executions/logs", headers=headers, params=params,
            )
            assert first_response.status_code == 200
            first = first_response.json()
            assert [log["id"] for log in first["logs"]] == expected_ids[:2]
            assert first["continuation_token"]

            # Arrivals before the cursor must not shift the remaining pages.
            # Include a same-timestamp arrival to exercise the ID tie-breaker.
            db_session.add_all([
                ExecutionLog(execution_id=execution_id, level="INFO",
                             message="newer", timestamp=timestamp + timedelta(seconds=1)),
                ExecutionLog(execution_id=execution_id, level="INFO",
                             message="same-time arrival", timestamp=timestamp),
            ])
            await db_session.commit()

            second_response = http_client.get(
                "/api/executions/logs", headers=headers,
                params={**params, "continuation_token": first["continuation_token"]},
            )
            assert second_response.status_code == 200
            second = second_response.json()
            assert [log["id"] for log in second["logs"]] == expected_ids[2:4]
            assert second["continuation_token"]

            third_response = http_client.get(
                "/api/executions/logs", headers=headers,
                params={**params, "continuation_token": second["continuation_token"]},
            )
            assert third_response.status_code == 200
            third = third_response.json()
            assert [log["id"] for log in third["logs"]] == expected_ids[4:]
            assert third["continuation_token"] is None
        finally:
            await db_session.execute(delete(ExecutionLog).where(
                ExecutionLog.execution_id == execution_id,
            ))
            await db_session.execute(delete(Execution).where(Execution.id == execution_id))
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_list_logs_date_range_filter(
        self,
        http_client: httpx.Client,
        admin_token: str,
        seeded_logs,
    ):
        """Admin can filter logs by date range."""
        response = http_client.get(
            "/api/executions/logs",
            headers=auth_headers(admin_token),
            params={
                "start_date": "2020-01-01T00:00:00Z",
                "end_date": "2030-12-31T23:59:59Z",
                "workflow_name": seeded_logs["workflow_name"],
                "limit": 50,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["logs"]) == 2
        assert {log["id"] for log in data["logs"]} == seeded_logs["log_ids"]

    @pytest.mark.asyncio
    async def test_list_logs_response_structure(
        self,
        http_client: httpx.Client,
        admin_token: str,
        seeded_logs,
    ):
        """Verify log entries have the expected structure."""
        response = http_client.get(
            "/api/executions/logs",
            headers=auth_headers(admin_token),
            params={"limit": 10, "workflow_name": seeded_logs["workflow_name"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["logs"]) == 2
        log = data["logs"][0]
        assert {
            "id", "execution_id", "level", "message", "timestamp", "workflow_name",
        }.issubset(log)

    def test_list_logs_without_auth(
        self,
        http_client: httpx.Client,
    ):
        """Unauthenticated requests should be rejected."""
        response = http_client.get("/api/executions/logs")
        # Should get 401 (unauthenticated) or 403 (forbidden)
        assert response.status_code in [401, 403]
