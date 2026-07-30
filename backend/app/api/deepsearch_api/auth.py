"""Service-key auth for the Blog-Post Generation API.

Mirrors ``backend.app.api.internal_webhooks._verify_webhook_secret`` semantics
(fail-closed when the secret is unset) but reads the key from
``Authorization: Bearer <key>`` — marketing's chosen transport — via
``HTTPBearer(auto_error=False)`` so we can return an Arabic 401 envelope
instead of FastAPI's default 403/plain response.

The service key is THE security boundary for this surface (the rate limiter is
a fail-open cost cap, not a security control). Therefore:

* ``EDITORIAL_SERVICE_KEY`` unset  → 401 (fail-closed — refuse rather than
  accidentally run open in prod without a key).
* missing / malformed / mismatched credentials → 401.

Comparison is constant-time (``hmac.compare_digest``) to avoid leaking the key
length/prefix via timing.
"""
from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.app.errors import ErrorCode, LunaHTTPException
from shared.config import get_settings

# auto_error=False → return our own Arabic 401 rather than the library's 403.
_bearer_scheme = HTTPBearer(auto_error=False)

# Arabic error strings (Absolute Rule #5).
_MSG_NOT_CONFIGURED = "لم تُهيأ مصادقة التحرير"
_MSG_INVALID_KEY = "مفتاح مصادقة التحرير غير صالح"


def _verify_service_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> None:
    """FastAPI ``Depends`` guard. Raises 401 (Arabic) unless the presented
    Bearer key exactly matches ``EDITORIAL_SERVICE_KEY``.

    Fail-closed: if the key is unset in settings, EVERY call is rejected — the
    endpoint is effectively closed until an operator provisions the key.
    """
    expected = (get_settings().EDITORIAL_SERVICE_KEY or "").strip()
    if not expected:
        raise LunaHTTPException(
            status_code=401,
            code=ErrorCode.AUTH_INVALID,
            detail=_MSG_NOT_CONFIGURED,
            headers={"WWW-Authenticate": "Bearer"},
        )

    supplied = (credentials.credentials if credentials is not None else "").strip()
    # Compare on bytes: ``compare_digest`` raises TypeError on non-ASCII str,
    # and this value is attacker-controlled (Authorization header).
    if not supplied or not hmac.compare_digest(
        supplied.encode("utf-8"), expected.encode("utf-8")
    ):
        raise LunaHTTPException(
            status_code=401,
            code=ErrorCode.AUTH_INVALID,
            detail=_MSG_INVALID_KEY,
            headers={"WWW-Authenticate": "Bearer"},
        )


__all__ = ["_verify_service_key"]
