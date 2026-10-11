"""Readonly committed-grant loader for a parent-reserved isolated ingress.

This is neither issuer admission nor live custody. Only the parent chooses the
fixed identity; HTTP claims cannot select rows. No lifecycle locks or writes.
"""

from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.runtime_sdk_credentials import (
    GRANT_FIELDS,
    GrantSnapshot,
    RuntimeSDKDenied,
)
from src.models.orm.runtime_execution import (
    RuntimeAdmission,
    RuntimeSession,
    RuntimeStart,
    WorkflowRuntimeSDKGrant,
)


async def load_committed_finite_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    grant_id: UUID,
    execution_id: UUID,
    session_id: UUID,
    owner_incarnation_id: UUID,
    attempt_id: UUID,
) -> GrantSnapshot:
    """Read one real finite provision; callers still need live Rust admission."""
    g = WorkflowRuntimeSDKGrant.__table__.c
    s = RuntimeSession.__table__.c
    st = RuntimeStart.__table__.c
    a = RuntimeAdmission.__table__.c
    query = (
        select(*(g[name] for name in GRANT_FIELDS))
        .select_from(WorkflowRuntimeSDKGrant.__table__)
        .join(
            RuntimeSession.__table__,
            and_(
                s.id == g.runtime_session_id,
                s.execution_id == g.execution_id,
                s.owner_incarnation_id == g.owner_incarnation_id,
                s.workflow_attempt_id == g.workflow_attempt_id,
            ),
        )
        .join(
            RuntimeStart.__table__,
            and_(
                st.id == g.committed_start_id,
                st.session_id == s.id,
                st.start_message_id == g.start_message_id,
            ),
        )
        .join(
            RuntimeAdmission.__table__,
            and_(
                a.grant_id == g.id,
                a.session_id == s.id,
                a.purpose == "provision",
                a.committed_start_id == st.id,
                a.start_message_id == st.start_message_id,
                a.operations_digest == g.operations_digest,
                a.expires_at == g.initial_access_expires_at,
            ),
        )
        .where(
            g.id == grant_id,
            g.execution_id == execution_id,
            g.runtime_session_id == session_id,
            g.owner_incarnation_id == owner_incarnation_id,
            g.workflow_attempt_id == attempt_id,
            s.closed_at.is_(None),
            g.revoked_at.is_(None),
            g.timeout_seconds > 0,
            g.credential_deadline == g.initial_access_expires_at,
            g.initial_access_expires_at > func.clock_timestamp(),
        )
    )
    async with session_factory() as session:
        row = (await session.execute(query)).mappings().one_or_none()
    if row is None:
        raise RuntimeSDKDenied("committed runtime SDK snapshot denied")
    return GrantSnapshot.model_validate(dict(row))
