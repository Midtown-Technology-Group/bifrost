"""
Users Router

List and manage users, view user roles and forms.
"""

import logging
from typing import Literal
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from shared import access_checks
from src.config import get_settings
from src.core.auth import CurrentActiveUser, CurrentSuperuser
from src.core.db_deps import DbSession
from src.services.access_explain import entry_for_key, test_access
from src.services.authorization.enforce import (
    Caller,
    load_caller,
    operation_reach,
    org_target,
    privileged_user_ids,
    require_operation,
    require_unprotected,
)
from src.services.events import emit_event
from src.services.user_access_map import get_user_access_map as get_user_access_map_service
from src.services.user_invite_service import UserInviteService
from src.services.user_mfa_reset import reset_user_mfa as reset_user_mfa_service
from src.services.user_role_assignments import (
    RoleAssignmentError,
    get_role_assignments as get_role_assignments_service,
    replace_role_assignments as replace_role_assignments_service,
)
from src.models import User as UserORM, UserRole as UserRoleORM, FormRole as FormRoleORM, Organization as OrganizationORM
from src.models import (
    BulkUserOperation,
    BulkUserResponse,
    UserCreate,
    UserPublic,
    UserUpdate,
    UserRolesResponse,
    UserFormsResponse,
)
from src.models.contracts.access_checks import AccessCheckRequest, AccessTrace
from src.models.contracts.role_assignments import (
    UserRoleAssignmentsResponse,
    UserRoleAssignmentsUpdate,
)
from src.models.contracts.user_access import UserAccessMap
from src.models.contracts.users import UserMfaResetResponse
from src.models.contracts.user_invites import (
    CreateInviteResponse,
    SendInviteRequest,
)
from src.models.orm.workflows import Workflow as WorkflowORM
from src.services.operation_catalog import operation_route

from src.models.orm.external_identities import ExternalIdentity
from src.models.orm.organizations import Organization
from shared.models import ExternalIdentityCreateRequest, ExternalIdentityResponse
from src.services.audit import emit_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["Users"])


@router.get("/{user_id}/external-identities", response_model=list[ExternalIdentityResponse], **operation_route("users.list_external_identities"))
async def list_external_identities(
    user_id: UUID, user: CurrentSuperuser, db: DbSession,
) -> list[ExternalIdentityResponse]:
    rows = (await db.execute(
        select(ExternalIdentity).where(ExternalIdentity.user_id == user_id)
    )).scalars().all()
    return [ExternalIdentityResponse.model_validate(row, from_attributes=True) for row in rows]


@router.post("/{user_id}/external-identities", response_model=ExternalIdentityResponse, status_code=201, **operation_route("users.create_external_identity"))
async def create_external_identity(
    user_id: UUID, request: ExternalIdentityCreateRequest,
    user: CurrentSuperuser, db: DbSession,
) -> ExternalIdentityResponse:
    target = await db.get(UserORM, user_id)
    if target is None or not target.is_active or target.is_system:
        raise HTTPException(status_code=404, detail="Active user not found")
    if request.authorized_organization_id is not None:
        home_org = await db.get(Organization, target.organization_id)
        granted_org = await db.get(Organization, request.authorized_organization_id)
        if (
            home_org is None or not home_org.is_active or not home_org.is_provider
            or granted_org is None or not granted_org.is_active
            or granted_org.id == home_org.id
        ):
            raise HTTPException(status_code=400, detail="Invalid provider organization grant")
    identity = ExternalIdentity(
        user_id=user_id,
        provider=request.provider,
        external_scope_id=request.external_scope_id,
        external_user_id=request.external_user_id,
        authorized_organization_id=request.authorized_organization_id,
    )
    db.add(identity)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="External actor is already linked") from exc
    await emit_audit(
        db, "external_identity.create", resource_type="external_identity",
        resource_id=identity.id,
        details={
            "provider": identity.provider,
            "user_id": str(user_id),
            "authorized_organization_id": (
                str(identity.authorized_organization_id)
                if identity.authorized_organization_id else None
            ),
        },
        strict=True,
    )
    await db.commit()
    return ExternalIdentityResponse.model_validate(identity, from_attributes=True)


@router.delete("/{user_id}/external-identities/{identity_id}", status_code=204, **operation_route("users.delete_external_identity"))
async def delete_external_identity(
    user_id: UUID, identity_id: UUID, user: CurrentSuperuser, db: DbSession,
) -> None:
    identity = await db.get(ExternalIdentity, identity_id)
    if identity is None or identity.user_id != user_id:
        raise HTTPException(status_code=404, detail="External identity not found")
    await db.delete(identity)
    await emit_audit(
        db, "external_identity.delete", resource_type="external_identity",
        resource_id=identity_id,
        details={"provider": identity.provider, "user_id": str(user_id)},
        strict=True,
    )
    await db.commit()


@router.get(
    "",
    response_model=list[UserPublic],
    summary="List users",
    description="List all users with optional filtering by type and organization",
**operation_route("users.list"))
async def list_users(
    user: CurrentActiveUser,
    db: DbSession,
    response: Response,
    type: str | None = Query(None, description="Filter by user type: 'platform' or 'org'"),
    scope: str | None = Query(
        None,
        description="Filter scope: omit for all (superusers), 'global' for global only, "
        "or org UUID for specific org."
    ),
    include_inactive: bool = Query(False, description="Include inactive (disabled) users"),
    search: str | None = Query(None, description="Search user name or email"),
    sort_by: Literal["name", "email", "status", "created", "last_login"] | None = Query(
        None, description="Sort field; omit to preserve the legacy email order"
    ),
    sort_direction: Literal["asc", "desc"] = Query("asc"),
    limit: int | None = Query(
        None,
        ge=1,
        le=100,
        description="Maximum rows to return; omit for the legacy unbounded response",
    ),
    offset: int = Query(0, ge=0, description="Rows to skip when limit is set"),
) -> list[UserPublic]:
    """List the users the caller may read (users.read), optionally pinned
    to one organization or to Global by ``scope``.

    Note: Users are not org-scoped resources - they belong to one org.
    """
    from shared.sdk_users import UserServiceError, list_users as list_users_service

    try:
        items, total = await list_users_service(
            db,
            await load_caller(db, user),
            type=type,
            scope=scope,
            include_inactive=include_inactive,
            search=search,
            sort_by=sort_by,
            sort_direction=sort_direction,
            limit=limit,
            offset=offset,
        )
    except UserServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
    response.headers["X-Total-Count"] = str(total)
    return items


@router.post(
    "",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create user",
    description="Invite a new user into an organization (or, for Platform Admins, create a Global or Platform Admin user)",
**operation_route("users.create"))
async def create_user(
    request: UserCreate,
    user: CurrentActiveUser,
    db: DbSession,
) -> UserPublic:
    """Create a new user."""
    from shared.sdk_users import UserServiceError, create_user as create_user_service

    try:
        return await create_user_service(
            db,
            await load_caller(db, user),
            email=request.email,
            name=request.name,
            is_active=request.is_active,
            is_superuser=request.is_superuser,
            is_external=request.is_external,
            organization_id=request.organization_id,
        )
    except UserServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.patch(
    "/bulk",
    response_model=BulkUserResponse,
    summary="Bulk user operation",
    description=(
        "Apply one operation (move_org, replace_roles, set_active) to a batch of users "
        "in a single transaction. Returns per-user pass/fail."
    ),
**operation_route("users.bulk_update"))
async def bulk_update_users(
    request: BulkUserOperation,
    actor: CurrentActiveUser,
    db: DbSession,
) -> BulkUserResponse:
    """Apply a single bulk operation across N users in one transaction."""
    from shared.sdk_users import bulk_update_users as bulk_update_users_service

    return await bulk_update_users_service(db, await load_caller(db, actor), request)


@router.post(
    "/{user_id}/invite/resend",
    response_model=CreateInviteResponse,
    summary="Resend invite",
    description="Generate a fresh invite token and email it to the user.",
    responses={404: {"description": "User not found"}, 409: {"description": "User is already registered"}},
**operation_route("users.invites.resend"))
async def resend_invite(
    user_id: UUID,
    user: CurrentActiveUser,
    db: DbSession,
) -> CreateInviteResponse:
    caller = await load_caller(db, user)
    await _require_invite_target(db, caller, user_id, operation="users.invites.resend")
    return await _generate_invite(user_id=user_id, actor=user, db=db, send=True)


@router.post(
    "/{user_id}/invite/send",
    response_model=CreateInviteResponse,
    summary="Send invite",
    description="Emit invite automation for an existing registration link without rotating the token.",
**operation_route("users.invites.send"))
async def send_invite(
    user_id: UUID,
    request: SendInviteRequest,
    user: CurrentActiveUser,
    db: DbSession,
) -> CreateInviteResponse:
    caller = await load_caller(db, user)
    await _require_invite_target(db, caller, user_id, operation="users.invites.send")
    token = _extract_invite_token(request.registration_url)
    svc = UserInviteService(db)
    try:
        invite, target = await svc.get_valid_invite_user(token=token)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invite is not valid") from exc

    if target.id != user_id:
        raise HTTPException(status_code=400, detail="Invite does not belong to user")

    event_id = await _emit_user_invited_event(
        actor=user,
        invite=invite,
        reason="sent",
        registration_url=request.registration_url,
        target=target,
    )

    return CreateInviteResponse(
        user_id=user_id,
        expires_at=invite.expires_at,
        registration_url=request.registration_url,
        event_emitted=True,
        event_id=event_id,
    )


@router.post(
    "/{user_id}/invite/regenerate",
    response_model=CreateInviteResponse,
    summary="Regenerate invite link",
    description="Generate a fresh invite token without sending an email; returns the URL.",
    responses={404: {"description": "User not found"}, 409: {"description": "User is already registered"}},
**operation_route("users.invites.regenerate"))
async def regenerate_invite(
    user_id: UUID,
    user: CurrentActiveUser,
    db: DbSession,
) -> CreateInviteResponse:
    caller = await load_caller(db, user)
    await _require_invite_target(db, caller, user_id, operation="users.invites.regenerate")
    return await _generate_invite(user_id=user_id, actor=user, db=db, send=False)


@router.delete(
    "/{user_id}/invite",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke invite",
    description="Revoke any active invite for the user.",
**operation_route("users.invites.revoke"))
async def revoke_invite(
    user_id: UUID,
    user: CurrentActiveUser,
    db: DbSession,
) -> None:
    caller = await load_caller(db, user)
    target = await _require_invite_target(
        db, caller, user_id, operation="users.invites.revoke", missing_ok=True
    )
    if target is None:
        return
    svc = UserInviteService(db)
    await svc.revoke(user_id=user_id)


async def _require_invite_target(
    db, caller: Caller, user_id: UUID, *, operation: str, missing_ok: bool = False
) -> UserORM | None:
    """users.readwrite at the user's organization; a privileged user only
    for a Platform Admin. A missing user is 404 (or nothing, for revoke,
    which has always been a no-op then) to a caller with reach somewhere."""
    from shared.bridge_grants import refresh_mutation_caller, require_bridge_target_admin

    caller = await refresh_mutation_caller(db, caller)
    operation_reach(caller, operation)
    target = await db.get(UserORM, user_id, populate_existing=True)
    if target is None:
        if missing_ok:
            return None
        raise HTTPException(status_code=404, detail="User not found")
    require_operation(caller, operation, org_target(target.organization_id))
    await require_bridge_target_admin(db, caller, target.id)
    require_unprotected(caller, target.id in await privileged_user_ids(db, [target.id]))
    return target


async def _generate_invite(
    *, user_id: UUID, actor, db, send: bool
) -> CreateInviteResponse:
    target = (
        await db.execute(select(UserORM).where(UserORM.id == user_id))
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.is_registered:
        raise HTTPException(status_code=409, detail="User is already registered")

    svc = UserInviteService(db)
    raw_token, invite = await svc.create_or_replace(
        user_id=user_id, created_by=actor.user_id
    )
    registration_url = (
        f"{get_settings().public_url.rstrip('/')}/accept-invite?token={raw_token}"
    )

    event_id = None
    if send:
        event_id = await _emit_user_invited_event(
            actor=actor,
            invite=invite,
            reason="resent",
            registration_url=registration_url,
            target=target,
        )

    return CreateInviteResponse(
        user_id=user_id,
        expires_at=invite.expires_at,
        registration_url=registration_url,
        event_emitted=send,
        event_id=event_id,
    )


def _extract_invite_token(registration_url: str) -> str:
    parsed = urlparse(registration_url)
    token = parse_qs(parsed.query).get("token", [None])[0]
    if not token:
        raise HTTPException(status_code=400, detail="registration_url must include token")
    return token


async def _emit_user_invited_event(
    *,
    actor,
    invite,
    reason: str,
    registration_url: str,
    target: UserORM,
) -> UUID:
    event_id, _ = await emit_event(
        "user.invited",
        {
            "user_id": str(target.id),
            "email": target.email,
            "name": target.name or "",
            "registration_url": registration_url,
            "expires_at": invite.expires_at.isoformat(),
            "invited_by": {
                "user_id": str(actor.user_id),
                "email": actor.email,
                "name": getattr(actor, "name", None) or "",
            },
            "reason": reason,
        },
        organization_id=target.organization_id,
        triggered_by=str(actor.user_id),
    )
    return event_id


@router.get(
    "/{user_id}",
    response_model=UserPublic,
    summary="Get user details",
    description="Get a specific user's details",
**operation_route("users.get"))
async def get_user(
    user_id: str,
    user: CurrentActiveUser,
    db: DbSession,
) -> UserPublic:
    """Get a specific user's details."""
    from shared.sdk_users import UserServiceError, get_user as get_user_service

    try:
        return await get_user_service(db, await load_caller(db, user), user_id=user_id)
    except UserServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.patch(
    "/{user_id}",
    response_model=UserPublic,
    summary="Update user",
    description="Update user properties including role transitions",
**operation_route("users.update"))
async def update_user(
    user_id: str,
    request: UserUpdate,
    user: CurrentActiveUser,
    db: DbSession,
) -> UserPublic:
    """Update a user."""
    from shared.sdk_users import UserServiceError, update_user as update_user_service

    try:
        return await update_user_service(
            db,
            await load_caller(db, user),
            user_id=user_id,
            fields=set(request.model_fields_set),
            email=request.email,
            name=request.name,
            password=request.password,
            is_active=request.is_active,
            is_superuser=request.is_superuser,
            is_verified=request.is_verified,
            is_external=request.is_external,
            mfa_enabled=request.mfa_enabled,
            organization_id=request.organization_id,
        )
    except UserServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete user",
    description="Delete a user from the system",
**operation_route("users.delete"))
async def delete_user(
    user_id: str,
    user: CurrentActiveUser,
    db: DbSession,
) -> None:
    """Permanently delete a user. User must be inactive first."""
    from shared.sdk_users import UserServiceError, delete_user as delete_user_service

    try:
        await delete_user_service(db, await load_caller(db, user), user_id=user_id)
    except UserServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.post(
    "/{user_id}/mfa/reset",
    response_model=UserMfaResetResponse,
    summary="Reset a user's MFA",
    description="Remove the user's authenticator app, recovery codes, passkeys and remembered devices, "
    "and sign them out everywhere. They enroll MFA again at their next sign-in.",
**operation_route("users.mfa.reset"))
async def reset_user_mfa(
    user_id: UUID,
    user: CurrentActiveUser,
    db: DbSession,
) -> UserMfaResetResponse:
    from shared.sdk_users import UserServiceError

    try:
        return await reset_user_mfa_service(db, await load_caller(db, user), user_id)
    except UserServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.get(
    "/{user_id}/roles",
    response_model=UserRolesResponse,
    summary="Get user roles",
    description="Get all roles assigned to a user",
**operation_route("users.roles.list"))
async def get_user_roles(
    user_id: str,
    user: CurrentActiveUser,
    db: DbSession,
) -> UserRolesResponse:
    """Get all roles assigned to a user."""
    caller = await load_caller(db, user)
    db_user = await _require_assignment_reader(db, caller, user_id, operation="users.roles.list")
    user_uuid = db_user.id

    result = await db.execute(
        select(UserRoleORM.role_id).where(UserRoleORM.user_id == user_uuid)
    )
    role_ids = [str(rid) for rid in result.scalars().all()]

    return UserRolesResponse(role_ids=role_ids)


@router.get(
    "/{user_id}/forms",
    response_model=UserFormsResponse,
    summary="Get user forms",
    description="Get all forms a user can access based on their roles",
**operation_route("users.forms.list"))
async def get_user_forms(
    user_id: str,
    user: CurrentActiveUser,
    db: DbSession,
) -> UserFormsResponse:
    """Get all forms a user can access."""
    caller = await load_caller(db, user)
    db_user = await _require_assignment_reader(db, caller, user_id, operation="users.forms.list")

    # Platform admins have access to all forms
    if db_user.is_superuser:
        access_checks.note_power("forms.read", db_user.organization_id, subject=f"user:{db_user.id}")
        return UserFormsResponse(
            is_superuser=True,
            has_access_to_all_forms=True,
            form_ids=[],
        )

    # Get user's roles
    role_result = await db.execute(
        select(UserRoleORM.role_id).where(UserRoleORM.user_id == db_user.id)
    )
    role_ids = list(role_result.scalars().all())

    if not role_ids:
        return UserFormsResponse(
            is_superuser=False,
            has_access_to_all_forms=False,
            form_ids=[],
        )

    # Get forms for those roles
    form_result = await db.execute(
        select(FormRoleORM.form_id).where(FormRoleORM.role_id.in_(role_ids))
    )
    form_ids = list(set(str(fid) for fid in form_result.scalars().all()))

    return UserFormsResponse(
        is_superuser=False,
        has_access_to_all_forms=False,
        form_ids=form_ids,
    )


async def _require_assignment_reader(
    db, caller: Caller, user_id: str, *, operation: str
) -> UserORM:
    """roleassignments.read at the user's organization (user id or email)."""
    operation_reach(caller, operation)
    try:
        uuid_id = UUID(user_id)
        result = await db.execute(select(UserORM).where(UserORM.id == uuid_id))
    except ValueError:
        result = await db.execute(select(UserORM).where(UserORM.email == user_id))
    db_user = result.scalar_one_or_none()
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    require_operation(caller, operation, org_target(db_user.organization_id))
    return db_user


@router.get(
    "/{user_id}/role-assignments",
    response_model=UserRoleAssignmentsResponse,
    summary="Get a user's role assignments",
    description=(
        "The user's base role, additional roles with where each applies, whether "
        "the user is protected, and the roles the caller may grant them."
    ),
)
async def get_role_assignments(
    user_id: UUID,
    user: CurrentActiveUser,
    db: DbSession,
) -> UserRoleAssignmentsResponse:
    try:
        return await get_role_assignments_service(db, await load_caller(db, user), user_id=user_id)
    except RoleAssignmentError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.get(
    "/{user_id}/access",
    response_model=UserAccessMap,
    summary="Get what a user can do, and where",
    description=(
        "The user's permissions grouped by place (home, selected organizations, "
        "all customer organizations, Global) with the role each comes through."
    ),
)
async def get_user_access(
    user_id: UUID,
    user: CurrentActiveUser,
    db: DbSession,
) -> UserAccessMap:
    try:
        return await get_user_access_map_service(db, await load_caller(db, user), user_id=user_id)
    except RoleAssignmentError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.put(
    "/{user_id}/role-assignments",
    response_model=UserRoleAssignmentsResponse,
    summary="Replace a user's role assignments",
    description="Replace the user's base role and additional roles (with boundaries) atomically.",
)
async def replace_role_assignments(
    user_id: UUID,
    request: UserRoleAssignmentsUpdate,
    user: CurrentActiveUser,
    db: DbSession,
) -> UserRoleAssignmentsResponse:
    try:
        return await replace_role_assignments_service(
            db, await load_caller(db, user), user_id=user_id, request=request
        )
    except RoleAssignmentError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.post(
    "/{user_id}/access/check",
    response_model=AccessTrace,
    summary="Test a user's access",
    description=(
        "What the managed-identity model decides for this user performing an access-list operation "
        "in an organization (or Global), directly or through a workflow. Report-only; nothing is "
        "recorded."
    ),
)
async def check_user_access(
    user_id: UUID,
    body: AccessCheckRequest,
    user: CurrentActiveUser,
    db: DbSession,
) -> AccessTrace:
    operation = "POST /api/users/{user_id}/access/check"
    caller = await load_caller(db, user)
    reach = operation_reach(caller, operation)
    subject = await db.get(UserORM, user_id)
    if subject is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    require_operation(caller, operation, org_target(subject.organization_id))
    entry = entry_for_key(body.operation)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown operation {body.operation!r}: use an access-list catalog id or \"METHOD /api/path\"",
        )
    target = body.organization_id if isinstance(body.organization_id, UUID) else None
    if target is not None and await db.get(OrganizationORM, target) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    if not reach.covers(target):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only test access in organizations you reach",
        )
    if body.workflow_id is not None:
        workflow = await db.get(WorkflowORM, body.workflow_id)
        if workflow is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
        if not reach.covers(workflow.organization_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only test workflows in organizations you reach",
            )
    trace = await test_access(db, subject.id, target, entry, body.workflow_id)
    return AccessTrace.model_validate(trace.as_dict())
