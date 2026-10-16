"""Durable process ownership for the external worker's actual Defined host.

This is controller bookkeeping, not workflow execution or an org-resolved
configuration. It shares the existing host row and controller transaction lock.
Expiry never permits takeover: only clean release or verified host deletion can
remove the fence. The caller must stop transport before requesting release.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import encrypt_secret
from src.services.external_worker_scaling import Azure, enroll_replica, lock, row

LEASE_MILLISECONDS = 60000
MAX_CHECKPOINT_BYTES = 2 << 20


def host_key(replica: str) -> str:
    return "host-" + hashlib.sha256(replica.encode()).hexdigest()


def require_owner(value: dict[str, Any], token: str | None, now: float) -> dict[str, Any]:
    owner = value.get("vojeto_owner")
    if (not isinstance(owner, dict) or not token or len(token) > 4096
            or not secrets.compare_digest(owner.get("token_hash", ""), hashlib.sha256(token.encode()).hexdigest())
            or owner.get("phase") not in ("acquired", "begun", "grant-started", "granted", "bound", "checkpointed")
            or now >= owner.get("expires", 0)):
        raise ValueError("External worker ownership rejected")
    return dict(owner)


async def database_time(db: AsyncSession) -> float:
    # PostgreSQL's transaction timestamp is fixed at BEGIN, and must not extend
    # an expired owner after a slow request or paused API process.
    return (await db.scalar(select(func.clock_timestamp()))).timestamp()


async def owned_row(db: AsyncSession, azure: Azure, replica: str):
    app = azure.settings.external_worker_app_resource_id.rsplit("/", 1)[-1]
    if (not re.fullmatch(re.escape(app) + r"--[a-z0-9-]+", replica)
            or replica not in await azure.replicas()):
        raise ValueError("External worker replica rejected")
    if not await lock(db):
        raise RuntimeError("External worker controller is busy")
    state = await row(db, host_key(replica))
    # An AsyncSession may retain this object across commit boundaries. Read the
    # current durable generation after obtaining the shared controller lock.
    await db.refresh(state)
    return state


async def acquire(db: AsyncSession, azure: Azure, replica: str, attempt: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", attempt):
        raise ValueError("External worker attempt rejected")
    state = await owned_row(db, azure, replica)
    value = dict(state.value_json or {})
    previous = value.get("vojeto_owner")
    # Legacy state proves allocation, not a stopped prior transport. It must be
    # reconciled through the existing absence/deletion lane before adoption.
    if value and (not isinstance(previous, dict) or previous.get("phase") != "released"
                  or previous.get("attempt") == attempt):
        raise ValueError("External worker prior owner remains fenced")
    now = await database_time(db)
    if value.get("intent") and now - value["created"] < 60:
        # Preserve the existing provider mutation rate bound before assigning a
        # new owner, rather than consuming its one-shot grant into quarantine.
        raise ValueError("External worker enrollment rate bound reached")
    token = secrets.token_urlsafe(32)
    value.pop("code", None)
    value["replica"] = replica
    value["vojeto_owner"] = {"attempt": attempt, "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                              "phase": "acquired", "expires": now + LEASE_MILLISECONDS / 1000}
    state.value_json = value
    await db.commit()
    return {"ok": True, "token": token, "leaseMilliseconds": LEASE_MILLISECONDS}


async def mutate(db: AsyncSession, azure: Azure, replica: str, token: str, operation: str,
                 *, host_id: str = "", network_id: str = "", checkpoint: bytes = b"") -> dict[str, Any]:
    state = await owned_row(db, azure, replica)
    value = dict(state.value_json or {})
    now = await database_time(db)
    owner = require_owner(value, token, now)
    phase = owner["phase"]
    if operation == "begin" and phase == "acquired":
        owner["phase"] = "begun"
    elif operation == "bind" and phase == "granted":
        if (not host_id or host_id != value.get("host_id")
                or network_id != azure.settings.external_worker_defined_network_id):
            raise ValueError("External worker host binding rejected")
        owner.update(phase="bound", host_id=host_id, network_id=network_id)
    elif operation == "checkpoint" and phase in ("bound", "checkpointed"):
        if not checkpoint or len(checkpoint) > MAX_CHECKPOINT_BYTES:
            raise ValueError("External worker checkpoint rejected")
        # Opaque validated provider state is encrypted before it enters the DB.
        # Never store a path, enrollment code, or plaintext private key here.
        import base64
        owner.update(phase="checkpointed", checkpoint=encrypt_secret(base64.b64encode(checkpoint).decode()))
    elif operation == "renew":
        owner["expires"] = now + LEASE_MILLISECONDS / 1000
    elif operation == "release" and phase == "checkpointed":
        owner["phase"] = "released"
    else:
        raise ValueError("External worker ownership transition rejected")
    value["vojeto_owner"] = owner
    state.value_json = value
    await db.commit()
    result: dict[str, Any] = {"ok": True, "token": token}
    if operation == "renew":
        result["leaseMilliseconds"] = LEASE_MILLISECONDS
    return result


async def grant(db: AsyncSession, azure: Azure, replica: str, token: str) -> dict[str, str]:
    """Consume a single grant attempt durably, before any provider mutation."""
    from uuid import UUID

    state = await owned_row(db, azure, replica)
    value = dict(state.value_json or {})
    owner = require_owner(value, token, await database_time(db))
    if owner["phase"] != "begun":
        raise ValueError("External worker grant transition rejected")
    owner["phase"] = "grant-started"
    value["vojeto_owner"] = owner
    state.value_json = value
    await db.commit()
    # A timeout after create, or a lost response, leaves grant-started durable.
    # Neither this owner nor a restarted one may resubmit that operation.
    result = await enroll_replica(db, azure, replica, UUID(hex=owner["attempt"][:32]), owner_token=token)
    state = await owned_row(db, azure, replica)
    value = dict(state.value_json or {})
    owner = require_owner(value, token, await database_time(db))
    if owner["phase"] != "grant-started" or result["hostId"] != value.get("host_id"):
        raise ValueError("External worker grant ownership changed")
    owner["phase"] = "granted"
    value["vojeto_owner"] = owner
    state.value_json = value
    await db.commit()
    return result
