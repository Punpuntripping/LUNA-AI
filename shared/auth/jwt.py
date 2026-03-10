"""
JWT utilities for Supabase Auth tokens.
Decode, verify, and extract user information.
Used by backend auth middleware.

Uses PyJWT (import jwt), NOT python-jose.
Algorithm: HS256, audience: "authenticated".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import jwt  # PyJWT library

from shared.config import get_settings

logger = logging.getLogger(__name__)


# ============================================
# DATA CLASSES
# ============================================

@dataclass
class AuthUser:
    """Authenticated user extracted from JWT."""
    auth_id: str       # Supabase auth.users.id (UUID as string)
    email: str
    role: str          # 'authenticated', 'anon', 'service_role'
    exp: datetime      # Token expiration
    iat: datetime      # Token issued at
    user_metadata: dict  # Custom claims from Supabase Auth

    @property
    def is_expired(self) -> bool:
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

    try:
        payload = jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
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
