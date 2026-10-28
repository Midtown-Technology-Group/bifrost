"""First-run consent, exercised against the migrated test database only."""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from shared.builtin_roles import DECRYPTION_ROLE_ID, PLATFORM_ADMIN_ROLE_ID
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.constants import SYSTEM_USER_UUID
from src.models import AuditLog, User, UserRole, UserRoleBoundary
from src.models.contracts.auth import RegisterRequest
from src.models.orm.users import PlatformSetup
from src.routers.auth import register_user
from src.services.authorization.context import Boundary
from src.services.user_role_assignments import SECRETS_READER_BOUNDARIES
from starlette.requests import Request


@pytest_asyncio.fixture
async def setup_db(async_engine):
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            await session.execute(
                delete(User).where(
                    User.identity_kind.is_(None), User.id != SYSTEM_USER_UUID
                )
            )
            state = await session.get(PlatformSetup, True)
            state.completed = False
            await session.flush()
            # Release only this session's SAVEPOINT into the outer test transaction,
            # so a registration rollback preserves the deliberately fresh fixture.
            await session.commit()
            yield session
            await session.rollback()
        await transaction.rollback()


async def register(db, email="first@example.com", **choices):
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1234)})
    with patch("src.routers.auth.auth_limiter.check", new=AsyncMock()):
        return await register_user(
            request,
            RegisterRequest(
                email=email,
                password="Test-only-credential-123!",
                **choices,
            ),
            db,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("consent", [None, False, True])
async def test_first_admin_consent_is_explicit_exact_and_audited(setup_db, consent):
    user = await register(
        setup_db, **({} if consent is None else {"grant_secrets_reader": consent})
    )
    from uuid import UUID

    user_id = UUID(user.id)
    roles = set(
        (
            await setup_db.scalars(
                select(UserRole.role_id).where(UserRole.user_id == user_id)
            )
        ).all()
    )
    assert roles == (
        {PLATFORM_ADMIN_ROLE_ID, DECRYPTION_ROLE_ID}
        if consent
        else {PLATFORM_ADMIN_ROLE_ID}
    )
    rows = (
        await setup_db.scalars(
            select(UserRoleBoundary).where(
                UserRoleBoundary.user_id == user_id,
                UserRoleBoundary.role_id == DECRYPTION_ROLE_ID,
            )
        )
    ).all()
    assert {Boundary(r.kind, r.organization_id) for r in rows} == (
        SECRETS_READER_BOUNDARIES if consent else set()
    )
    audit = (
        await setup_db.scalars(
            select(AuditLog).where(
                AuditLog.action == "auth.setup_completed",
                AuditLog.resource_id == user_id,
            )
        )
    ).one()
    assert audit.user_id == user_id
    assert audit.details["grant_secrets_reader"] is bool(consent)
    assert (await setup_db.get(PlatformSetup, True)).completed


@pytest.mark.asyncio
async def test_completed_setup_refuses_replay_and_other_user_consent(setup_db):
    from fastapi import HTTPException

    await register(setup_db)
    for email in ("first@example.com", "other@example.com"):
        with pytest.raises(HTTPException, match="first-time setup"):
            await register(setup_db, email=email, grant_secrets_reader=True)
    assert not (
        await setup_db.scalars(
            select(UserRole).where(UserRole.role_id == DECRYPTION_ROLE_ID)
        )
    ).all()


@pytest.mark.asyncio
async def test_deleting_humans_does_not_reopen_bootstrap(setup_db):
    from src.services.first_user_setup import setup_available

    await register(setup_db)
    await setup_db.execute(
        delete(User).where(User.identity_kind.is_(None), User.id != SYSTEM_USER_UUID)
    )
    assert not await setup_available(setup_db)


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_account_grant_and_gate(setup_db):
    from src.repositories.audit_logs import AuditLogRepository

    with (
        patch.object(
            AuditLogRepository,
            "create",
            new=AsyncMock(side_effect=RuntimeError("audit unavailable")),
        ),
        pytest.raises(RuntimeError, match="audit unavailable"),
    ):
        await register(setup_db, grant_secrets_reader=True)
    await setup_db.rollback()
    assert not (
        await setup_db.scalars(select(User).where(User.email == "first@example.com"))
    ).all()
    assert not (await setup_db.get(PlatformSetup, True)).completed


@pytest.mark.asyncio
@pytest.mark.parametrize("consent", [False, True])
async def test_passkey_consent_is_bound_to_challenge_and_single_use(setup_db, consent):
    import json
    from types import SimpleNamespace

    from src.services.passkey_service import PasskeyService

    service = PasskeyService(setup_db)
    token, _ = await service.generate_setup_registration_options(
        "passkey@example.com", grant_secrets_reader=consent
    )
    verified = SimpleNamespace(
        credential_id=b"test-only-id",
        credential_public_key=b"fake-public-key",
        sign_count=0,
        credential_device_type="singleDevice",
        credential_backed_up=False,
    )
    credential = SimpleNamespace(response=SimpleNamespace(transports=[]))
    with (
        patch(
            "src.services.passkey_service.parse_registration_credential_json",
            return_value=credential,
        ),
        patch(
            "src.services.passkey_service.verify_registration_response",
            return_value=verified,
        ),
    ):
        user, _ = await service.verify_setup_registration(token, json.dumps({}))
        roles = set(
            (
                await setup_db.scalars(
                    select(UserRole.role_id).where(UserRole.user_id == user.id)
                )
            ).all()
        )
        assert (DECRYPTION_ROLE_ID in roles) is consent
        with pytest.raises(ValueError, match="not found or expired"):
            await service.verify_setup_registration(token, "{}")


@pytest.mark.asyncio
async def test_invalid_or_stale_passkey_proof_cannot_grant(setup_db):
    from types import SimpleNamespace

    from src.services.passkey_service import PasskeyService

    service = PasskeyService(setup_db)
    with pytest.raises(ValueError, match="not found or expired"):
        await service.verify_setup_registration("expired-or-unknown", "{}")
    token, _ = await service.generate_setup_registration_options(
        "wrong@example.com", grant_secrets_reader=True
    )
    with (
        patch(
            "src.services.passkey_service.parse_registration_credential_json",
            return_value=SimpleNamespace(),
        ),
        patch(
            "src.services.passkey_service.verify_registration_response",
            side_effect=ValueError("wrong challenge"),
        ),
        pytest.raises(ValueError, match="wrong challenge"),
    ):
        await service.verify_setup_registration(token, "{}")
    assert not (await setup_db.get(PlatformSetup, True)).completed
    assert not await setup_db.scalar(
        select(User.id).where(User.email == "wrong@example.com")
    )


@pytest.mark.asyncio
async def test_later_users_and_promotions_receive_no_reader(setup_db):
    from uuid import UUID

    from shared.sdk_users import set_platform_admin
    from src.repositories.users import UserRepository

    await register(setup_db)
    later = await UserRepository(setup_db).create_user(
        email="later@example.com",
        organization_id=UUID("00000000-0000-0000-0000-000000000002"),
    )
    await set_platform_admin(setup_db, later, True, assigned_by="test")
    assert not await setup_db.scalar(
        select(UserRole).where(
            UserRole.user_id == later.id, UserRole.role_id == DECRYPTION_ROLE_ID
        )
    )


@pytest.mark.asyncio
async def test_passkey_challenge_created_before_other_setup_is_stale(setup_db):
    from types import SimpleNamespace

    from src.services.passkey_service import PasskeyService

    service = PasskeyService(setup_db)
    token, _ = await service.generate_setup_registration_options(
        "stale@example.com", grant_secrets_reader=True
    )
    await register(setup_db)
    with (
        patch(
            "src.services.passkey_service.parse_registration_credential_json",
            return_value=SimpleNamespace(),
        ),
        patch(
            "src.services.passkey_service.verify_registration_response",
            return_value=SimpleNamespace(),
        ),
        pytest.raises(ValueError, match="Another user"),
    ):
        await service.verify_setup_registration(token, "{}")
    assert not await setup_db.scalar(
        select(UserRole).where(UserRole.role_id == DECRYPTION_ROLE_ID)
    )


@pytest.mark.asyncio
async def test_bootstrap_does_not_bypass_self_role_guard_but_second_admin_can_grant(
    setup_db,
):
    from uuid import UUID

    from shared.builtin_roles import USER_ROLE_ID
    from src.core.constants import PROVIDER_ORG_ID
    from src.models.contracts.role_assignments import UserRoleAssignmentsUpdate
    from src.repositories.users import UserRepository
    from src.services.user_role_assignments import (
        RoleAssignmentError,
        replace_role_assignments,
    )

    from tests.helpers.authorization import admin_caller

    first = await register(setup_db)
    first_id = UUID(first.id)
    request = UserRoleAssignmentsUpdate(
        base_role_id=USER_ROLE_ID,
        additional=[
            {"role_id": PLATFORM_ADMIN_ROLE_ID},
            {"role_id": DECRYPTION_ROLE_ID},
        ],
    )
    with pytest.raises(RoleAssignmentError, match="own role assignments"):
        await replace_role_assignments(
            setup_db, admin_caller(user_id=first_id), user_id=first_id, request=request
        )
    second = await UserRepository(setup_db).create_user(
        email="second@example.com", organization_id=PROVIDER_ORG_ID, is_superuser=True
    )
    await replace_role_assignments(
        setup_db, admin_caller(user_id=second.id), user_id=first_id, request=request
    )
    assert await setup_db.scalar(
        select(UserRole).where(
            UserRole.user_id == first_id, UserRole.role_id == DECRYPTION_ROLE_ID
        )
    )
    assert not await setup_db.scalar(
        select(UserRole).where(
            UserRole.user_id == second.id, UserRole.role_id == DECRYPTION_ROLE_ID
        )
    )


def test_consent_requires_a_boolean_and_cannot_be_added_to_passkey_verification():
    from pydantic import ValidationError
    from src.models.contracts.passkeys import (
        SetupPasskeyOptionsRequest,
        SetupPasskeyVerifyRequest,
    )

    with pytest.raises(ValidationError):
        RegisterRequest(
            email="first@example.com", password="test-only", grant_secrets_reader="true"
        )
    assert not SetupPasskeyOptionsRequest(
        email="first@example.com"
    ).grant_secrets_reader
    verification = SetupPasskeyVerifyRequest(
        registration_token="test", credential={}, grant_secrets_reader=True
    )
    assert "grant_secrets_reader" not in verification.model_dump()
