"""
JWT utilities for Supabase Auth tokens.
Decode, verify, and extract user information.
Used by backend auth middleware.

Uses PyJWT (import jwt), NOT python-jose.
Supports both HS256 (JWT secret) and ES256 (JWKS) depending on token header.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import jwt  # PyJWT library
from jwt import PyJWKClient

from shared.config import get_settings

logger = logging.getLogger(__name__)

# JWKS client — singleton, caches keys automatically
_jwks_client: Optional[PyJWKClient] = None


def _get_jwks_client() -> PyJWKClient:
    """Get or create a cached JWKS client for the Supabase project."""
    global _jwks_client
    if _jwks_client is None:
        settings = get_settings()
        jwks_url = f"{settings.SUPABASE_URL}/auth/v1/.well-known/jwks.json"
        _jwks_client = PyJWKClient(jwks_url, cache_keys=True)
    return _jwks_client


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

    try:
        if alg == "HS256":
            signing_key = settings.SUPABASE_JWT_SECRET
        else:
            # ES256 — fetch public key from Supabase JWKS
            jwks_client = _get_jwks_client()
            signing_key = jwks_client.get_signing_key_from_jwt(token).key

        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["HS256", "ES256"],
            audience="authenticated",
            options={
                "verify_exp": True,
                "verify_aud": True,
                "require": ["sub", "email", "role", "exp", "iat"],
            },
        )
        return payload

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


# ============================================
# TOKEN REFRESH (via Supabase client)
# ============================================

async def refresh_token(refresh_token_str: str) -> dict:
    """
    Refresh an expired access token using the refresh token.

    Args:
        refresh_token_str: The refresh token from the login response.

    Returns:
        Dict with new access_token, refresh_token, expires_at.
    """
    from shared.db.client import get_supabase_client

    client = get_supabase_client()
    try:
        response = client.auth.refresh_session(refresh_token_str)
        session = response.session
        return {
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
            "expires_at": session.expires_at,
            "user": {
                "id": session.user.id,
                "email": session.user.email,
            },
        }
    except Exception as e:
        logger.error(f"Token refresh failed: {e}")
        raise AuthError(f"Token refresh failed: {e}", 401)
