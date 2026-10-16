"""Provision/revoke a finite router lease using the shared platform-job host.

The provision job completes after local lighthouse startup. It does not claim
end-to-end readiness or hold a scheduler slot for the lifetime of the tunnel.
The launcher owns the absolute deadline and independently supervises cleanup.
"""

from uuid import UUID

from src.jobs.execution_policy import WorkloadClass, WorkerLossBehavior, platform_job_operations_policy
from src.jobs.platform.base import PlatformJobContext, PlatformJobDefinition, PlatformJobFailure, PlatformJobPolicy
from src.models.contracts.device_peer_sessions import DevicePeerRevokePayload, DevicePeerSessionPayload


async def _authorize(context: PlatformJobContext, device_id: UUID, *, active: bool) -> None:
    from src.core.database import get_db_context
    from src.core.principal import UserPrincipal
    from src.repositories.users import UserRepository
    from src.services.devices import get_device_scoped, require_device_execute_permission

    async with get_db_context() as db:
        actor = await UserRepository(db).get_by_id(UUID(context.requested_by_user_id))
        if actor is None or not actor.is_active:
            raise PlatformJobFailure("peer_permission_denied", "Device session permission was revoked")
        principal = UserPrincipal(user_id=actor.id, email=actor.email, organization_id=actor.organization_id,
                                  is_superuser=actor.is_superuser, is_active=actor.is_active)
        await require_device_execute_permission(db, principal)
        device = await get_device_scoped(db, principal, device_id)
        if str(device.organization_id) != str(context.organization_id) or active and device.status != "active":
            raise PlatformJobFailure("peer_permission_denied", "Device session permission was revoked")


async def start_peer_session(context: PlatformJobContext, payload: DevicePeerSessionPayload) -> dict:
    from datetime import datetime, timezone
    from src.config import get_settings
    from src.services.device_peer_launcher import PeerLauncherClient, PeerLauncherError

    await _authorize(context, payload.device_id, active=True)
    remaining = (payload.expires - datetime.now(timezone.utc)).total_seconds()
    if not 1 <= remaining <= 1800:
        raise PlatformJobFailure("peer_expired", "Device session deadline expired")
    await context.report("Starting session lighthouse", percent=10)
    async with PeerLauncherClient(get_settings()) as client:
        try:
            result = await client.start(context.job_id.hex, payload)
            await context.report("Lighthouse started; endpoint connectivity unverified", percent=100)
            return result
        except BaseException:
            # A timeout may occur after the router started. Revoking the known
            # nonce also fences a create RPC that arrives after cancellation.
            try:
                await client.stop(context.job_id.hex)
            except PeerLauncherError:
                await context.log("warning", "peer_cleanup_pending", "Lighthouse revocation could not be confirmed; absolute expiry remains enforced")
            raise


async def revoke_peer_session(context: PlatformJobContext, payload: DevicePeerRevokePayload) -> dict:
    from src.config import get_settings
    from src.services.device_peer_launcher import PeerLauncherClient

    # Revocation is allowed after device disablement. The HTTP admission already
    # checked ownership; this rechecks current user and device scope.
    await _authorize(context, payload.device_id, active=False)
    async with PeerLauncherClient(get_settings()) as client:
        await client.stop(payload.session_job_id.hex)
    await context.report("Lighthouse stopped; endpoint disconnect unconfirmed", percent=100)
    return {"session_job_id": str(payload.session_job_id), "lighthouse_revoked": True,
            "endpoint_disconnect_confirmed": False}


def _definition(name, model, handler):
    return PlatformJobDefinition(
        job_type=name, payload_version=1, payload_model=model, handler=handler,
        policy=PlatformJobPolicy(timeout_seconds=60, max_attempts=1, retry_on_runner_loss=False,
                                 max_concurrency=4, min_memory_headroom_mb=64),
        operations_policy=platform_job_operations_policy(name, workload_class=WorkloadClass.PLATFORM_INTERACTIVE,
                                                         worker_loss=WorkerLossBehavior.FAIL),
    )


DEVICE_PEER_START_DEFINITION = _definition("device.peer_lighthouse.start", DevicePeerSessionPayload, start_peer_session)
DEVICE_PEER_REVOKE_DEFINITION = _definition("device.peer_lighthouse.revoke", DevicePeerRevokePayload, revoke_peer_session)
