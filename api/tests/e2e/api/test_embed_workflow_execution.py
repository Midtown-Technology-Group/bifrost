"""E2E tests for workflow execution via embed token."""

import asyncio
import hashlib
import hmac as hmac_module
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlparse

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.e2e.conftest import write_and_register


def _compute_hmac(params: dict[str, str], secret: str) -> str:
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac_module.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def _extract_token_from_redirect(response) -> str:
    """Extract the access token from the redirect URL fragment."""
    location = response.headers.get("location", "")
    parsed = urlparse(location)
    fragment = parsed.fragment  # e.g. "embed_token=eyJ..."
    assert fragment.startswith("embed_token="), f"Expected embed_token in fragment, got: {fragment}"
    return fragment.split("=", 1)[1]


@pytest.mark.e2e
class TestEmbedWorkflowExecution:
    """Test that embed tokens can authenticate workflow execution."""

    @pytest.fixture
    def embed_session(self, e2e_client, platform_admin):
        """Create an app with embed secret and get an embed token."""
        slug = f"embed-wf-test-{uuid.uuid4().hex[:8]}"
        # Create app
        r = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={"name": slug, "slug": slug, "app_model": "inline_v1"},
        )
        assert r.status_code == 201, r.text
        app = r.json()

        # Create embed secret
        r = e2e_client.post(
            f"/api/applications/{app['id']}/embed-secrets",
            headers=platform_admin.headers,
            json={"name": "Test"},
        )
        raw_secret = r.json()["raw_secret"]

        # Get embed token via HMAC-verified entry point
        params = {"agent_id": "42"}
        hmac_val = _compute_hmac(params, raw_secret)
        r = e2e_client.get(
            f"/embed/apps/{app['slug']}",
            params={**params, "hmac": hmac_val},
            follow_redirects=False,
        )
        assert r.status_code == 302, r.text
        embed_token = _extract_token_from_redirect(r)

        yield {
            "app": app,
            "embed_token": embed_token,
            "raw_secret": raw_secret,
            "verified_params": params,
        }

        # Cleanup
        e2e_client.delete(f"/api/applications/{app['id']}", headers=platform_admin.headers)

    @pytest.fixture
    def recovery_workflow(self, e2e_client, platform_admin):
        suffix = uuid.uuid4().hex[:8]
        function_name = f"embed_recovery_{suffix}"
        path = f"{function_name}.py"
        workflow = write_and_register(
            e2e_client,
            platform_admin.headers,
            path,
            (
                "from bifrost import workflow\n\n"
                f"@workflow(name='{function_name}')\n"
                f"async def {function_name}(marker: str):\n"
                "    return {'marker': marker}\n"
            ),
            function_name,
        )
        access_update = e2e_client.patch(
            f"/api/workflows/{workflow['id']}",
            headers=platform_admin.headers,
            json={"access_level": "everyone"},
        )
        assert access_update.status_code == 200, access_update.text
        yield workflow
        e2e_client.delete(
            f"/api/files/editor?path={path}",
            headers=platform_admin.headers,
        )

    def test_embed_token_authenticates_workflow_execute(self, e2e_client, embed_session):
        """An embed token should be accepted by the workflow execute endpoint.

        We send a request with a nonexistent workflow — we expect 404 (not found)
        rather than 401/403 (unauthorized), proving the token was accepted.
        """
        r = e2e_client.post(
            "/api/workflows/execute",
            headers={"Authorization": f"Bearer {embed_session['embed_token']}"},
            json={
                "workflow_id": "nonexistent-workflow-for-auth-test",
                "input_data": {},
            },
        )
        # Should get 404 (workflow not found) rather than 401/403 (unauthorized)
        assert r.status_code != 401, f"Embed token rejected as unauthorized: {r.text}"
        assert r.status_code != 403, f"Embed token rejected as forbidden: {r.text}"

    def test_execution_recovery_is_bound_to_exact_embed_session(
        self,
        e2e_client,
        embed_session,
        recovery_workflow,
    ):
        params = embed_session["verified_params"]
        hmac_val = _compute_hmac(params, embed_session["raw_secret"])
        second_bootstrap = e2e_client.get(
            f"/embed/apps/{embed_session['app']['slug']}",
            params={**params, "hmac": hmac_val},
            follow_redirects=False,
        )
        assert second_bootstrap.status_code == 302, second_bootstrap.text
        second_token = _extract_token_from_redirect(second_bootstrap)
        assert second_token != embed_session["embed_token"]

        execution_id = str(uuid.uuid4())
        payload = {
            "workflow_id": recovery_workflow["id"],
            "app_id": embed_session["app"]["id"],
            "input_data": {"marker": "same-input"},
            "sync": True,
        }

        def submit(token: str):
            return e2e_client.post(
                "/api/workflows/execute",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Bifrost-Execution-ID": execution_id,
                },
                json=payload,
            )

        initial = submit(embed_session["embed_token"])
        same_session = submit(embed_session["embed_token"])
        other_same_org_session = submit(second_token)

        assert initial.status_code == 200, initial.text
        assert same_session.status_code == 200, same_session.text
        assert same_session.json()["execution_id"] == execution_id
        assert other_same_org_session.status_code == 403, other_same_org_session.text
        assert "Access denied to this execution" in other_same_org_session.text

    @pytest.mark.asyncio
    async def test_concurrent_embed_sessions_cannot_claim_same_execution(
        self,
        e2e_client,
        embed_session,
        recovery_workflow,
        async_engine,
    ):
        """Both pre-insert lookups race; the admission lock chooses one owner."""

        params = embed_session["verified_params"]
        hmac_val = _compute_hmac(params, embed_session["raw_secret"])
        second_bootstrap = e2e_client.get(
            f"/embed/apps/{embed_session['app']['slug']}",
            params={**params, "hmac": hmac_val},
            follow_redirects=False,
        )
        assert second_bootstrap.status_code == 302, second_bootstrap.text
        sessions = {
            "first": embed_session["embed_token"],
            "second": _extract_token_from_redirect(second_bootstrap),
        }
        assert sessions["first"] != sessions["second"]

        execution_id = str(uuid.uuid4())
        payload = {
            "workflow_id": recovery_workflow["id"],
            "app_id": embed_session["app"]["id"],
            "input_data": {"marker": "concurrent-owner"},
            "sync": False,
        }

        def submit(item):
            label, token = item
            response = e2e_client.post(
                "/api/workflows/execute",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Bifrost-Execution-ID": execution_id,
                },
                json=payload,
            )
            return label, token, response

        lock_name = f"bifrost:workflow-execution:{execution_id}"
        tasks = []
        wait_error = None
        async with AsyncSession(async_engine) as lock_db:
            holder_pid = await lock_db.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(holder_pid, int)
            await lock_db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_name))"),
                {"lock_name": lock_name},
            )
            tasks = [
                asyncio.create_task(asyncio.to_thread(submit, item))
                for item in sessions.items()
            ]

            async def wait_for_both_admissions() -> None:
                async with AsyncSession(async_engine) as observer:
                    while True:
                        waiting = await observer.scalar(
                            text(
                                "SELECT count(*) FROM pg_locks waiting "
                                "JOIN pg_locks held ON "
                                "waiting.locktype = held.locktype AND "
                                "waiting.database IS NOT DISTINCT FROM "
                                "held.database AND "
                                "waiting.classid = held.classid AND "
                                "waiting.objid = held.objid AND "
                                "waiting.objsubid = held.objsubid "
                                "WHERE held.pid = :holder_pid AND held.granted "
                                "AND NOT waiting.granted AND waiting.pid <> held.pid"
                            ),
                            {"holder_pid": holder_pid},
                        )
                        if waiting == 2:
                            return
                        if any(task.done() for task in tasks):
                            raise AssertionError(
                                "An embed admission completed before reaching the "
                                "controlled execution lock"
                            )
                        await asyncio.sleep(0.01)

            try:
                await asyncio.wait_for(wait_for_both_admissions(), timeout=10)
            except (Exception, asyncio.CancelledError) as exc:
                wait_error = exc
            finally:
                await lock_db.commit()

        attempts = await asyncio.wait_for(asyncio.gather(*tasks), timeout=30)
        if wait_error is not None:
            raise wait_error

        accepted = [attempt for attempt in attempts if attempt[2].status_code == 200]
        denied = [attempt for attempt in attempts if attempt[2].status_code == 403]
        assert len(accepted) == 1, [(label, response.text) for label, _, response in attempts]
        assert len(denied) == 1, [(label, response.text) for label, _, response in attempts]
        assert accepted[0][2].json()["execution_id"] == execution_id
        assert "Access denied to this execution" in denied[0][2].text

        owner_token = accepted[0][1]
        losing_token = denied[0][1]
        owner_read = e2e_client.get(
            f"/api/executions/{execution_id}",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        losing_read = e2e_client.get(
            f"/api/executions/{execution_id}",
            headers={"Authorization": f"Bearer {losing_token}"},
        )
        assert owner_read.status_code == 200, owner_read.text
        assert losing_read.status_code == 403, losing_read.text

        owner_retry = e2e_client.post(
            "/api/workflows/execute",
            headers={
                "Authorization": f"Bearer {owner_token}",
                "X-Bifrost-Execution-ID": execution_id,
            },
            json=payload,
        )
        losing_retry = e2e_client.post(
            "/api/workflows/execute",
            headers={
                "Authorization": f"Bearer {losing_token}",
                "X-Bifrost-Execution-ID": execution_id,
            },
            json=payload,
        )
        assert owner_retry.status_code == 200, owner_retry.text
        assert owner_retry.json()["execution_id"] == execution_id
        assert losing_retry.status_code == 403, losing_retry.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure_kind", ["redis", "cancel"])
    async def test_interrupted_embed_admission_requires_owner_retry_before_publish(
        self,
        async_session_factory,
        platform_admin,
        recovery_workflow,
        monkeypatch,
        failure_kind,
    ):
        """A committed row stays unpublished until its owner mints capability."""

        from src.models.enums import ExecutionStatus
        from src.models.orm.executions import Execution, WorkflowExecutionAttempt
        from src.services.execution import async_executor, submission_recovery
        from src.services.execution.submission_recovery import (
            EmbedExecutionRecoveryDeniedError,
        )

        @asynccontextmanager
        async def db_context():
            async with async_session_factory() as session:
                yield session

        monkeypatch.setattr("src.core.database.get_db_context", db_context)
        interruption = (
            RuntimeError("synthetic Redis failure")
            if failure_kind == "redis"
            else asyncio.CancelledError()
        )
        original_bind = submission_recovery.bind_execution_to_embed_session
        bind_calls = 0

        async def interrupt_first_bind(session_jti, bound_execution_id):
            nonlocal bind_calls
            bind_calls += 1
            if bind_calls == 1:
                raise interruption
            await original_bind(session_jti, bound_execution_id)

        monkeypatch.setattr(
            submission_recovery,
            "bind_execution_to_embed_session",
            interrupt_first_bind,
        )
        publish = AsyncMock()
        monkeypatch.setattr(async_executor, "_publish_pending", publish)

        execution_id = str(uuid.uuid4())
        workflow_id = recovery_workflow["id"]
        owner_jti = "owner-session"
        context = SimpleNamespace(
            solution_deployment_id=None,
            event=None,
            org_id=(
                str(platform_admin.organization_id)
                if platform_admin.organization_id
                else None
            ),
            user_id=str(platform_admin.user_id),
            name=platform_admin.name,
            email=platform_admin.email,
            startup=None,
            form_inputs={},
            embed={"agent_id": "42"},
            is_platform_admin=True,
            is_provider_org=False,
            is_external=False,
        )
        parameters = {"marker": f"interrupted-{failure_kind}"}

        async def enqueue(session_jti: str):
            return await async_executor.enqueue_workflow_execution_once(
                context=context,
                workflow_id=workflow_id,
                parameters=parameters,
                execution_id=execution_id,
                embed_session_jti=session_jti,
            )

        expected_error = (
            RuntimeError if failure_kind == "redis" else asyncio.CancelledError
        )

        with pytest.raises(expected_error):
            await enqueue(owner_jti)

        # Keep the independent scheduler away from this row while the test
        # advances its local publisher clock past the due time.
        scheduled_at = datetime.now(timezone.utc) + timedelta(days=1)
        async with async_session_factory() as db:
            execution = await db.get(Execution, uuid.UUID(execution_id))
            assert execution is not None
            assert execution.status == ExecutionStatus.SCHEDULED
            assert execution.embed_capability_issued is False
            execution.scheduled_at = scheduled_at
            await db.commit()

        assert await async_executor._publish_scheduled_once(
            execution_id=execution_id,
            publish_kwargs=None,
        ) is False
        publish.assert_not_awaited()

        with pytest.raises(EmbedExecutionRecoveryDeniedError, match="Access denied"):
            await enqueue("losing-session")
        publish.assert_not_awaited()

        monkeypatch.setattr(
            async_executor,
            "datetime",
            SimpleNamespace(now=lambda _tz: scheduled_at + timedelta(seconds=1)),
        )
        recovered_id, reused = await enqueue(owner_jti)
        assert recovered_id == execution_id
        assert reused is True
        publish.assert_awaited_once()

        # A subsequent same-owner retry can re-mint its read capability but
        # cannot publish or execute the canonical row a second time.
        retried_id, retried = await enqueue(owner_jti)
        assert retried_id == execution_id
        assert retried is True
        publish.assert_awaited_once()

        async with async_session_factory() as db:
            execution = await db.get(Execution, uuid.UUID(execution_id))
            attempts = (
                await db.execute(
                    select(WorkflowExecutionAttempt).where(
                        WorkflowExecutionAttempt.execution_id
                        == uuid.UUID(execution_id)
                    )
                )
            ).scalars().all()
            assert execution is not None
            assert execution.embed_capability_issued is True
            assert execution.status == ExecutionStatus.PENDING
            assert len(attempts) == 1
        await submission_recovery.assert_execution_bound_to_embed_session(
            owner_jti,
            execution_id,
        )
        assert bind_calls == 3

    def test_embed_token_cannot_access_admin_endpoints(self, e2e_client, embed_session):
        """Embed tokens should be blocked from admin endpoints by EmbedScopeMiddleware."""
        r = e2e_client.get(
            "/api/users",
            headers={"Authorization": f"Bearer {embed_session['embed_token']}"},
        )
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"
        assert "Embed tokens cannot access" in r.text
