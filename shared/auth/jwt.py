"""
JWT utilities for Supabase Auth tokens.
Decode, verify, and extract user information.
Used by backend auth middleware.

Uses PyJWT (import jwt), NOT python-jose.
Supports both HS256 (JWT secret) and ES256 (JWKS) depending on token header.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import jwt  # PyJWT library
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from shared.config import get_settings

logger = logging.getLogger(__name__)

_JWKS_FETCH_TIMEOUT = 5.0
_JWKS_CACHE_LIFESPAN = 300


class ResilientJWKClient(PyJWKClient):
    """PyJWKClient that keeps last-known-good keys across JWKS outages.

    PyJWT's fetch_data() clears its internal jwk_set_cache on a failed fetch
    (the finally block puts None, and JWKSetCache.put(None) wipes the cache) —
    a single failed refresh otherwise invalidates every known kid. This subclass
    restores the last successful payload so a JWKS blip never 401s valid tokens.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last_good: Optional[dict] = None
        self._lock = threading.Lock()

    def fetch_data(self):
        try:
            data = super().fetch_data()
            with self._lock:
                self._last_good = data
            return data
        except PyJWKClientConnectionError:
            with self._lock:
                last_good = self._last_good
            if last_good is not None:
                logger.warning("JWKS fetch failed — serving last-known-good keyset")
                if self.jwk_set_cache is not None:
                    # Undo PyJWT's None-put and re-arm the TTL.
                    self.jwk_set_cache.put(last_good)
                return last_good
            raise


# JWKS client — resilient singleton, caches keys automatically (5-min TTL).
_jwks_client: Optional[ResilientJWKClient] = None


def _get_jwks_client() -> ResilientJWKClient:
    """Get or create a cached resilient JWKS client for the Supabase project."""
    global _jwks_client
    if _jwks_client is None:
        settings = get_settings()
        jwks_url = f"{settings.SUPABASE_URL}/auth/v1/.well-known/jwks.json"
        # Deliberately NO cache_keys=True: its per-kid lru_cache bypasses
        # refresh-on-failure and caches rotated-out keys forever. The
        # jwk_set_cache (5-min TTL) + _last_good give the same hot-path
        # behavior while keeping forced refresh effective.
        _jwks_client = ResilientJWKClient(
            jwks_url,
            cache_jwk_set=True,
            lifespan=_JWKS_CACHE_LIFESPAN,
            timeout=_JWKS_FETCH_TIMEOUT,
        )
    return _jwks_client


def prewarm_jwks() -> None:
    """Best-effort JWKS pre-fetch. Called from lifespan startup (in a thread)."""
    try:
        keys = _get_jwks_client().get_signing_keys()
        logger.info("JWKS pre-warmed (%d signing keys)", len(keys))
    except Exception as e:  # noqa: BLE001
        logger.warning("JWKS pre-warm failed (will retry lazily): %s", e)


# ============================================
# DATA CLASSES
# ============================================

@dataclass
class AuthUser:
    """Authenticated user extracted from JWT."""
    auth_id: str       # Supabase auth.users.id (UUID as string)
    email: str
    role: str          # 'authenticated', 'anon', 'service_role'
    exp: Optional[datetime] = None  # Token expiration (None when verified via get_user)
    iat: Optional[datetime] = None  # Token issued at (None when verified via get_user)
    user_metadata: dict = None      # Custom claims from Supabase Auth

    def __post_init__(self):
        if self.user_metadata is None:
            self.user_metadata = {}

    @property
    def is_expired(self) -> bool:
        if self.exp is None:
            return False
        return datetime.now(timezone.utc) > self.exp

    @property
    def full_name_ar(self) -> Optional[str]:
        return self.user_metadata.get("full_name_ar")


class AuthError(Exception):
    """Authentication error."""
    def __init__(self, message: str, status_code: int = 401):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class TokenExpiredError(AuthError):
    """JWT has expired."""
    def __init__(self):
        super().__init__("Token has expired", 401)


class TokenInvalidError(AuthError):
    """JWT is invalid or tampered."""
    def __init__(self, detail: str = "Invalid token"):
        super().__init__(detail, 401)


class AuthUnavailableError(AuthError):
    """Auth dependency (JWKS) is unreachable — caller should 503, not 401."""
    def __init__(self, detail: str = "Auth service unavailable"):
        super().__init__(detail, 503)


# ============================================
# TOKEN VERIFICATION
# ============================================

def decode_token(token: str) -> dict:
    """
    Decode and verify a Supabase JWT.

    Args:
        token: The JWT string (from Authorization: Bearer <token>).

    Returns:
        Decoded payload dict.

    Raises:
        TokenExpiredError: If token has expired.
        TokenInvalidError: If token is malformed or signature fails.
    """
    settings = get_settings()
    _ALLOWED_ALGORITHMS = {"HS256", "ES256"}

    header = jwt.get_unverified_header(token)
    alg = header.get("alg", "HS256")

    if alg not in _ALLOWED_ALGORITHMS:
        raise TokenInvalidError(f"Unsupported algorithm: {alg}")

    _decode_options = {
        "verify_exp": True,
        "verify_aud": True,
        "require": ["sub", "email", "role", "exp", "iat"],
    }

    try:
        jwks_client = None
        if alg == "HS256":
            signing_key = settings.SUPABASE_JWT_SECRET
        else:
            # ES256 — fetch public key from Supabase JWKS.
            # A kid-miss already triggers one internal refresh inside PyJWT.
            jwks_client = _get_jwks_client()
            signing_key = jwks_client.get_signing_key_from_jwt(token).key

        try:
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=["HS256", "ES256"],
                audience="authenticated",
                options=_decode_options,
            )
        except jwt.InvalidSignatureError:
            if alg != "ES256" or jwks_client is None:
                raise
            # Key may have rotated under the same kid / stale cache:
            # force ONE refresh and retry once. A second failure propagates.
            logger.warning(
                "ES256 signature failed — forcing JWKS refresh and retrying"
            )
            jwks_client.get_signing_keys(refresh=True)
            signing_key = jwks_client.get_signing_key_from_jwt(token).key
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=["HS256", "ES256"],
                audience="authenticated",
                options=_decode_options,
            )
        return payload

    except PyJWKClientConnectionError as e:
        # JWKS unreachable AND no last-known-good keys (cold start during outage).
        logger.error("JWKS unreachable, no cached keys: %s", e)
        raise AuthUnavailableError()
    except PyJWKClientError as e:
        raise TokenInvalidError(f"Unknown signing key: {e}")
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError()
    except jwt.InvalidAudienceError:
        raise TokenInvalidError("Invalid audience claim")
    except jwt.DecodeError as e:
        raise TokenInvalidError(f"Token decode failed: {e}")
    except jwt.InvalidTokenError as e:
        raise TokenInvalidError(f"Invalid token: {e}")


def extract_user(token: str) -> AuthUser:
    """
    Decode a JWT and return a structured AuthUser.

    Args:
        token: JWT string.

    Returns:
        AuthUser dataclass with extracted fields.
    """
    payload = decode_token(token)

    return AuthUser(
        auth_id=payload["sub"],
        email=payload.get("email", ""),
        role=payload.get("role", "authenticated"),
        exp=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
        iat=datetime.fromtimestamp(payload["iat"], tz=timezone.utc),
        user_metadata=payload.get("user_metadata", {}),
    )


def extract_token_from_header(authorization: str) -> str:
    """
    Extract the JWT from an Authorization header value.

    Args:
        authorization: Full header value, e.g., "Bearer eyJ..."

    Returns:
        Just the token string.

    Raises:
        TokenInvalidError: If header format is wrong.
    """
    if not authorization:
        raise TokenInvalidError("Missing Authorization header")

    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise TokenInvalidError("Authorization header must be: Bearer <token>")

    return parts[1]


def verify_request(authorization: str) -> AuthUser:
    """
    Full verification pipeline: header -> token -> user.
    Convenience function for middleware.

    Args:
        authorization: The full Authorization header value.

    Returns:
        AuthUser with verified identity.
    """
    token = extract_token_from_header(authorization)
    return extract_user(token)
