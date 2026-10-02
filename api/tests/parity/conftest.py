"""Parity-only lifecycle; no global fixture/setup modifications."""

import os
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

from tests.parity.harness import DeviceAdapter, PostgresRedisCapture, SeededEnvironment


@pytest_asyncio.fixture
async def parity_environment(async_engine):
    environment = SeededEnvironment(async_engine)
    await environment.seed()
    try:
        yield environment
    finally:
        await environment.cleanup()


@pytest_asyncio.fixture
async def python_adapter(parity_environment):
    async with PostgresRedisCapture(parity_environment, os.environ["BIFROST_REDIS_URL"]) as capture:
        adapter = DeviceAdapter("python", os.environ["TEST_API_URL"], capture)
        try:
            yield adapter
        finally:
            await adapter.close()


@pytest.fixture
def independent_python_environment(async_engine):
    @asynccontextmanager
    async def create():
        environment = SeededEnvironment(async_engine)
        await environment.seed()
        try:
            async with PostgresRedisCapture(environment, os.environ["BIFROST_REDIS_URL"]) as capture:
                adapter = DeviceAdapter("python", os.environ["TEST_API_URL"], capture)
                try:
                    yield environment, adapter
                finally:
                    await adapter.close()
        finally:
            await environment.cleanup()
    return create
