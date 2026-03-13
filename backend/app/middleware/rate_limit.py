"""
Redis sliding-window rate limiter middleware.
Fails open if Redis is unavailable (requests are allowed through).
Sets X-RateLimit-Remaining and X-RateLimit-Reset response headers.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import jwt as pyjwt

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from redis.asyncio import Redis as AsyncRedis

logger = logging.getLogger(__name__)

# Default limits (per IP per window)
DEFAULT_RATE_LIMIT = 60          # requests
DEFAULT_WINDOW_SECONDS = 60      # per minute

# Stricter limits for auth endpoints
AUTH_RATE_LIMIT = 10             # requests
AUTH_WINDOW_SECONDS = 60         # per minute

# Stricter limits for message send (resource-intensive: DB writes + AI pipeline)
MESSAGE_SEND_RATE_LIMIT = 20     # requests
MESSAGE_SEND_WINDOW_SECONDS = 60 # per minute

# Paths that are exempt from rate limiting
EXEMPT_PATHS = {"/api/v1/health", "/docs", "/redoc", "/openapi.json"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Sliding-window rate limiter backed by Redis sorted sets.
    If Redis is unavailable, requests pass through (fail-open).
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip exempt paths
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        # Get Redis from app state
        redis: Optional[AsyncRedis] = getattr(request.app.state, "redis", None)

        if redis is None:
            # Fail open — no rate limiting if Redis is down
            return await call_next(request)

        # Determine limits based on path
        if request.url.path.startswith("/api/v1/auth/"):
            max_requests = AUTH_RATE_LIMIT
            window = AUTH_WINDOW_SECONDS
        elif (
            "/messages" in request.url.path
            and request.method == "POST"
        ):
            max_requests = MESSAGE_SEND_RATE_LIMIT
            window = MESSAGE_SEND_WINDOW_SECONDS
        else:
            max_requests = DEFAULT_RATE_LIMIT
            window = DEFAULT_WINDOW_SECONDS

        # Build identifier — use X-Forwarded-For behind Railway proxy
        forwarded = request.headers.get("x-forwarded-for", "")
        client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")

        rate_key_id = client_ip  # default: IP-based

        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer ") and not request.url.path.startswith("/api/v1/auth/"):
            try:
                # Decode WITHOUT verification — just extract 'sub' for rate limiting.
                # Full verification happens in deps.get_current_user().
                token = auth_header[7:]
                payload = pyjwt.decode(token, options={"verify_signature": False}, algorithms=["HS256", "ES256"])
                user_sub = payload.get("sub")
                if user_sub:
                    rate_key_id = f"user:{user_sub}"
            except Exception:
                pass  # Fall back to IP-based

        key = f"ratelimit:{rate_key_id}:{request.url.path}:{window}"

        try:
            now = time.time()
            window_start = now - window

            pipe = redis.pipeline()
            # Remove entries outside the current window
            pipe.zremrangebyscore(key, 0, window_start)
            # Add current request timestamp
            pipe.zadd(key, {str(now): now})
            # Count requests in window
            pipe.zcard(key)
            # Set key expiry so it auto-cleans
            pipe.expire(key, window)
            results = await pipe.execute()

            current_count = results[2]  # ZCARD result
            remaining = max(0, max_requests - current_count)
            reset_at = int(now + window)

            if current_count > max_requests:
                detail_msg = "تم تجاوز الحد المسموح من الطلبات"
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": {"code": "RATE_LIMITED", "message": detail_msg, "status": 429},
                        "detail": detail_msg,
                    },
                    headers={
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(reset_at),
                        "Retry-After": str(window),
                    },
                )

            # Proceed with request
            response: Response = await call_next(request)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            response.headers["X-RateLimit-Reset"] = str(reset_at)
            return response

        except Exception as e:
            # Fail open — if Redis errors, allow the request
            logger.warning("Rate limiter error (failing open): %s", e)
            return await call_next(request)
