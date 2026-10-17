"""Durable request identity for device peer admission, including terminal jobs."""

from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text

from src.models.orm.platform_jobs import PlatformJob
from src.services.platform_jobs import enqueue_platform_job


async def enqueue_peer_session(db, definition, payload, *, dedupe_key: str, **kwargs):
    # The shared scheduler deduplicates active jobs. A session request also needs
    # to retain its identity after provisioning finishes, while its lease lives.
    identity = uuid5(NAMESPACE_URL, "bifrost:device-peer:" + dedupe_key)
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:identity))"),
        {"identity": "bifrost:device-peer:" + dedupe_key},
    )
    existing = await db.get(PlatformJob, identity)
    if existing is not None:
        return existing, True
    return await enqueue_platform_job(
        db, definition, payload, dedupe_key=dedupe_key, job_id=identity, **kwargs
    )
