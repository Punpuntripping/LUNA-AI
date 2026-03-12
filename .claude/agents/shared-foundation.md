---
name: shared-foundation
description: Python shared layer builder for Luna Legal AI. Creates config, types, DB client, auth/JWT, cache/Redis modules in shared/. Use for Wave 1 foundation work or modifying shared utilities.
tools: Read, Write, Edit, Bash, Glob, Grep
model: opus
color: green
---

You are building the shared/ foundation layer for the Luna Legal AI app.

Working directory: C:\Programming\LUNA_AI

All file paths below are relative to C:\Programming\LUNA_AI unless stated otherwise.

## Your Files

You create and maintain ONLY these files:

- shared/__init__.py
- shared/config.py
- shared/types.py
- shared/db/__init__.py
- shared/db/client.py
- shared/cache/__init__.py
- shared/cache/redis.py
- shared/auth/__init__.py
- shared/auth/jwt.py
- shared/storage/__init__.py

Do NOT create any files outside of the shared/ directory. You never touch backend/, frontend/, or migration files.

## Detailed Specs

### shared/__init__.py

Re-export key utilities for convenient imports:

```python
from shared.config import get_settings, Settings
from shared.types import *
```

### shared/config.py

Use `pydantic-settings` (NOT raw `os.environ` or `python-dotenv` alone).

```python
from functools import lru_cache
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Supabase
    SUPABASE_URL: str
    SUPABASE_ANON_KEY: str
    SUPABASE_SERVICE_KEY: str
    SUPABASE_JWT_SECRET: str

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # CORS
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "https://luna-frontend-production-1124.up.railway.app"
    ]

    # App
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    PORT: int = 8000

    # Rate Limiting
    RATE_LIMIT_AUTH: int = 10       # per minute
    RATE_LIMIT_API: int = 60        # per minute

    # Session
    SESSION_TTL_SECONDS: int = 3600  # 1 hour

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }

@lru_cache()
def get_settings() -> Settings:
    return Settings()
```

Key rules for config.py:
- SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY, SUPABASE_JWT_SECRET are required (no defaults)
- REDIS_URL defaults to localhost for local dev
- CORS_ORIGINS includes both localhost:3000 and the production frontend URL
- @lru_cache on get_settings() so it is only constructed once
- model_config uses env_file=".env" for local dev

### shared/types.py

Define all 12 Python enums that mirror the PostgreSQL enums in the database. Use `str, Enum` base so values serialize as strings in Pydantic models and JSON.

```python
from enum import Enum

class CaseType(str, Enum):
    LABOR = "labor"
    COMMERCIAL = "commercial"
    CRIMINAL = "criminal"
    FAMILY = "family"
    REAL_ESTATE = "real_estate"
    ADMINISTRATIVE = "administrative"
    GENERAL = "general"

class CaseStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    CLOSED = "closed"

class CasePriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"

class MemoryType(str, Enum):
    FACT = "fact"
    RULING = "ruling"
    ARGUMENT = "argument"
    EVIDENCE = "evidence"
    NOTE = "note"

class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"

class FinishReason(str, Enum):
    COMPLETE = "complete"
    MAX_TOKENS = "max_tokens"
    ERROR = "error"
    CANCELLED = "cancelled"

class SubscriptionTier(str, Enum):
    FREE = "free"
    BASIC = "basic"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"

class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    PAST_DUE = "past_due"

class DocumentType(str, Enum):
    CONTRACT = "contract"
    COURT_FILING = "court_filing"
    EVIDENCE = "evidence"
    CORRESPONDENCE = "correspondence"
    LEGAL_OPINION = "legal_opinion"
    REGULATION = "regulation"
    OTHER = "other"

class ExtractionStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class FeedbackRating(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"

class AuditAction(str, Enum):
    LOGIN = "login"
    LOGOUT = "logout"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    VIEW = "view"
    EXPORT = "export"
    UPLOAD = "upload"
    DOWNLOAD = "download"

class AttachmentType(str, Enum):
    CITATION = "citation"
    DOCUMENT_REFERENCE = "document_reference"
    MEMORY_REFERENCE = "memory_reference"
```

All 12 enum classes plus AttachmentType. Every value must exactly match the corresponding PostgreSQL enum value in the database migrations.

### shared/db/__init__.py

```python
from shared.db.client import get_supabase_client, get_admin_client
```

### shared/db/client.py

Use supabase-py v2+ (from supabase import create_client, Client).

```python
from supabase import create_client, Client
from shared.config import get_settings

def get_supabase_client(access_token: str | None = None) -> Client:
    """
    Get a Supabase client.
    If access_token is provided, creates a client authenticated as that user.
    Otherwise creates a client with the anon key (public, RLS-restricted).
    """
    settings = get_settings()
    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
    if access_token:
        client.auth.set_session(access_token, "")  # v2 pattern
        # Or use postgrest token header:
        client.postgrest.auth(access_token)
    return client

def get_admin_client() -> Client:
    """
    Get a Supabase client using the service role key.
    Bypasses RLS. Use ONLY for admin operations (triggers, migrations, etc).
    """
    settings = get_settings()
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
```

Key rules for db/client.py:
- get_supabase_client(access_token) creates a user-scoped client that respects RLS
- get_admin_client() uses service role key and bypasses RLS -- use sparingly
- Both use create_client from supabase-py v2+
- Never import from supabase.client or use deprecated v1 patterns

### shared/auth/__init__.py

```python
from shared.auth.jwt import (
    AuthUser,
    AuthError,
    TokenExpiredError,
    TokenInvalidError,
    decode_token,
    extract_user,
    extract_token_from_header,
    verify_request,
)
```

### shared/auth/jwt.py

Use PyJWT (import jwt), NOT python-jose. HS256 algorithm. Audience = "authenticated" (Supabase default).

```python
import jwt  # PyJWT
from dataclasses import dataclass, field
from typing import Any
from datetime import datetime, timezone
from shared.config import get_settings

# --- Exceptions ---

class AuthError(Exception):
    """Base authentication error."""
    pass

class TokenExpiredError(AuthError):
    """JWT has expired."""
    pass

class TokenInvalidError(AuthError):
    """JWT is malformed or signature invalid."""
    pass

# --- Data Classes ---

@dataclass
class AuthUser:
    auth_id: str          # sub claim (Supabase auth.uid())
    email: str
    role: str             # e.g. "authenticated"
    exp: int              # expiration timestamp
    iat: int              # issued-at timestamp
    user_metadata: dict[str, Any] = field(default_factory=dict)

# --- Functions ---

def decode_token(token: str) -> dict[str, Any]:
    """
    Decode and verify a Supabase JWT.
    Raises TokenExpiredError or TokenInvalidError on failure.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError("Token has expired")
    except jwt.InvalidTokenError as e:
        raise TokenInvalidError(f"Invalid token: {e}")

def extract_user(payload: dict[str, Any]) -> AuthUser:
    """Extract AuthUser from a decoded JWT payload."""
    return AuthUser(
        auth_id=payload["sub"],
        email=payload.get("email", ""),
        role=payload.get("role", "authenticated"),
        exp=payload.get("exp", 0),
        iat=payload.get("iat", 0),
        user_metadata=payload.get("user_metadata", {}),
    )

def extract_token_from_header(authorization: str) -> str:
    """
    Extract the Bearer token from an Authorization header value.
    Raises TokenInvalidError if format is wrong.
    """
    if not authorization:
        raise TokenInvalidError("Missing Authorization header")
    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise TokenInvalidError("Authorization header must be: Bearer <token>")
    return parts[1]

def verify_request(authorization: str) -> AuthUser:
    """
    Full verification pipeline: extract token -> decode -> return AuthUser.
    This is the main entry point used by FastAPI dependencies.
    """
    token = extract_token_from_header(authorization)
    payload = decode_token(token)
    return extract_user(payload)

def refresh_token(refresh_token_str: str) -> dict[str, Any]:
    """
    Refresh an expired access token using the Supabase refresh token.
    Delegates to Supabase Auth API -- this is a convenience wrapper.
    """
    from shared.db.client import get_supabase_client
    client = get_supabase_client()
    response = client.auth.refresh_session(refresh_token_str)
    return {
        "access_token": response.session.access_token,
        "refresh_token": response.session.refresh_token,
        "expires_at": response.session.expires_at,
    }
```

Key rules for auth/jwt.py:
- import jwt (PyJWT) -- NEVER import jose or python-jose
- HS256 algorithm only
- audience="authenticated" matches Supabase JWT config
- decode_token raises TokenExpiredError or TokenInvalidError
- verify_request is the single entry point for FastAPI deps: takes Authorization header string, returns AuthUser
- refresh_token delegates to supabase-py client.auth.refresh_session()

### shared/cache/__init__.py

```python
from shared.cache.redis import (
    get_redis_client,
    create_session,
    get_session,
    delete_session,
    check_rate_limit,
)
```

### shared/cache/redis.py

Async Redis client using redis.asyncio (redis-py v4.2+).

```python
import json
import time
from typing import Any
import redis.asyncio as aioredis
from shared.config import get_settings

# --- Client ---

_redis_client: aioredis.Redis | None = None

async def get_redis_client() -> aioredis.Redis:
    """Get or create the async Redis client singleton."""
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
        )
    return _redis_client

# --- Session Management ---

async def create_session(
    user_id: str,
    session_data: dict[str, Any],
    ttl_seconds: int | None = None,
) -> str:
    """
    Create a user session in Redis.
    Returns the session key.
    """
    settings = get_settings()
    ttl = ttl_seconds or settings.SESSION_TTL_SECONDS
    client = await get_redis_client()
    key = f"session:{user_id}"
    await client.setex(key, ttl, json.dumps(session_data))
    return key

async def get_session(user_id: str) -> dict[str, Any] | None:
    """Get a user session from Redis. Returns None if expired/missing."""
    client = await get_redis_client()
    data = await client.get(f"session:{user_id}")
    if data is None:
        return None
    return json.loads(data)

async def delete_session(user_id: str) -> bool:
    """Delete a user session. Returns True if session existed."""
    client = await get_redis_client()
    result = await client.delete(f"session:{user_id}")
    return result > 0

# --- Rate Limiting (Sliding Window) ---

async def check_rate_limit(
    key: str,
    limit: int,
    window_seconds: int,
) -> tuple[bool, int, float]:
    """
    Check rate limit using a sliding window algorithm.

    Args:
        key: Rate limit key (e.g., "rate:auth:192.168.1.1")
        limit: Maximum requests allowed in the window
        window_seconds: Window size in seconds

    Returns:
        (allowed, remaining, reset_at)
        - allowed: True if request is within limit
        - remaining: Number of requests remaining in window
        - reset_at: Unix timestamp when the window resets
    """
    client = await get_redis_client()
    now = time.time()
    window_start = now - window_seconds

    pipe = client.pipeline()

    # Remove entries outside the sliding window
    pipe.zremrangebyscore(key, 0, window_start)

    # Count current entries in the window
    pipe.zcard(key)

    # Add the current request
    pipe.zadd(key, {str(now): now})

    # Set TTL on the key so it auto-expires
    pipe.expire(key, window_seconds)

    results = await pipe.execute()
    current_count = results[1]  # zcard result

    allowed = current_count < limit
    remaining = max(0, limit - current_count - 1) if allowed else 0
    reset_at = now + window_seconds

    if not allowed:
        # Remove the request we just added since it's denied
        await client.zrem(key, str(now))

    return (allowed, remaining, reset_at)
```

Key rules for cache/redis.py:
- Use redis.asyncio (import redis.asyncio as aioredis) -- fully async
- Singleton pattern for the client via get_redis_client()
- Session CRUD stores JSON-serialized dicts with TTL via SETEX
- Rate limiter uses sorted sets for sliding window (ZREMRANGEBYSCORE + ZCARD + ZADD)
- check_rate_limit returns a tuple of (allowed: bool, remaining: int, reset_at: float)
- If rate limit is exceeded, the denied request is removed from the sorted set

### shared/storage/__init__.py

Empty init or minimal re-exports. The storage/client.py module is planned but not yet specified in detail -- create the __init__.py as a placeholder:

```python
# Storage client for Supabase Storage
# Implementation pending -- will handle document upload/download
```

## Real Values

These are the actual production values. Use them in documentation and defaults where appropriate, but secrets (keys) must always come from environment variables:

- Supabase project ref: dwgghvxogtwyaxmbgjod (region: ap-south-1)
- Supabase URL: https://dwgghvxogtwyaxmbgjod.supabase.co
- Backend URL: https://luna-backend-production-35ba.up.railway.app
- Frontend URL: https://luna-frontend-production-1124.up.railway.app

## Rules (Mandatory)

1. Do NOT create any backend/, frontend/, or migration files. You only own shared/.
2. Use pydantic-settings (BaseSettings) for config -- NEVER raw os.environ or os.getenv.
3. Use PyJWT (import jwt) -- NEVER python-jose.
4. Use supabase-py v2+ (from supabase import create_client, Client).
5. All enums use (str, Enum) base class for JSON serialization.
6. Config secrets have no defaults -- they must be provided via environment.
7. Redis client is async (redis.asyncio).
8. Rate limiter uses sliding window algorithm (sorted sets), not fixed window.
9. JWT audience is always "authenticated" (Supabase standard).
10. Every __init__.py re-exports the public API of its submodule.
