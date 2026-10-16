"""Concurrency coverage for orphaned-workflow file recreation."""

import asyncio
import hashlib
from contextlib import suppress
from uuid import uuid4

import pytest
from sqlalchemy import delete, text
from unittest.mock import AsyncMock

from src.models.orm.file_index import FileIndex
from src.models.orm.workflows import Workflow
from src.services.workflow_orphan import WorkflowOrphanService

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_concurrent_repoint_cannot_recreate_old_source_at_new_path(
    async_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A waiting recreation re-reads after the workspace transaction fence."""
    suffix = uuid4().hex[:8]
    workflow_id = uuid4()
    function_name = f"recreate_{suffix}"
    old_path = f"workflows/recreate_old_{suffix}.py"
    new_path = f"workflows/recreate_new_{suffix}.py"
    old_code = (
        "from src.sdk import workflow\n\n"
        "@workflow\n"
        f"def {function_name}():\n"
        "    return 'old'\n"
    )
    new_code = (
        "from src.sdk import workflow\n\n"
        "@workflow\n"
        f"def {function_name}():\n"
        "    return 'new'\n"
    )

    async with async_session_factory() as setup:
        setup.add_all([
            Workflow(
                id=workflow_id,
                name=f"Recreation {suffix}",
                function_name=function_name,
                path=old_path,
                type="workflow",
                is_active=False,
                is_orphaned=True,
                solution_id=None,
            ),
            FileIndex(
                path=old_path,
                content=old_code,
                content_hash=hashlib.sha256(old_code.encode()).hexdigest(),
                updated_by="test@example.com",
            ),
            FileIndex(
                path=new_path,
                content=new_code,
                content_hash=hashlib.sha256(new_code.encode()).hexdigest(),
                updated_by="test@example.com",
            ),
        ])
        await setup.commit()

    replacement_has_lock = asyncio.Event()
    finish_replacement = asyncio.Event()
    replace_task = None
    recreate_task = None
    try:
        async with (
            async_session_factory() as replacement_db,
            async_session_factory() as recreation_db,
        ):
            replacement = WorkflowOrphanService(replacement_db)
            original_read = replacement._read_file

            async def paused_read(path: str) -> str | None:
                replacement_has_lock.set()
                await finish_replacement.wait()
                return await original_read(path)

            monkeypatch.setattr(replacement, "_read_file", paused_read)
            replace_task = asyncio.create_task(
                replacement.replace_workflow(
                    workflow_id,
                    new_path,
                    function_name,
                )
            )
            await asyncio.wait_for(replacement_has_lock.wait(), 10)

            recreation_pid = await recreation_db.scalar(
                text("SELECT pg_backend_pid()")
            )
            recreation = WorkflowOrphanService(recreation_db)
            selected_source = AsyncMock(return_value=old_code)
            monkeypatch.setattr(recreation, "_get_code", selected_source)
            recreate_task = asyncio.create_task(
                recreation.recreate_file(workflow_id)
            )

            async def wait_for_recreation_lock_wait() -> None:
                async with async_session_factory() as observer:
                    while not await observer.scalar(
                        text(
                            "SELECT EXISTS ("
                            "SELECT 1 FROM pg_locks "
                            "WHERE pid=:pid AND locktype='advisory' AND NOT granted)"
                        ),
                        {"pid": recreation_pid},
                    ):
                        if recreate_task.done():
                            raise AssertionError(
                                "recreation passed the concurrent replacement fence"
                            )
                        await asyncio.sleep(0.01)

            await asyncio.wait_for(wait_for_recreation_lock_wait(), 10)
            finish_replacement.set()
            replaced = await asyncio.wait_for(replace_task, 10)
            assert replaced.path == new_path

            with pytest.raises(ValueError, match="is not orphaned"):
                await asyncio.wait_for(recreate_task, 10)
            selected_source.assert_not_awaited()
            await recreation_db.rollback()
    finally:
        finish_replacement.set()
        for task in (replace_task, recreate_task):
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        async with async_session_factory() as cleanup:
            await cleanup.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await cleanup.execute(
                delete(FileIndex).where(FileIndex.path.in_([old_path, new_path]))
            )
            await cleanup.commit()
