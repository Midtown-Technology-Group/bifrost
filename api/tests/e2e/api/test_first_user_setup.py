"""Public registration races and upgrade safety on disposable migrated databases."""

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from src.core.constants import SYSTEM_USER_UUID
from src.core.database import get_db
from src.models import User, UserRole
from src.models.orm.users import PlatformSetup
from src.routers.auth import router

from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    _create_database,
    _direct_database_url,
    _drop_database,
    _upgrade,
)


@pytest_asyncio.fixture
async def fresh_database():
    # Reuse the migration harness's restricted database-name allowlist.
    name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    await _create_database(name)
    url = _direct_database_url(name)
    try:
        yield url
    finally:
        await _drop_database(name)


@pytest.mark.asyncio
@pytest.mark.parametrize("race", ["passwords", "mixed", "passkeys", "same_token"])
async def test_concurrent_public_setup_has_one_winner_and_cannot_replay(
    fresh_database, race
):
    await asyncio.to_thread(_upgrade, fresh_database, "head")
    engine = create_async_engine(fresh_database)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router)

    async def database():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = database
    from types import SimpleNamespace

    from src.services.passkey_service import PasskeyService

    tokens = []
    async with sessions() as db:
        if race != "passwords":
            for n in range(2):
                token, _ = await PasskeyService(db).generate_setup_registration_options(
                    f"passkey-{n}@example.com",
                    grant_secrets_reader=True,
                )
                tokens.append(token)
        await db.commit()
    verified = SimpleNamespace(
        credential_id=b"test-id",
        credential_public_key=b"test-public-key",
        sign_count=0,
        credential_device_type="singleDevice",
        credential_backed_up=False,
    )
    credential = SimpleNamespace(response=SimpleNamespace(transports=[]))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            with (
                patch("src.routers.auth.auth_limiter.check", new=AsyncMock()),
                patch(
                    "src.services.passkey_service.parse_registration_credential_json",
                    return_value=credential,
                ),
                patch(
                    "src.services.passkey_service.verify_registration_response",
                    return_value=verified,
                ),
            ):
                bodies = [
                    {
                        "email": f"first-{n}@example.com",
                        "password": "Test-only-password-123!",
                        "grant_secrets_reader": True,
                    }
                    for n in range(2)
                ]
                requests = (
                    [client.post("/auth/register", json=body) for body in bodies]
                    if race == "passwords"
                    else []
                )
                if race == "mixed":
                    requests = [
                        client.post("/auth/register", json=bodies[0]),
                        client.post(
                            "/auth/setup/passkey/verify",
                            json={"registration_token": tokens[0], "credential": {}},
                        ),
                    ]
                elif race in ("passkeys", "same_token"):
                    requests = [
                        client.post(
                            "/auth/setup/passkey/verify",
                            json={
                                "registration_token": tokens[
                                    0 if race == "same_token" else n
                                ],
                                "credential": {},
                            },
                        )
                        for n in range(2)
                    ]
                responses = await asyncio.gather(*requests)
                assert sum(r.status_code in (200, 201) for r in responses) == 1, [
                    r.text for r in responses
                ]
                assert sum(r.status_code == 400 for r in responses) == 1, [
                    r.text for r in responses
                ]
                for body in bodies:
                    assert (
                        await client.post("/auth/register", json=body)
                    ).status_code == 400
                # Authenticated callers have no additional bootstrap authority either.
                assert (
                    await client.post(
                        "/auth/register",
                        json=bodies[0],
                        headers={"Authorization": "Bearer invalid"},
                    )
                ).status_code == 400
        async with sessions() as db:
            people = (
                await db.scalars(
                    select(User).where(
                        User.identity_kind.is_(None),
                        User.organization_id.is_not(None),
                        User.id != SYSTEM_USER_UUID,
                    )
                )
            ).all()
            assert len(people) == 1
            assert (
                len(
                    (
                        await db.scalars(
                            select(UserRole).where(UserRole.user_id == people[0].id)
                        )
                    ).all()
                )
                == 2
            )
            assert (await db.get(PlatformSetup, True)).completed
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_human", [False, True])
async def test_upgrade_closes_existing_install_without_changing_assignments(
    fresh_database, has_human
):
    await asyncio.to_thread(
        _upgrade, fresh_database, "20261025_remote_worker_ownership"
    )
    engine = create_async_engine(fresh_database)
    user_id = uuid4()
    try:
        async with engine.begin() as db:
            if has_human:
                await db.execute(
                    text("""INSERT INTO users (id, email, organization_id, base_role_id)
                    VALUES (:id, 'existing@example.com', '00000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000006')"""),
                    {"id": user_id},
                )
            before = (
                await db.execute(
                    text(
                        "SELECT user_id, role_id FROM user_roles ORDER BY user_id, role_id"
                    )
                )
            ).all()
        await asyncio.to_thread(_upgrade, fresh_database, "head")
        async with engine.begin() as db:
            assert await db.scalar(select(PlatformSetup.completed))
            assert (
                await db.execute(
                    text(
                        "SELECT user_id, role_id FROM user_roles ORDER BY user_id, role_id"
                    )
                )
            ).all() == before
            await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
            assert await db.scalar(select(PlatformSetup.completed))
    finally:
        await engine.dispose()
