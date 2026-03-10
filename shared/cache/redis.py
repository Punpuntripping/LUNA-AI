"""
Redis client factory and helpers.
Compatible with Upstash Redis (serverless) and standard Redis.
Used for: session management, caching, rate limiting.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from functools import lru_cache
from typing import Any, Optional
from uuid import UUID

import redis
from redis.asyncio import Redis as AsyncRedis

from shared.config import get_settings

logger = logging.getLogger(__name__)

# ============================================
# DEFAULT TTLs
# ============================================
SESSION_TTL = timedelta(hours=24)      # Active conversation session
CACHE_TTL = timedelta(minutes=30)      # General cache
RATE_LIMIT_TTL = timedelta(minutes=1)  # Rate limit window


# ============================================
# CLIENT FACTORY
# ============================================

@lru_cache(maxsize=1)
def get_redis_client() -> redis.Redis:
    """
    Sync Redis client (singleton).
    Works with both standard Redis and Upstash.
    """
    settings = get_settings()

    client = redis.from_url(
        settings.REDIS_URL,
        password=settings.REDIS_PASSWORD or None,
        decode_responses=True,  # Return strings, not bytes
        socket_timeout=5,
        socket_connect_timeout=5,
        retry_on_timeout=True,
        health_check_interval=30,
    )

    # Verify connection
    try:
        client.ping()
        logger.info("Redis sync client connected")
    except redis.ConnectionError as e:
        logger.error(f"Redis connection failed: {e}")
        raise

    return client


@lru_cache(maxsize=1)
def get_async_redis_client() -> AsyncRedis:
    """
    Async Redis client (singleton).
    Use in FastAPI routes and async contexts.
    """
    settings = get_settings()

    client = AsyncRedis.from_url(
        settings.REDIS_URL,
        password=settings.REDIS_PASSWORD or None,
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5,
        retry_on_timeout=True,
        health_check_interval=30,
    )

    logger.info("Redis async client initialized")
    return client


# ============================================
# SESSION HELPERS
# Active conversation message buffer
# Key pattern: session:{user_id}:{conversation_id}
# ============================================

def _session_key(user_id: UUID, conversation_id: UUID) -> str:
    """Build Redis key for a conversation session."""
    return f"session:{user_id}:{conversation_id}"


async def create_session(
    user_id: UUID,
    conversation_id: UUID,
    messages: list[dict],
    ttl: timedelta = SESSION_TTL,
) -> None:
    """
    Create/overwrite a conversation session in Redis.

    Args:
        user_id: The user's ID.
        conversation_id: The conversation's ID.
        messages: List of message dicts [{role, content}, ...].
        ttl: Time-to-live for the session.
    """
    r = get_async_redis_client()
    key = _session_key(user_id, conversation_id)
    await r.set(key, json.dumps(messages, ensure_ascii=False), ex=int(ttl.total_seconds()))
    logger.debug(f"Created session {key} with {len(messages)} messages")


async def get_session(
    user_id: UUID,
    conversation_id: UUID,
) -> Optional[list[dict]]:
    """
    Retrieve active conversation messages from Redis.

    Returns:
        List of message dicts, or None if session expired/missing.
    """
    r = get_async_redis_client()
    key = _session_key(user_id, conversation_id)
    data = await r.get(key)
    if data is None:
        return None
    return json.loads(data)


async def append_session_message(
    user_id: UUID,
    conversation_id: UUID,
    message: dict,
    ttl: timedelta = SESSION_TTL,
) -> None:
    """
    Append a single message to the active session.
    If session doesn't exist, creates it with just this message.
    """
    messages = await get_session(user_id, conversation_id) or []
    messages.append(message)
    await create_session(user_id, conversation_id, messages, ttl)


async def delete_session(user_id: UUID, conversation_id: UUID) -> None:
    """Delete a conversation session (e.g., on end-session)."""
    r = get_async_redis_client()
    key = _session_key(user_id, conversation_id)
    await r.delete(key)
    logger.debug(f"Deleted session {key}")


async def get_active_session_ids(user_id: UUID) -> list[str]:
    """
    Get all active conversation session IDs for a user.
    Uses SCAN to find matching keys (safe for production).
    """
    r = get_async_redis_client()
    pattern = f"session:{user_id}:*"
    keys = []
    async for key in r.scan_iter(match=pattern, count=100):
        # Extract conversation_id from key
        parts = key.split(":")
        if len(parts) == 3:
            keys.append(parts[2])
    return keys


# ============================================
# GENERAL CACHE HELPERS
# Key pattern: cache:{namespace}:{key}
# ============================================

def _cache_key(namespace: str, key: str) -> str:
    """Build Redis key for cached data."""
    return f"cache:{namespace}:{key}"


async def cache_get(namespace: str, key: str) -> Optional[Any]:
    """
    Get a cached value.

    Returns:
        Deserialized value, or None if not cached.
    """
    r = get_async_redis_client()
    data = await r.get(_cache_key(namespace, key))
    if data is None:
        return None
    return json.loads(data)


async def cache_set(
    namespace: str,
    key: str,
    value: Any,
    ttl: timedelta = CACHE_TTL,
) -> None:
    """
    Set a cached value with TTL.

    Args:
        namespace: Cache namespace (e.g., 'cases', 'user_profile').
        key: Cache key within namespace.
        value: Any JSON-serializable value.
        ttl: Time-to-live.
    """
    r = get_async_redis_client()
    await r.set(
        _cache_key(namespace, key),
        json.dumps(value, ensure_ascii=False, default=str),
        ex=int(ttl.total_seconds()),
    )


async def cache_delete(namespace: str, key: str) -> None:
    """Delete a cached value."""
    r = get_async_redis_client()
    await r.delete(_cache_key(namespace, key))


async def cache_invalidate_namespace(namespace: str) -> int:
    """
    Delete all cached values in a namespace.
    Returns count of deleted keys.
    """
    r = get_async_redis_client()
    pattern = f"cache:{namespace}:*"
    count = 0
    async for key in r.scan_iter(match=pattern, count=100):
        await r.delete(key)
        count += 1
    return count


# ============================================
# RATE LIMITING HELPERS
# Sliding window counter using Redis INCR + EXPIRE
# Key pattern: ratelimit:{identifier}:{window}
# ============================================

async def check_rate_limit(
    identifier: str,
    max_requests: int,
    window: timedelta = RATE_LIMIT_TTL,
) -> tuple[bool, int]:
    """
    Check and increment rate limit counter (sliding window).

    Args:
        identifier: Unique identifier (e.g., user_id, ip_address).
        max_requests: Maximum allowed requests in the window.
        window: Time window for the limit.

    Returns:
        Tuple of (is_allowed: bool, remaining: int).
    """
    r = get_async_redis_client()
    key = f"ratelimit:{identifier}:{int(window.total_seconds())}"

    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, int(window.total_seconds()))
    results = await pipe.execute()

    current_count = results[0]
    remaining = max(0, max_requests - current_count)
    is_allowed = current_count <= max_requests

    return is_allowed, remaining
