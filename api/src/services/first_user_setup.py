"""Transaction-scoped first-run gate and fixed, explicitly consented role grant.

This is internal to credential registration, not an authenticated self-grant API.
Callers hold the singleton row lock until credential, grant and audit commit.
"""

from shared.builtin_roles import DECRYPTION_ROLE_ID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import PROVIDER_ORG_ID
from src.models.orm.users import PlatformSetup, User
from src.repositories.users import UserRepository
from src.services.audit import emit_audit
from src.services.audit_context import ActorContext
from src.services.user_role_assignments import (
    SECRETS_READER_BOUNDARIES,
    insert_assignment,
)


async def setup_available(db: AsyncSession, *, lock: bool = False) -> bool:
    statement = select(PlatformSetup).where(PlatformSetup.id.is_(True))
    if lock:
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )
    state = (await db.execute(statement)).scalar_one()
    return not state.completed and not await UserRepository(db).has_any_users()


async def complete_first_user_setup(
    db: AsyncSession,
    user: User,
    *,
    grant_secrets_reader: bool = False,
) -> None:
    """Finish a locked, fresh bootstrap in the caller's credential transaction."""
    state = await db.get(PlatformSetup, True)
    if state is None or state.completed:
        raise ValueError("First-time setup has already completed")
    if (
        not user.is_superuser
        or user.organization_id != PROVIDER_ORG_ID
        or user.identity_kind
    ):
        raise ValueError("First-time setup requires the new provider administrator")
    if grant_secrets_reader:
        await insert_assignment(
            db,
            user_id=user.id,
            role_id=DECRYPTION_ROLE_ID,
            boundaries=SECRETS_READER_BOUNDARIES,
            assigned_by=user.email,
        )
    state.completed = True
    await emit_audit(
        db,
        "auth.setup_completed",
        resource_type="user",
        resource_id=user.id,
        actor_override=ActorContext(
            user_id=user.id,
            organization_id=PROVIDER_ORG_ID,
            email=user.email,
            source="setup",
        ),
        details={
            "grant_secrets_reader": grant_secrets_reader,
            "role_id": str(DECRYPTION_ROLE_ID) if grant_secrets_reader else None,
            "boundaries": sorted(b.kind.value for b in SECRETS_READER_BOUNDARIES)
            if grant_secrets_reader
            else [],
            "provider_organization_id": str(PROVIDER_ORG_ID),
        },
        strict=True,
    )
    await db.flush()
