"""Real database scope and audit evidence for privileged workspace operations."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.principal import UserPrincipal
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.services import workspace_admin as service

pytestmark = pytest.mark.asyncio


async def test_oauth_reconciliation_and_diagnostics_never_cross_token_org(
    db_session: AsyncSession, seed_user: User
):
    org = Organization(name="Boundary test " + uuid4().hex, created_by="test")
    db_session.add(org)
    await db_session.flush()
    name = "Boundary-" + uuid4().hex
    provider = OAuthProvider(
        provider_name=name,
        client_id="test",
        encrypted_client_secret=b"",
        status="not_connected",
    )
    db_session.add(provider)
    await db_session.flush()
    now = datetime.now(UTC)
    global_token = OAuthToken(
        provider_id=provider.id,
        organization_id=None,
        encrypted_access_token=b"global-access",
        encrypted_refresh_token=b"global-refresh",
        expires_at=now + timedelta(days=2),
    )
    scoped_token = OAuthToken(
        provider_id=provider.id,
        organization_id=org.id,
        encrypted_access_token=b"org-access",
        encrypted_refresh_token=b"org-refresh",
        expires_at=now + timedelta(days=1),
    )
    user_token = OAuthToken(
        provider_id=provider.id,
        organization_id=org.id,
        user_id=seed_user.id,
        encrypted_access_token=b"user-access",
        encrypted_refresh_token=b"user-refresh",
        expires_at=now + timedelta(days=3),
    )
    db_session.add_all([global_token, scoped_token, user_token])
    await db_session.flush()
    admin = UserPrincipal(seed_user.id, seed_user.email, None, is_superuser=True)
    result = await service.inspect_oauth(db_session, admin, name, org.id)
    assert [item.token_id for item in result.tokens] == [str(scoped_token.id)]
    result = await service.reconcile_oauth(db_session, admin, name, org.id)
    assert result.token_rows_updated == 1
    assert result.provider_rows_updated == 0
    await db_session.refresh(global_token)
    await db_session.refresh(user_token)
    await db_session.refresh(provider)
    assert global_token.encrypted_refresh_token == b"global-refresh"
    assert user_token.encrypted_refresh_token == b"user-refresh"
    assert provider.status == "not_connected"
    assert scoped_token.encrypted_refresh_token == b"org-refresh"


async def test_execution_redaction_exact_target_scope_and_audit(
    db_session: AsyncSession, seed_user: User
):
    from src.models.orm.audit import AuditLog

    org = Organization(name="Boundary test " + uuid4().hex, created_by="test")
    db_session.add(org)
    await db_session.flush()
    target = Execution(
        workflow_name="boundary-test",
        executed_by_name="test",
        organization_id=org.id,
        status=ExecutionStatus.SUCCESS,
        parameters={"secret": "target"},
        result={"secret": "result"},
        variables={"secret": "variable"},
        execution_context={"secret": "context"},
        error_message="sensitive",
    )
    other = Execution(
        workflow_name="boundary-test",
        executed_by_name="test",
        organization_id=None,
        status=ExecutionStatus.SUCCESS,
        parameters={"secret": "other"},
    )
    db_session.add_all([target, other])
    await db_session.flush()
    admin = UserPrincipal(seed_user.id, seed_user.email, None, is_superuser=True)
    with pytest.raises(service.AdminOperationError) as error:
        await service.redact_execution(db_session, admin, target.id, "global")
    assert error.value.status_code == 404
    await service.redact_execution(db_session, admin, target.id)
    for _ in range(2):
        await service.redact_execution(db_session, admin, target.id, org.id)
    await db_session.refresh(target)
    await db_session.refresh(other)
    assert (
        target.parameters
        == target.result
        == target.variables
        == target.execution_context
        == {"redacted": True}
    )
    assert target.error_message is None
    assert target.status == ExecutionStatus.SUCCESS
    assert other.parameters == {"secret": "other"}
    audits = list(
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.resource_id == target.id,
                    AuditLog.action == "execution.redact_sensitive_fields",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 3
    assert all(
        audit.user_id == seed_user.id and audit.organization_id == org.id
        for audit in audits
    )
    assert "target" not in str([audit.details for audit in audits])
