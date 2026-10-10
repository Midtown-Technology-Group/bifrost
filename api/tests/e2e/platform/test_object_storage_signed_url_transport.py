"""Retain the real signed-URL transport signal outside the in-memory unit fake."""

import pytest
from src.config import get_settings

from tests.contract.test_object_storage_contract import (
    S3ObjectStorage,
    assert_signed_url_http_round_trip,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_signed_url_http_round_trip_uses_actual_test_stack_storage():
    settings = get_settings()
    bucket, endpoint, access, secret = (
        settings.s3_bucket,
        settings.s3_endpoint_url,
        settings.s3_access_key,
        settings.s3_secret_key,
    )
    assert bucket and endpoint and access and secret, (
        "Test stack object storage must be configured"
    )
    storage = S3ObjectStorage(
        bucket=bucket,
        endpoint_url=endpoint,
        access_key_id=access,
        secret_access_key=secret,
        region_name=settings.s3_region,
    )
    try:
        await assert_signed_url_http_round_trip(storage)
    finally:
        await storage.cleanup()
