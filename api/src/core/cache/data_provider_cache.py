"""
Redis-backed cache for data provider results.

Replaces the in-memory cache with Redis for:
- Shared cache across multiple containers (horizontal scaling)
- Durability across restarts
- TTL-based expiration
- Stampede protection via SETNX locks

Follows the same patterns as other cache modules in shared/cache/.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.core.cache.redis_client import CacheError, get_redis

logger = logging.getLogger(__name__)

# Default TTL for data provider cache (5 minutes)
TTL_DATA_PROVIDER = 300

# Lock TTL for stampede protection (10 seconds)
TTL_LOCK = 10


@dataclass(frozen=True)
class DataProviderCacheIdentity:
    """Authorization-bound identity for one data provider cache namespace."""

    organization_id: str | None
    provider_id: str
    principal_id: str

    def __post_init__(self) -> None:
        if not self.provider_id:
            raise ValueError("Data provider cache identity requires provider_id")
        if not self.principal_id:
            raise ValueError("Data provider cache identity requires principal_id")


def _get_scope(org_id: str | None) -> str:
    """Get the scope prefix for a key."""
    if org_id and org_id != "GLOBAL":
        return f"org:{org_id}"
    return "global"


def data_provider_cache_key(
    identity: DataProviderCacheIdentity,
    param_hash: str,
) -> str:
    """
    Key for a cached data provider result.

    Pattern: bifrost:{scope}:dp:{provider_id}:principal:{principal_id}:{param_hash}

    Args:
        identity: Immutable provider, tenant, and effective-principal identity
        param_hash: Hash of parameters

    Returns:
        Redis key string
    """
    scope = _get_scope(identity.organization_id)
    return (
        f"bifrost:{scope}:dp:{identity.provider_id}:"
        f"principal:{identity.principal_id}:{param_hash}"
    )


def data_provider_lock_key(
    identity: DataProviderCacheIdentity,
    param_hash: str,
) -> str:
    """
    Key for stampede protection lock.

    Pattern: bifrost:{scope}:dp:{provider_id}:principal:{principal_id}:{param_hash}:lock
    """
    return f"{data_provider_cache_key(identity, param_hash)}:lock"


def compute_param_hash(parameters: dict[str, Any] | None) -> str:
    """
    Compute deterministic hash of parameters.

    Args:
        parameters: Input parameters dict

    Returns:
        16-character hex hash
    """
    if not parameters:
        return "empty"

    # Sort keys for deterministic hash
    param_str = json.dumps(parameters, sort_keys=True, default=str)
    return hashlib.sha256(param_str.encode()).hexdigest()[:16]


async def get_cached_result(
    identity: DataProviderCacheIdentity,
    parameters: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """
    Get cached data provider result from Redis.

    Args:
        identity: Immutable provider, tenant, and effective-principal identity
        parameters: Input parameters

    Returns:
        Cached entry with 'data' and 'expires_at' keys, or None if not cached
    """
    param_hash = compute_param_hash(parameters)
    cache_key = data_provider_cache_key(identity, param_hash)

    try:
        async with get_redis() as r:
            cached_json = await r.get(cache_key)

            if cached_json is None:
                logger.debug(
                    "Cache miss for data provider: %s",
                    identity.provider_id,
                )
                return None

            cached_entry = json.loads(cached_json)

            # Parse expires_at and check if still valid
            expires_at_str = cached_entry.get("expires_at")
            if expires_at_str:
                expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) >= expires_at:
                    # Expired - Redis TTL should have handled this, but be safe
                    logger.debug(
                        "Cache expired for data provider: %s",
                        identity.provider_id,
                    )
                    await r.delete(cache_key)
                    return None

            logger.info("Cache hit for data provider: %s", identity.provider_id)
            return cached_entry

    except CacheError as e:
        # Log but don't fail - cache is optional
        logger.warning("Cache read failed for %s: %s", identity.provider_id, e)
        return None
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Cache data invalid for %s: %s", identity.provider_id, e)
        return None


async def cache_result(
    identity: DataProviderCacheIdentity,
    parameters: dict[str, Any] | None,
    result: Any,
    ttl_seconds: int = TTL_DATA_PROVIDER,
) -> datetime:
    """
    Cache a data provider result in Redis.

    Args:
        identity: Immutable provider, tenant, and effective-principal identity
        parameters: Input parameters
        result: Result to cache
        ttl_seconds: Time to live in seconds

    Returns:
        Expiration datetime
    """
    param_hash = compute_param_hash(parameters)
    cache_key = data_provider_cache_key(identity, param_hash)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

    cache_entry = {
        "data": result,
        "expires_at": expires_at.isoformat()
    }

    try:
        async with get_redis() as r:
            # Use SETEX for atomic set with TTL
            await r.setex(
                cache_key,
                ttl_seconds,
                json.dumps(cache_entry, default=str)
            )
            logger.info(
                "Cached data provider result: %s (TTL: %ss)",
                identity.provider_id,
                ttl_seconds,
            )

    except CacheError as e:
        # Log but don't fail - cache is optional
        logger.warning("Cache write failed for %s: %s", identity.provider_id, e)

    return expires_at


async def invalidate_data_provider(
    identity: DataProviderCacheIdentity,
    parameters: dict[str, Any] | None = None,
) -> None:
    """
    Invalidate cached data provider result.

    If parameters is None, invalidates all cached results for that data provider.

    Args:
        identity: Immutable provider, tenant, and effective-principal identity
        parameters: Optional specific parameters to invalidate
    """
    try:
        async with get_redis() as r:
            if parameters is not None:
                # Invalidate specific result
                param_hash = compute_param_hash(parameters)
                cache_key = data_provider_cache_key(identity, param_hash)
                deleted = await r.delete(cache_key)
                if deleted:
                    logger.info(
                        "Invalidated cached data provider: %s",
                        identity.provider_id,
                    )
            else:
                # Invalidate all results for this data provider using pattern
                scope = _get_scope(identity.organization_id)
                pattern = (
                    f"bifrost:{scope}:dp:{identity.provider_id}:"
                    f"principal:{identity.principal_id}:*"
                )
                cursor = 0
                deleted_count = 0

                while True:
                    cursor, keys = await r.scan(cursor, match=pattern, count=100)
                    if keys:
                        # Filter out lock keys
                        data_keys = [k for k in keys if not k.endswith(":lock")]
                        if data_keys:
                            deleted_count += await r.delete(*data_keys)

                    if cursor == 0:
                        break

                if deleted_count:
                    logger.info(
                        "Invalidated %s cached results for: %s",
                        deleted_count,
                        identity.provider_id,
                    )

    except CacheError as e:
        # Best effort - log and continue
        logger.warning(
            "Cache invalidation failed for %s: %s",
            identity.provider_id,
            e,
        )


async def acquire_compute_lock(
    identity: DataProviderCacheIdentity,
    parameters: dict[str, Any] | None,
    lock_ttl: int = TTL_LOCK,
) -> bool:
    """
    Acquire a lock to prevent cache stampede.

    Use this when multiple concurrent requests might compute the same data provider.
    Only the first request acquires the lock and computes; others wait or skip.

    Args:
        identity: Immutable provider, tenant, and effective-principal identity
        parameters: Input parameters
        lock_ttl: Lock TTL in seconds

    Returns:
        True if lock acquired, False if already locked
    """
    param_hash = compute_param_hash(parameters)
    lock_key = data_provider_lock_key(identity, param_hash)

    try:
        async with get_redis() as r:
            # SETNX returns True if key was set (lock acquired)
            acquired = await r.set(lock_key, "1", nx=True, ex=lock_ttl)
            return bool(acquired)

    except CacheError as e:
        logger.warning(
            "Lock acquisition failed for %s: %s",
            identity.provider_id,
            e,
        )
        # On error, allow computation (no lock protection)
        return True


async def release_compute_lock(
    identity: DataProviderCacheIdentity,
    parameters: dict[str, Any] | None,
) -> None:
    """
    Release the compute lock after caching result.

    Args:
        identity: Immutable provider, tenant, and effective-principal identity
        parameters: Input parameters
    """
    param_hash = compute_param_hash(parameters)
    lock_key = data_provider_lock_key(identity, param_hash)

    try:
        async with get_redis() as r:
            await r.delete(lock_key)

    except CacheError as e:
        # Lock will expire on its own - log and continue
        logger.warning(
            "Lock release failed for %s: %s",
            identity.provider_id,
            e,
        )
