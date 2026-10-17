"""Regression tests for treating `.bifrost/` as ordinary workspace files."""

from unittest.mock import AsyncMock, Mock, call

import pytest


@pytest.mark.asyncio
async def test_delete_file_allows_bifrost_paths():
    """delete_file() should not special-case or block `.bifrost/` paths."""
    from src.services.file_storage.file_ops import FileOperationsService

    service = FileOperationsService.__new__(FileOperationsService)
    service._delete_from_s3 = AsyncMock()
    service._remove_from_search_index = AsyncMock()
    service._handle_app_file_cleanup = AsyncMock()
    service._remove_metadata = AsyncMock()
    service._invalidate_module_cache_if_python = AsyncMock()
    operations = Mock()
    operations.attach_mock(service._invalidate_module_cache_if_python, "invalidate")
    operations.attach_mock(service._delete_from_s3, "delete_from_s3")
    operations.attach_mock(service._remove_metadata, "remove_metadata")
    operations.attach_mock(service._remove_from_search_index, "remove_from_search_index")
    operations.attach_mock(service._handle_app_file_cleanup, "handle_app_file_cleanup")

    await service.delete_file(".bifrost/workflows.yaml")

    service._delete_from_s3.assert_awaited_once_with(".bifrost/workflows.yaml")
    service._remove_from_search_index.assert_awaited_once_with(".bifrost/workflows.yaml")
    service._handle_app_file_cleanup.assert_awaited_once_with(".bifrost/workflows.yaml")
    service._remove_metadata.assert_awaited_once_with(".bifrost/workflows.yaml")
    service._invalidate_module_cache_if_python.assert_has_awaits([
        call(".bifrost/workflows.yaml"),
        call(".bifrost/workflows.yaml"),
    ])
    assert service._invalidate_module_cache_if_python.await_count == 2
    assert operations.mock_calls == [
        call.invalidate(".bifrost/workflows.yaml"),
        call.delete_from_s3(".bifrost/workflows.yaml"),
        call.invalidate(".bifrost/workflows.yaml"),
        call.remove_metadata(".bifrost/workflows.yaml"),
        call.remove_from_search_index(".bifrost/workflows.yaml"),
        call.handle_app_file_cleanup(".bifrost/workflows.yaml"),
    ]
