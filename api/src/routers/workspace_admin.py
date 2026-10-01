"""Privileged domain actions, with credential-safe validation errors."""

from typing import Literal
from uuid import UUID

from bifrost.admin_models import (
    ExecutionRedactionResult,
    OAuthDiagnostics,
    OAuthReconciliationResult,
    OAuthRecoveryRequest,
    OAuthRecoveryResult,
    ScopedAdminRequest,
)
from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from src.core.auth import Context
from src.services import workspace_admin as service


class AdminRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                # FastAPI's default error includes rejected input, possibly tokens.
                raise HTTPException(
                    422, "Invalid administrative operation request"
                ) from None
            except service.AdminOperationError as error:
                raise HTTPException(error.status_code, str(error)) from None

        return safe_handler


router = APIRouter(
    prefix="/api", tags=["Privileged administration"], route_class=AdminRoute
)


@router.get(
    "/oauth/connections/{connection_name}/diagnostics", response_model=OAuthDiagnostics
)
async def inspect_oauth(
    connection_name: str, scope: Literal["global"] | UUID, ctx: Context
):
    return await service.inspect_oauth(ctx.db, ctx.user, connection_name, scope)


@router.post(
    "/oauth/connections/{connection_name}/recover", response_model=OAuthRecoveryResult
)
async def recover_oauth(
    connection_name: str, request: OAuthRecoveryRequest, ctx: Context
):
    return await service.recover_oauth(ctx.db, ctx.user, connection_name, request)


@router.post(
    "/oauth/connections/{connection_name}/reconcile",
    response_model=OAuthReconciliationResult,
)
async def reconcile_oauth(
    connection_name: str, request: ScopedAdminRequest, ctx: Context
):
    return await service.reconcile_oauth(
        ctx.db, ctx.user, connection_name, request.scope
    )


@router.post(
    "/executions/{execution_id}/redact-sensitive-fields",
    response_model=ExecutionRedactionResult,
)
async def redact_execution(
    execution_id: UUID, request: ScopedAdminRequest, ctx: Context
):
    return await service.redact_execution(ctx.db, ctx.user, execution_id, request.scope)
