"""Real forked children cold-importing over the trusted engine socket.

Uses a real ``TemplateProcess`` (the same fork primitive the pool uses):
the child has the worker's private Unix socket injected exactly as the pool
does. The parent serves the **real** ``GET /api/sdk/modules-resolve``
and ``GET /api/sdk/modules/{path:path}`` routes on that socket, and the child
loads its entry workflow (cold fetch) plus a dynamic import (cold resolve)
through ``engine_request_sync``. The child's network API is dead and its
environment carries no database or storage credentials, so success proves
the socket served both cold import routes with zero API requests and zero
child DB/S3 connections.

The dynamic import runs on a worker thread while an async SDK call
(``forms.list``) is in flight over the async socket connection, proving the
separate sync/async engine connections cannot deadlock.

Seeding is S3-only with unique paths (never through the Redis cache), so every
first load is guaranteed cold in the child.

Marked ``slow`` like the other real-fork tests: template boot costs seconds.
Run explicitly alongside the focused suite.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from uuid import uuid4

import pytest
import pytest_asyncio

from src.services.execution.template_process import TemplateProcess
from src.services.execution.worker_sdk_http import WorkerSdkHttpServer

pytestmark = pytest.mark.slow


@pytest_asyncio.fixture(autouse=True)
async def _fresh_shared_redis():
    """Rebind the shared async Redis singleton to each test's loop.

    pytest-asyncio runs every test on a fresh event loop, but
    ``src.core.redis_client``'s singleton persists across tests; a connection
    minted on a previous test's loop raises "attached to a different loop".
    Dispose around each test so the parent-served import routes mint
    connections on the current loop.
    """
    from src.core.redis_client import close_redis_client

    await close_redis_client()
    yield
    await close_redis_client()


def _context_for(
    function_name: str, file_path: str, engine_token: str, execution_id: str
) -> dict:
    return {
        "execution_id": execution_id,
        "name": function_name,
        "function_name": function_name,
        "file_path": file_path,
        "parameters": {},
        "caller": {
            "user_id": "00000000-0000-0000-0000-000000000001",
            "email": "engine@bifrost.internal",
            "name": "Bifrost Engine",
        },
        "organization": None,
        "tags": [],
        "timeout_seconds": 120,
        "cache_ttl_seconds": 0,
        "transient": True,
        "no_cache": True,
        "is_platform_admin": True,
        "engine_token": engine_token,
    }


def _entry_source(function_name: str, dep_module: str) -> str:
    return (
        "from bifrost import forms, workflow\n"
        "\n"
        "\n"
        f'@workflow(name="{function_name}", description="import-socket fork test")\n'
        f"async def {function_name}():\n"
        "    import asyncio\n"
        "    import importlib\n"
        "    import os\n"
        "    import sys\n"
        "    from bifrost.client import get_engine_socket_path\n"
        "    _used_socket = get_engine_socket_path() is not None\n"
        # The deadlock probe: the async form call and the cold synchronous
        # import run concurrently, each on its own engine-local socket
        # connection (async HTTPX vs sync HTTPX). A single shared channel
        # would deadlock here.
        "    _forms, _dep = await asyncio.gather(\n"
        "        forms.list(),\n"
        f"        asyncio.to_thread(importlib.import_module, {dep_module!r}),\n"
        "    )\n"
        "    return {\n"
        "        'value': _dep.VALUE,\n"
        "        'used_socket': _used_socket,\n"
        "        'form_count': len(_forms),\n"
        "        'had_db_url': (\n"
        "            'BIFROST_DATABASE_URL' in os.environ\n"
        "            or 'BIFROST_DATABASE_URL_SYNC' in os.environ\n"
        "        ),\n"
        "        'had_sqlalchemy': 'sqlalchemy' in sys.modules,\n"
        "        'had_s3': any(\n"
        "            k in os.environ for k in (\n"
        "                'BIFROST_S3_ACCESS_KEY',\n"
        "                'BIFROST_S3_SECRET_KEY',\n"
        "                'BIFROST_S3_ENDPOINT_URL',\n"
        "                'BIFROST_S3_BUCKET',\n"
        "            )\n"
        "        ),\n"
        "    }\n"
    )


async def _seed_s3_only(relpath: str, content: str) -> None:
    """Write a workspace file to S3 without warming the Redis cache."""
    from src.services.repo_storage import RepoStorage

    await RepoStorage().write(relpath, content.encode("utf-8"))


async def _drop_s3(relpath: str) -> None:
    from src.services.repo_storage import RepoStorage

    with contextlib.suppress(Exception):
        await RepoStorage().delete(relpath)


def _wait_for_pid_to_die(pid: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.05)
        except OSError:
            return


@pytest.mark.asyncio
class TestForkedColdImportOverSocket:
    async def test_cold_entry_and_dynamic_import_over_socket(
        self, db_session, monkeypatch, claimed_sdk_execution
    ):
        """Cold entry fetch + dynamic resolve ride the socket, no credentials."""
        from src.core.security import mint_engine_token
        from src.models.enums import FormAccessLevel
        from src.models.orm.forms import Form as FormModel

        tag = uuid4().hex[:8]
        dep_module = f"cold_socket_dep_{tag}"
        dep_path = f"{dep_module}.py"
        function_name = f"fork_sock_{tag}"
        entry_path = f"workflows/fork_sock_{tag}.py"

        form_name = f"fork-sock-form-{tag}"
        db_session.add(
            FormModel(
                name=form_name,
                access_level=FormAccessLevel.AUTHENTICATED,
                organization_id=None,
                is_active=True,
                created_by="fork-sock-test",
            )
        )
        await db_session.commit()

        await _seed_s3_only(dep_path, f"VALUE = 'socket-{tag}'\n")
        await _seed_s3_only(entry_path, _entry_source(function_name, dep_module))

        # Hard-disable HTTP for every forked child of this test: any module
        # fetch or SDK call that reaches HTTP fails with connection-refused,
        # so success proves the engine socket served both.
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        execution_id, claim_token = await claimed_sdk_execution()
        engine_token, _ = mint_engine_token(
            execution_id=execution_id,
            attempt_token=claim_token,
            solution_id=None,
            global_repo_access=True,
            timeout_seconds=120,
        )
        context = _context_for(function_name, entry_path, engine_token, execution_id)

        server = WorkerSdkHttpServer()
        await server.start()
        assert server.socket_path is not None

        template = TemplateProcess()
        template.start()
        try:
            child_pid, work_queue, result_queue = template.fork(
                worker_id="sdk-import-socket-fork",
                sdk_socket_path=server.socket_path,
            )
            work_queue.put((execution_id, context))
            envelope = await asyncio.to_thread(result_queue.get, True, 120.0)
            assert envelope["success"] is True, envelope
            result = envelope["result"]
            assert result["value"] == f"socket-{tag}", result
            assert result["used_socket"] is True, result
            assert result["form_count"] >= 1, result
            assert result["had_db_url"] is False, result
            assert result["had_sqlalchemy"] is False, result
            assert result["had_s3"] is False, result
            _wait_for_pid_to_die(child_pid)
        finally:
            with contextlib.suppress(Exception):
                work_queue.close()
            with contextlib.suppress(Exception):
                result_queue.close()
            template.shutdown()
            await server.stop()
            await _drop_s3(dep_path)
            await _drop_s3(entry_path)
            from sqlalchemy import delete

            await db_session.execute(
                delete(FormModel).where(FormModel.name == form_name)
            )
            await db_session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("stale_index", [False, True])
async def test_service_cold_bootstrap_uses_handed_token_and_socket(
    db_session, monkeypatch, stale_index
):
    """A real service child starts without ambient auth or a reachable API."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import delete
    from src.core.module_cache_contract import MODULE_INDEX_GENERATION_KEY, MODULE_INDEX_KEY
    from src.core.redis_client import get_redis_client
    from src.core.security import mint_service_token
    from src.models.orm.organizations import Organization
    from src.models.orm.services import ServiceAttempt, ServiceDefinition
    from src.models.orm.workflows import Workflow

    tag = uuid4().hex[:8]
    name = f"cold_service_{tag}"
    path = f"workflows/{name}.py"
    dep = f"cold_service_dep_{tag}"
    dep_path = f"{dep}.py"
    org = Organization(name=f"cold-service-{tag}", is_active=True, created_by="test")
    db_session.add(org)
    await db_session.flush()
    workflow = Workflow(name=name, function_name=name, path=path, type="service",
                        organization_id=org.id, created_by="test")
    db_session.add(workflow)
    await db_session.flush()
    definition = ServiceDefinition(workflow_id=workflow.id, organization_id=org.id,
                                   desired_state="running", created_by="test")
    db_session.add(definition)
    await db_session.flush()
    attempt = ServiceAttempt(service_id=definition.id, lease_token=uuid4().hex,
                             lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    db_session.add(attempt)
    await db_session.commit()
    token, expires_at = mint_service_token(
        service_id=str(definition.id), attempt_id=str(attempt.id),
        organization_id=str(org.id), solution_id=None, global_repo_access=True,
    )
    source = f'''from bifrost import service
@service
async def {name}():
    import os
    from bifrost.client import get_engine_socket_path
    from {dep} import VALUE
    await service.ready()
    return {{"value": VALUE, "socket": get_engine_socket_path() is not None,
            "has_db": "BIFROST_DATABASE_URL" in os.environ,
            "has_storage": "BIFROST_S3_SECRET_KEY" in os.environ}}
'''
    await _seed_s3_only(path, source)
    await _seed_s3_only(dep_path, f"VALUE = 'service-{tag}'\n")
    redis = await get_redis_client()
    await redis.delete(MODULE_INDEX_KEY, MODULE_INDEX_GENERATION_KEY)
    if stale_index:
        await redis.sadd(MODULE_INDEX_KEY, "stale-service-module.py")
        await redis.set(MODULE_INDEX_GENERATION_KEY, "stale-service-generation")
    monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")
    for key in ("BIFROST_ACCESS_TOKEN", "BIFROST_REFRESH_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    context = _context_for(name, path, token, str(attempt.id))
    context.pop("engine_token")
    context["is_platform_admin"] = False
    context["organization"] = {"id": str(org.id), "name": org.name, "is_active": True}
    context["service"] = {
        "service_id": str(definition.id), "attempt_id": str(attempt.id),
        "lease_token": attempt.lease_token, "token": token, "token_expires_at": expires_at,
    }
    server = WorkerSdkHttpServer()
    await server.start()
    template = TemplateProcess()
    template.start()
    try:
        child_pid, work_queue, result_queue = template.fork(
            worker_id=f"cold-service-{tag}", sdk_socket_path=server.socket_path,
        )
        work_queue.put((str(attempt.id), context))
        envelope = await asyncio.to_thread(result_queue.get, True, 120.0)
        assert envelope["success"] is True, envelope
        assert envelope["result"] == {
            "value": f"service-{tag}", "socket": True, "has_db": False, "has_storage": False,
        }
        _wait_for_pid_to_die(child_pid)
    finally:
        template.shutdown()
        await server.stop()
        await _drop_s3(path)
        await _drop_s3(dep_path)
        await redis.delete(MODULE_INDEX_KEY, MODULE_INDEX_GENERATION_KEY)
        await db_session.execute(delete(ServiceAttempt).where(ServiceAttempt.id == attempt.id))
        await db_session.execute(delete(ServiceDefinition).where(ServiceDefinition.id == definition.id))
        await db_session.execute(delete(Workflow).where(Workflow.id == workflow.id))
        await db_session.execute(delete(Organization).where(Organization.id == org.id))
        await db_session.commit()
